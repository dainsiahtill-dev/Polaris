"""Back-mapping assets for projected experiment projects.

This module treats back-mapping as a first-class artifact instead of an
afterthought. The goal is to preserve enough symbol-level structure to map
manual code changes and runtime evidence back to the originating target cells.

The current projection-time implementation works from the in-memory projection
snapshot so Polaris does not introduce new direct workspace file reads while
generating artifacts. A later runtime refresh worker can upgrade this index with
Tree-sitter-backed slicing once it is wired behind a proper contract boundary.
"""

from __future__ import annotations

import ast
import hashlib
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from polaris.kernelone.fs import KernelFileSystem

    from .models import ProjectionEntry

_DEFAULT_RUNTIME_CFG: dict[str, Any] = {
    "syntax_parser_enabled": True,
    "syntax_parser_provider": "auto",
}


def build_python_back_mapping_index(
    *,
    project_root: str | Path,
    rendered_files: Mapping[str, str],
    projection_entries: Iterable[ProjectionEntry],
) -> dict[str, object]:
    """Create a symbol-level back-mapping index from projected Python files."""

    project_root_path = Path(project_root).resolve()
    return _build_python_back_mapping_index(
        project_root_path=project_root_path,
        content_map=rendered_files,
        projection_entries=projection_entries,
        mapping_strategy="projection_snapshot_ast_symbol_index",
    )


def build_python_back_mapping_index_from_workspace(
    *,
    kernel_fs: KernelFileSystem,
    project_root: str,
    projection_entries: Iterable[ProjectionEntry],
) -> dict[str, object]:
    """Refresh a symbol-level back-mapping index from current workspace files."""

    project_root_path = kernel_fs.resolve_workspace_path(project_root)
    normalized_project_root = str(project_root).replace("\\", "/").rstrip("/")
    content_map: dict[str, str] = {}
    for entry in projection_entries:
        if not entry.path.endswith(".py"):
            continue
        workspace_path = f"{normalized_project_root}/{entry.path}" if normalized_project_root else entry.path
        if not kernel_fs.workspace_exists(workspace_path):
            continue
        content_map[entry.path] = kernel_fs.workspace_read_text(workspace_path, encoding="utf-8")
    return _build_python_back_mapping_index(
        project_root_path=project_root_path,
        content_map=content_map,
        projection_entries=projection_entries,
        mapping_strategy="workspace_refresh_ast_symbol_index",
    )


