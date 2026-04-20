from __future__ import annotations

import ast
import re
from importlib import import_module
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import (
    MAX_FILE_SIZE_BYTES,
    MAX_METADATA_ITEMS,
    MAX_SIGNATURE_CHARS,
)

if TYPE_CHECKING:
    from pathlib import Path

_MAX_SIGNATURE_CHARS = MAX_SIGNATURE_CHARS
_MAX_METADATA_ITEMS = MAX_METADATA_ITEMS
_SYNTAX_PROVIDERS = {"off", "auto", "tree_sitter"}
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.$]*")


def _normalize_signature(text: str) -> str:
    compacted = " ".join(str(text or "").split())
    if len(compacted) <= _MAX_SIGNATURE_CHARS:
        return compacted
    return compacted[: _MAX_SIGNATURE_CHARS - 3].rstrip() + "..."


def _dedupe_text_items(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        item = str(raw).strip()
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= _MAX_METADATA_ITEMS:
            break
    return result


def _safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return _normalize_signature(ast.unparse(node))
    except (AttributeError, TypeError, ValueError):
        return ""


def _scope_contains_callable(scope_stack: list[tuple[str, str]]) -> bool:
    return any(kind in {"function", "method"} for _, kind in scope_stack)


def _scope_names(scope_stack: list[tuple[str, str]]) -> list[str]:
    return [name for name, _ in scope_stack if name]


def _scope_text(scope_stack: list[tuple[str, str]]) -> str:
    return ".".join(_scope_names(scope_stack))


def _build_qualified_name(scope_stack: list[tuple[str, str]], symbol: str) -> str:
    names = _scope_names(scope_stack)
    if names:
        return ".".join([*names, symbol])
    return symbol


def _build_symbol_row(
    *,
    symbol: str,
    kind: str,
    lang: str,
    rel_path: str,
    line_start: int,
    line_end: int,
    qualified_name: str,
    syntax_source: str,
    signature: str = "",
    scope: str = "",
    parameters: list[str] | None = None,
    return_type: str = "",
    decorators: list[str] | None = None,
    bases: list[str] | None = None,
    relation_targets: list[str] | None = None,
    type_parameters: list[str] | None = None,
    attributes: list[str] | None = None,
) -> dict[str, Any]:
    safe_line_start = max(1, int(line_start))
    safe_line_end = max(safe_line_start, int(line_end))
    signature_value = _normalize_signature(signature)
    scope_value = str(scope or "").strip()
    row: dict[str, Any] = {
        "symbol": str(symbol).strip(),
        "kind": str(kind).strip(),
        "lang": str(lang).strip(),
        "file": rel_path,
        "line_start": safe_line_start,
        "line_end": safe_line_end,
        "line_span": max(1, safe_line_end - safe_line_start + 1),
        "qualified_name": str(qualified_name).strip() or f"{rel_path}:{str(symbol).strip()}",
        "scope": scope_value,
        "scope_depth": int(scope_value.count(".") + 1) if scope_value else 0,
        "syntax_source": str(syntax_source).strip() or "unknown",
    }
    if signature_value:
        row["signature"] = signature_value
    if return_type:
        row["return_type"] = _normalize_signature(return_type)
    for key, values in (
        ("parameters", parameters or []),
        ("decorators", decorators or []),
        ("bases", bases or []),
        ("relation_targets", relation_targets or []),
        ("type_parameters", type_parameters or []),
        ("attributes", attributes or []),
    ):
        normalized = _dedupe_text_items([str(item) for item in values])
        if normalized:
            row[key] = normalized
    return row


def _dedupe_symbol_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for row in sorted(
        rows,
        key=lambda item: (
            int(item.get("line_start", 1)),
            int(item.get("line_end", 1)),
            str(item.get("qualified_name", "")),
            str(item.get("kind", "")),
        ),
    ):
        key = (
            str(row.get("qualified_name", "")),
            str(row.get("kind", "")),
            int(row.get("line_start", 1)),
            int(row.get("line_end", 1)),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _py_parameters_from_ast(args: ast.arguments) -> list[str]:
    text = _safe_unparse(args)
    if not text:
        return []
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    values = [segment.strip() for segment in text.split(",") if segment.strip()]
    return values[:_MAX_METADATA_ITEMS]


def _py_function_signature_from_ast(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args_text = _safe_unparse(node.args)
    if not args_text:
        args_text = "()"
    elif not args_text.startswith("("):
        args_text = f"({args_text})"
    return_type = _safe_unparse(node.returns)
    return _normalize_signature(f"{prefix} {node.name}{args_text}{f' -> {return_type}' if return_type else ''}")


def _py_class_signature_from_ast(node: ast.ClassDef) -> str:
    bases = [_safe_unparse(base) for base in node.bases]
    bases = [item for item in bases if item]
    base_text = f"({', '.join(bases)})" if bases else ""
    return _normalize_signature(f"class {node.name}{base_text}")


def _assignment_targets(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        result: list[str] = []
        for item in target.elts:
            result.extend(_assignment_targets(item))
        return result
    return []


def _assignment_symbol_rows_from_ast(
    node: ast.Assign | ast.AnnAssign,
    *,
    rel_path: str,
    scope_stack: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    if _scope_contains_callable(scope_stack):
        return []
    targets: list[str] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            targets.extend(_assignment_targets(target))
    elif isinstance(node.target, ast.expr):
        targets.extend(_assignment_targets(node.target))
    rows: list[dict[str, Any]] = []
    line_start = int(getattr(node, "lineno", 1))
    line_end = int(getattr(node, "end_lineno", line_start))
    annotation = (
        _safe_unparse(node.annotation)
        if isinstance(node, ast.AnnAssign) and isinstance(node.annotation, ast.AST)
        else ""
    )
    for symbol in _dedupe_text_items(targets):
        signature = f"{symbol}: {annotation}" if annotation else symbol
        rows.append(
            _build_symbol_row(
                symbol=symbol,
                kind="variable",
                lang="python",
                rel_path=rel_path,
                line_start=line_start,
                line_end=line_end,
                qualified_name=_build_qualified_name(scope_stack, symbol),
                syntax_source="ast",
                signature=signature,
                scope=_scope_text(scope_stack),
            )
        )
    return rows


def _py_symbols_from_ast(tree: ast.AST, rel_path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(node: ast.AST, scope_stack: list[tuple[str, str]]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                decorators = [_safe_unparse(item) for item in child.decorator_list]
                decorators = [item for item in decorators if item]
                bases = [_safe_unparse(base) for base in child.bases]
                bases = [item for item in bases if item]
                class_attributes: list[str] = []
                if decorators:
                    class_attributes.append("decorated")
                rows.append(
                    _build_symbol_row(
                        symbol=child.name,
                        kind="class",
                        lang="python",
                        rel_path=rel_path,
                        line_start=int(child.lineno),
                        line_end=int(getattr(child, "end_lineno", child.lineno)),
                        qualified_name=_build_qualified_name(scope_stack, child.name),
                        syntax_source="ast",
                        signature=_py_class_signature_from_ast(child),
                        scope=_scope_text(scope_stack),
                        decorators=decorators,
                        bases=bases,
                        relation_targets=bases,
                        attributes=class_attributes,
                    )
                )
                visit(child, [*scope_stack, (child.name, "class")])
                continue

            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent_kind = scope_stack[-1][1] if scope_stack else ""
                symbol_kind = "method" if parent_kind == "class" else "function"
                decorators = [_safe_unparse(item) for item in child.decorator_list]
                decorators = [item for item in decorators if item]
                callable_attributes: list[str] = []
                if isinstance(child, ast.AsyncFunctionDef):
                    callable_attributes.append("async")
                if decorators:
                    callable_attributes.append("decorated")
                if symbol_kind == "method":
                    callable_attributes.append("bound_to_class")
                rows.append(
                    _build_symbol_row(
                        symbol=child.name,
                        kind=symbol_kind,
                        lang="python",
                        rel_path=rel_path,
                        line_start=int(child.lineno),
                        line_end=int(getattr(child, "end_lineno", child.lineno)),
                        qualified_name=_build_qualified_name(scope_stack, child.name),
                        syntax_source="ast",
                        signature=_py_function_signature_from_ast(child),
                        scope=_scope_text(scope_stack),
                        parameters=_py_parameters_from_ast(child.args),
                        return_type=_safe_unparse(child.returns),
                        decorators=decorators,
                        attributes=callable_attributes,
                    )
                )
                visit(child, [*scope_stack, (child.name, symbol_kind)])
                continue

            if isinstance(child, (ast.Assign, ast.AnnAssign)):
                rows.extend(
                    _assignment_symbol_rows_from_ast(
                        child,
                        rel_path=rel_path,
                        scope_stack=scope_stack,
                    )
                )
                continue

            visit(child, scope_stack)

    visit(tree, [])
    return _dedupe_symbol_rows(rows)


TS_SYMBOL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "function",
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)"),
    ),
    (
        "class",
        re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+extends\s+[^{]+)?"),
    ),
    (
        "type",
        re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)(?:<[^>]+>)?"),
    ),
    ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_][A-Za-z0-9_]*)")),
    (
        "variable",
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    ),
]


def normalize_syntax_provider(value: Any, default_value: str = "off") -> str:
    token = str(value or default_value).strip().lower()
    if token in _SYNTAX_PROVIDERS:
        return token
    fallback = str(default_value or "off").strip().lower()
    return fallback if fallback in _SYNTAX_PROVIDERS else "off"


def _extract_type_parameters(signature: str) -> list[str]:
    start = signature.find("<")
    end = signature.find(">", start + 1) if start >= 0 else -1
    if start < 0 or end <= start + 1:
        return []
    inner = signature[start + 1 : end]
    return _dedupe_text_items([segment.strip() for segment in inner.split(",")])


def _extract_parameters_from_signature(signature: str) -> list[str]:
    start = signature.find("(")
    end = signature.find(")", start + 1) if start >= 0 else -1
    if start < 0 or end <= start + 1:
        return []
    inner = signature[start + 1 : end].strip()
    if not inner:
        return []
    return _dedupe_text_items([segment.strip() for segment in inner.split(",")])


def _extract_return_type_from_signature(signature: str, lang: str) -> str:
    if lang == "python":
        marker = "->"
        idx = signature.find(marker)
        if idx >= 0:
            return _normalize_signature(signature[idx + len(marker) :].strip())
        return ""
    match = re.search(r"\)\s*:\s*([^{=]+)$", signature)
    if match:
        return _normalize_signature(match.group(1).strip())
    return ""


def _normalize_relation_target(value: str) -> str:
    text = str(value).strip().rstrip(",")
    text = re.sub(r"<[^>]*>", "", text)
    match = _IDENTIFIER_RE.search(text)
    return match.group(0) if match else ""


def _extract_relation_targets_from_signature(signature: str) -> list[str]:
    text = str(signature)
    lowered = text.lower()
    targets: list[str] = []
    for keyword in ("extends", "implements"):
        start = 0
        token = f"{keyword} "
        while True:
            idx = lowered.find(token, start)
            if idx < 0:
                break
            value_start = idx + len(token)
            terminators = [
                lowered.find(" extends ", value_start),
                lowered.find(" implements ", value_start),
                lowered.find("{", value_start),
                lowered.find(":", value_start),
            ]
            valid_terminators = [item for item in terminators if item >= 0]
            value_end = min(valid_terminators) if valid_terminators else len(text)
            value = text[value_start:value_end]
            for segment in value.split(","):
                normalized = _normalize_relation_target(segment)
                if normalized:
                    targets.append(normalized)
            start = value_start
    return _dedupe_text_items(targets)


def _ts_symbols_from_text(text: str, rel_path: str, lang: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in TS_SYMBOL_PATTERNS:
            match = pattern.search(line)
            if match:
                symbol = str(match.group(1)).strip()
                signature = _normalize_signature(line.strip())
                rows.append(
                    _build_symbol_row(
                        symbol=symbol,
                        kind=kind,
                        lang=lang,
                        rel_path=rel_path,
                        line_start=line_no,
                        line_end=line_no,
                        qualified_name=symbol,
                        syntax_source="text",
                        signature=signature,
                        relation_targets=_extract_relation_targets_from_signature(signature),
                        type_parameters=_extract_type_parameters(signature),
                    )
                )
                break
    return _dedupe_symbol_rows(rows)


def _extract_symbol_name(node: Any, source_bytes: bytes) -> str:
    name_node = None
    child_by_field_name = getattr(node, "child_by_field_name", None)
    if callable(child_by_field_name):
        try:
            name_node = child_by_field_name("name")
        except (AttributeError, TypeError):
            name_node = None

    if name_node is None:
        for child in list(getattr(node, "children", []) or []):
            child_type = str(getattr(child, "type", ""))
            if child_type in {"identifier", "property_identifier", "type_identifier"}:
                name_node = child
                break

    if name_node is None:
        return ""

    start = int(getattr(name_node, "start_byte", 0))
    end = int(getattr(name_node, "end_byte", 0))
    if end <= start:
        return ""
    return source_bytes[start:end].decode("utf-8", errors="replace").strip()


def _node_line_bounds(node: Any) -> tuple[int, int]:
    start_point = tuple(getattr(node, "start_point", (0, 0)))
    end_point = tuple(getattr(node, "end_point", start_point))
    line_start = int(start_point[0]) + 1 if len(start_point) > 0 else 1
    line_end = int(end_point[0]) + 1 if len(end_point) > 0 else line_start
    return line_start, max(line_start, line_end)


def _node_text(node: Any, source_bytes: bytes) -> str:
    start = int(getattr(node, "start_byte", 0))
    end = int(getattr(node, "end_byte", 0))
    if end <= start:
        return ""
    return source_bytes[start:end].decode("utf-8", errors="replace")


def _compact_signature_from_node_text(text: str, lang: str) -> str:
    compacted = _normalize_signature(text)
    if not compacted:
        return ""
    if lang == "python":
        idx = compacted.find(":")
        if idx >= 0:
            compacted = compacted[:idx]
    else:
        idx = compacted.find("{")
        if idx >= 0:
            compacted = compacted[:idx]
    return _normalize_signature(compacted.strip())


def _extract_decorators_from_tree_sitter(node: Any, source_bytes: bytes) -> list[str]:
    decorators: list[str] = []
    for child in list(getattr(node, "children", []) or []):
        if str(getattr(child, "type", "")) != "decorator":
            continue
        value = _normalize_signature(_node_text(child, source_bytes))
        if value.startswith("@"):
            value = value[1:].strip()
        if value:
            decorators.append(value)
    return _dedupe_text_items(decorators)


def _load_tree_sitter_parser(lang: str, file_path: Path) -> Any | None:
    suffix = file_path.suffix.lower()

    # Map languages to their tree-sitter aliases
    lang_aliases = {
        "python": ["python"],
        "typescript": ["typescript", "tsx"] if suffix == ".tsx" else ["typescript"],
        "javascript": ["javascript", "jsx"] if suffix == ".jsx" else ["javascript"],
    }

    aliases = lang_aliases.get(lang, [])
    if not aliases:
        return None

    # Try different tree-sitter language packages
    modules_to_try = [
        "tree_sitter_language_pack",
        "tree_sitter_languages",
        "tree_sitter_language_pack.all_languages",
    ]

    for module_name in modules_to_try:
        try:
            module = import_module(module_name)
        except ImportError:
            continue

        parser_getter = getattr(module, "get_parser", None)
        if not callable(parser_getter):
            continue

        for alias in aliases:
            try:
                parser = parser_getter(alias)
                if parser is not None:
                    return parser
            except (ValueError, TypeError, KeyError):
                continue

    return None


def _collect_py_tree_sitter_symbols(
    *,
    node: Any,
    source_bytes: bytes,
    rel_path: str,
    rows: list[dict[str, Any]],
    scope_stack: list[tuple[str, str]],
    decorators: list[str] | None = None,
) -> None:
    if node is None:
        return
    node_type = str(getattr(node, "type", ""))
    children = list(getattr(node, "children", []) or [])
    active_decorators = list(decorators or [])

    if node_type == "decorated_definition":
        decorated = _extract_decorators_from_tree_sitter(node, source_bytes)
        definition_node = None
        for child in children:
            if str(getattr(child, "type", "")) in {
                "function_definition",
                "class_definition",
            }:
                definition_node = child
                break
        if definition_node is not None:
            _collect_py_tree_sitter_symbols(
                node=definition_node,
                source_bytes=source_bytes,
                rel_path=rel_path,
                rows=rows,
                scope_stack=scope_stack,
                decorators=decorated,
            )
            for child in children:
                if child is definition_node or str(getattr(child, "type", "")) == "decorator":
                    continue
                _collect_py_tree_sitter_symbols(
                    node=child,
                    source_bytes=source_bytes,
                    rel_path=rel_path,
                    rows=rows,
                    scope_stack=scope_stack,
                )
            return

    if node_type in {"function_definition", "class_definition"}:
        symbol = _extract_symbol_name(node, source_bytes)
        if symbol:
            scope = _scope_text(scope_stack)
            line_start, line_end = _node_line_bounds(node)
            signature = _compact_signature_from_node_text(_node_text(node, source_bytes), "python")
            relation_targets = (
                _extract_relation_targets_from_signature(signature) if node_type == "class_definition" else []
            )
            symbol_kind = "class"
            if node_type == "function_definition":
                parent_kind = scope_stack[-1][1] if scope_stack else ""
                symbol_kind = "method" if parent_kind == "class" else "function"
            attributes: list[str] = []
            if signature.startswith("async "):
                attributes.append("async")
            if active_decorators:
                attributes.append("decorated")
            if symbol_kind == "method":
                attributes.append("bound_to_class")
            rows.append(
                _build_symbol_row(
                    symbol=symbol,
                    kind=symbol_kind,
                    lang="python",
                    rel_path=rel_path,
                    line_start=line_start,
                    line_end=line_end,
                    qualified_name=_build_qualified_name(scope_stack, symbol),
                    syntax_source="tree_sitter",
                    signature=signature,
                    scope=scope,
                    parameters=_extract_parameters_from_signature(signature)
                    if symbol_kind in {"function", "method"}
                    else [],
                    return_type=_extract_return_type_from_signature(signature, "python")
                    if symbol_kind in {"function", "method"}
                    else "",
                    decorators=active_decorators,
                    bases=relation_targets if symbol_kind == "class" else [],
                    relation_targets=relation_targets,
                    attributes=attributes,
                )
            )
            scope_stack = [*scope_stack, (symbol, symbol_kind)]

    if node_type == "assignment" and not _scope_contains_callable(scope_stack):
        symbol = _extract_symbol_name(node, source_bytes)
        if symbol:
            line_start, line_end = _node_line_bounds(node)
            signature = _compact_signature_from_node_text(_node_text(node, source_bytes), "python")
            rows.append(
                _build_symbol_row(
                    symbol=symbol,
                    kind="variable",
                    lang="python",
                    rel_path=rel_path,
                    line_start=line_start,
                    line_end=line_end,
                    qualified_name=_build_qualified_name(scope_stack, symbol),
                    syntax_source="tree_sitter",
                    signature=signature,
                    scope=_scope_text(scope_stack),
                )
            )

    for child in children:
        _collect_py_tree_sitter_symbols(
            node=child,
            source_bytes=source_bytes,
            rel_path=rel_path,
            rows=rows,
            scope_stack=scope_stack,
        )


def _py_symbols_from_tree_sitter(
    *,
    text: str,
    file_path: Path,
    rel_path: str,
) -> list[dict[str, Any]]:
    parser = _load_tree_sitter_parser("python", file_path)
    if parser is None:
        return []

    source_bytes = text.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(source_bytes)
    except (ValueError, TypeError):
        return []

    root_node = getattr(tree, "root_node", None)
    if root_node is None:
        return []

    rows: list[dict[str, Any]] = []
    _collect_py_tree_sitter_symbols(
        node=root_node,
        source_bytes=source_bytes,
        rel_path=rel_path,
        rows=rows,
        scope_stack=[],
    )
    return _dedupe_symbol_rows(rows)


def _ts_symbols_from_tree_sitter(
    *,
    text: str,
    file_path: Path,
    rel_path: str,
    lang: str,
) -> list[dict[str, Any]]:
    parser = _load_tree_sitter_parser(lang, file_path)
    if parser is None:
        return []
    source_bytes = text.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(source_bytes)
    except (ValueError, TypeError):
        return []

    root_node = getattr(tree, "root_node", None)
    if root_node is None:
        return []

    rows: list[dict[str, Any]] = []

    def visit(node: Any, scope_stack: list[tuple[str, str]]) -> None:
        if node is None:
            return
        node_type = str(getattr(node, "type", ""))
        children = list(getattr(node, "children", []) or [])

        symbol = ""
        symbol_kind = ""
        signature = ""
        relation_targets: list[str] = []
        attributes: list[str] = []
        scope_for_children = scope_stack

        if node_type in {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
        }:
            symbol = _extract_symbol_name(node, source_bytes)
            parent_kind = scope_stack[-1][1] if scope_stack else ""
            symbol_kind = "method" if node_type == "method_definition" else "function"
            if parent_kind == "class" and symbol_kind == "function":
                symbol_kind = "method"
            signature = _compact_signature_from_node_text(_node_text(node, source_bytes), lang)
            if signature.lower().startswith("async "):
                attributes.append("async")

        elif node_type == "class_declaration":
            symbol = _extract_symbol_name(node, source_bytes)
            symbol_kind = "class"
            signature = _compact_signature_from_node_text(_node_text(node, source_bytes), lang)
            relation_targets = _extract_relation_targets_from_signature(signature)

        elif node_type in {"interface_declaration", "type_alias_declaration"}:
            symbol = _extract_symbol_name(node, source_bytes)
            symbol_kind = "type"
            signature = _compact_signature_from_node_text(_node_text(node, source_bytes), lang)
            relation_targets = _extract_relation_targets_from_signature(signature)

        elif node_type == "variable_declarator":
            symbol = _extract_symbol_name(node, source_bytes)
            value_node = None
            child_by_field_name = getattr(node, "child_by_field_name", None)
            if callable(child_by_field_name):
                try:
                    value_node = child_by_field_name("value")
                except (AttributeError, TypeError):
                    value_node = None
            value_type = str(getattr(value_node, "type", ""))
            if value_type in {"arrow_function", "function_expression"}:
                symbol_kind = "function"
                signature = _compact_signature_from_node_text(_node_text(node, source_bytes), lang)
                attributes.append("from_variable_declarator")
            elif not _scope_contains_callable(scope_stack):
                symbol_kind = "variable"
                signature = _compact_signature_from_node_text(_node_text(node, source_bytes), lang)

        if symbol and symbol_kind:
            line_start, line_end = _node_line_bounds(node)
            rows.append(
                _build_symbol_row(
                    symbol=symbol,
                    kind=symbol_kind,
                    lang=lang,
                    rel_path=rel_path,
                    line_start=line_start,
                    line_end=line_end,
                    qualified_name=_build_qualified_name(scope_stack, symbol),
                    syntax_source="tree_sitter",
                    signature=signature,
                    scope=_scope_text(scope_stack),
                    parameters=_extract_parameters_from_signature(signature),
                    return_type=_extract_return_type_from_signature(signature, lang),
                    relation_targets=relation_targets,
                    type_parameters=_extract_type_parameters(signature),
                    attributes=attributes,
                )
            )
            if symbol_kind in {"class", "type", "function", "method"}:
                scope_for_children = [*scope_stack, (symbol, symbol_kind)]

        for child in children:
            visit(child, scope_for_children)

    visit(root_node, [])
    return _dedupe_symbol_rows(rows)


def extract_symbols(
    file_path: Path,
    rel_path: str,
    lang: str,
    runtime_cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    # Check file size to prevent blocking
    try:
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            return [
                {
                    "type": "error",
                    "name": "file_too_large",
                    "line_start": 1,
                    "line_end": 1,
                    "char_start": 0,
                    "char_end": 0,
                    "error": f"File too large: {file_size:,} bytes",
                }
            ]
    except OSError:
        return [
            {
                "type": "error",
                "name": "stat_error",
                "line_start": 1,
                "line_end": 1,
                "char_start": 0,
                "char_end": 0,
                "error": "Cannot read file stats",
            }
        ]

    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return [
            {
                "type": "error",
                "name": "read_error",
                "line_start": 1,
                "line_end": 1,
                "char_start": 0,
                "char_end": 0,
                "error": str(exc),
            }
        ]

    if lang == "python":
        runtime = dict(runtime_cfg or {})
        parser_enabled = bool(runtime.get("syntax_parser_enabled", False))
        parser_provider = normalize_syntax_provider(
            runtime.get("syntax_parser_provider", "off"),
            default_value="off",
        )

        # Try tree-sitter first if enabled, fallback to AST
        if parser_enabled and parser_provider in {"auto", "tree_sitter"}:
            ts_rows = _py_symbols_from_tree_sitter(
                text=text,
                file_path=file_path,
                rel_path=rel_path,
            )
            if ts_rows:
                return ts_rows

        # Fallback to standard AST
        try:
            tree = ast.parse(text, filename=rel_path)
            return _py_symbols_from_ast(tree, rel_path)
        except SyntaxError:
            return []
    if lang in {"typescript", "javascript"}:
        runtime = dict(runtime_cfg or {})
        parser_enabled = bool(runtime.get("syntax_parser_enabled", False))
        parser_provider = normalize_syntax_provider(
            runtime.get("syntax_parser_provider", "off"),
            default_value="off",
        )
        if parser_enabled and parser_provider in {"auto", "tree_sitter"}:
            parsed_rows = _ts_symbols_from_tree_sitter(
                text=text,
                file_path=file_path,
                rel_path=rel_path,
                lang=lang,
            )
            if parsed_rows:
                return parsed_rows
        return _ts_symbols_from_text(text, rel_path, lang)
    return []