def _build_python_back_mapping_index(
    *,
    project_root_path: Path,
    content_map: Mapping[str, str],
    projection_entries: Iterable[ProjectionEntry],
    mapping_strategy: str,
) -> dict[str, object]:
    """Create a symbol-level back-mapping index from a projection content map."""

    files: list[dict[str, object]] = []
    lookup_by_symbol: dict[str, list[dict[str, object]]] = {}
    issues: list[dict[str, object]] = []

    for entry in sorted(projection_entries, key=lambda item: item.path):
        if not entry.path.endswith(".py"):
            continue

        absolute_path = (project_root_path / entry.path).resolve()
        file_record: dict[str, object] = {
            "path": entry.path,
            "absolute_path": str(absolute_path),
            "cell_ids": list(entry.cell_ids),
            "description": entry.description,
        }

        content = content_map.get(entry.path)
        if content is None:
            issue: dict[str, object] = {
                "path": entry.path,
                "error": "projection_content_missing",
            }
            file_record["status"] = "missing"
            file_record["symbols"] = []
            files.append(file_record)
            issues.append(issue)
            continue

        symbols = _normalize_symbol_rows(
            rows=_extract_symbol_rows(
                relative_path=entry.path,
                content=content,
            ),
            entry=entry,
        )
        file_record["status"] = "ok"
        file_record["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        file_record["symbol_count"] = len(symbols)
        file_record["symbols"] = symbols
        files.append(file_record)

        for symbol in symbols:
            qualified_name = str(symbol.get("qualified_name") or "").strip()
            if not qualified_name:
                continue
            lookup_by_symbol.setdefault(qualified_name, []).append(
                {
                    "path": entry.path,
                    "kind": symbol.get("kind", "unknown"),
                    "line_start": symbol.get("line_start", 1),
                    "line_end": symbol.get("line_end", symbol.get("line_start", 1)),
                    "cell_ids": list(entry.cell_ids),
                }
            )

    return {
        "project_root": str(project_root_path),
        "language": "python",
        "mapping_strategy": mapping_strategy,
        "runtime_cfg": dict(_DEFAULT_RUNTIME_CFG),
        "files": files,
        "lookup": {
            "by_qualified_name": lookup_by_symbol,
        },
        "issues": issues,
    }


def _extract_symbol_rows(
    *,
    relative_path: str,
    content: str,
) -> list[dict[str, Any]]:
    tree_sitter_rows = _extract_symbol_rows_with_tree_sitter(content, relative_path)
    if tree_sitter_rows:
        return tree_sitter_rows
    return _extract_symbol_rows_with_ast(content, relative_path)


def _extract_symbol_rows_with_tree_sitter(
    content: str,
    relative_path: str,
) -> list[dict[str, Any]]:
    parser = _load_tree_sitter_parser()
    if parser is None:
        return []

    source_bytes = content.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(source_bytes)
    except (TypeError, ValueError):
        return []

    root_node = getattr(tree, "root_node", None)
    if root_node is None:
        return []

    rows: list[dict[str, Any]] = []

    def visit(node: Any, scope: tuple[str, ...]) -> None:
        node_type = str(getattr(node, "type", ""))
        children = list(getattr(node, "children", []) or [])
        if node_type in {"class_definition", "function_definition"}:
            name = _extract_tree_sitter_name(node, source_bytes)
            if name:
                kind = "class" if node_type == "class_definition" else ("method" if scope else "function")
                line_start, line_end = _node_line_bounds(node)
                qualified_name = ".".join((*scope, name)) if scope else name
                rows.append(
                    {
                        "symbol": name,
                        "qualified_name": qualified_name,
                        "kind": kind,
                        "line_start": line_start,
                        "line_end": line_end,
                        "syntax_source": "tree_sitter",
                        "scope": ".".join(scope),
                    }
                )
                next_scope = (*scope, name) if node_type == "class_definition" else scope
            else:
                next_scope = scope
        else:
            next_scope = scope

        for child in children:
            visit(child, next_scope)

    visit(root_node, ())
    return rows


def _load_tree_sitter_parser() -> Any | None:
    for module_name in (
        "tree_sitter_language_pack",
        "tree_sitter_languages",
        "tree_sitter_language_pack.all_languages",
    ):
        try:
            module = import_module(module_name)
        except ImportError:
            continue
        parser_getter = getattr(module, "get_parser", None)
        if not callable(parser_getter):
            continue
        try:
            parser = parser_getter("python")
        except (KeyError, TypeError, ValueError):
            continue
        if parser is not None:
            return parser
    return None


def _extract_tree_sitter_name(node: Any, source_bytes: bytes) -> str:
    for child in list(getattr(node, "children", []) or []):
        if str(getattr(child, "type", "")) != "identifier":
            continue
        try:
            return source_bytes[int(child.start_byte) : int(child.end_byte)].decode("utf-8").strip()
        except (AttributeError, TypeError, UnicodeDecodeError, ValueError):
            return ""
    return ""


def _node_line_bounds(node: Any) -> tuple[int, int]:
    start = getattr(node, "start_point", (0, 0))
    end = getattr(node, "end_point", start)
    line_start = int(start[0]) + 1 if isinstance(start, tuple) and start else 1
    line_end = int(end[0]) + 1 if isinstance(end, tuple) and end else line_start
    return max(1, line_start), max(line_start, line_end)


def _extract_symbol_rows_with_ast(
    content: str,
    relative_path: str,
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(content, filename=relative_path)
    except SyntaxError as exc:
        line_number = int(getattr(exc, "lineno", 1) or 1)
        return [
            {
                "symbol": "parse_error",
                "qualified_name": f"{relative_path}:parse_error",
                "kind": "error",
                "line_start": line_number,
                "line_end": line_number,
                "signature": str(exc),
                "syntax_source": "ast_fallback",
            }
        ]

    rows: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rows.append(_fallback_symbol_row(node=node, kind="function", scope=""))
        elif isinstance(node, ast.ClassDef):
            rows.append(_fallback_symbol_row(node=node, kind="class", scope=""))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    rows.append(_fallback_symbol_row(node=child, kind="method", scope=node.name))
    return rows


def _fallback_symbol_row(
    *,
    node: ast.AST,
    kind: str,
    scope: str,
) -> dict[str, Any]:
    name = str(getattr(node, "name", "<anonymous>"))
    qualified_name = ".".join(part for part in (scope, name) if part)
    line_start = int(getattr(node, "lineno", 1) or 1)
    line_end = int(getattr(node, "end_lineno", line_start) or line_start)
    return {
        "symbol": name,
        "qualified_name": qualified_name or name,
        "kind": kind,
        "line_start": line_start,
        "line_end": line_end,
        "syntax_source": "ast_fallback",
        "scope": scope,
    }


def _normalize_symbol_rows(
    *,
    rows: Iterable[Mapping[str, Any]],
    entry: ProjectionEntry,
) -> list[dict[str, object]]:
    normalized_rows: list[dict[str, object]] = []
    for row in rows:
        name = str(row.get("symbol") or row.get("name") or "").strip()
        if not name:
            continue
        line_start = int(row.get("line_start") or row.get("lineno") or 1)
        line_end = int(row.get("line_end") or row.get("end_lineno") or line_start)
        normalized_rows.append(
            {
                "name": name,
                "qualified_name": str(row.get("qualified_name") or name).strip(),
                "kind": str(row.get("kind") or row.get("type") or "unknown").strip() or "unknown",
                "scope": str(row.get("scope") or "").strip(),
                "signature": str(row.get("signature") or "").strip(),
                "syntax_source": str(row.get("syntax_source") or "unknown").strip() or "unknown",
                "line_start": max(1, line_start),
                "line_end": max(line_start, line_end),
                "cell_ids": list(entry.cell_ids),
            }
        )
    return normalized_rows


__all__ = [
    "build_python_back_mapping_index",
    "build_python_back_mapping_index_from_workspace",
]
