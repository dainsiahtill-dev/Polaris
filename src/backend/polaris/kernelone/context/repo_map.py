from __future__ import annotations

import fnmatch
import logging
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tree_sitter_language_pack import get_parser as _get_parser_tsp  # type: ignore
    from tree_sitter_languages import get_parser as _get_parser_tsl  # type: ignore

    _TSParserGetter = _get_parser_tsp | _get_parser_tsl | None

_logger = logging.getLogger(__name__)

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}

LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
}

LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python",
    "python": "python",
    "js": "javascript",
    "javascript": "javascript",
    "jsx": "jsx",
    "ts": "typescript",
    "typescript": "typescript",
    "tsx": "tsx",
}


def build_repo_map(
    root: str,
    *,
    languages: list[str] | None = None,
    max_files: int = 200,
    max_lines: int = 200,
    per_file_lines: int = 12,
    include_glob: str | None = None,
    exclude_glob: str | None = None,
) -> dict[str, Any]:
    root = os.path.abspath(root or ".")
    allowed_langs = _normalize_languages(languages)
    total_files = 0
    mapped_files = 0
    total_lines = 0
    skeleton_lines = 0
    symbols = 0
    truncated = False
    lines: list[str] = []

    for path in _iter_files(root, include_glob, exclude_glob):
        if max_files > 0 and total_files >= max_files:
            truncated = True
            break
        ext = os.path.splitext(path)[1].lower()
        language = LANGUAGE_EXTENSIONS.get(ext)
        if not language or language not in allowed_langs:
            continue
        total_files += 1
        skeleton, file_stats = _build_file_skeleton(path, language, per_file_lines)
        if not skeleton:
            continue
        mapped_files += 1
        total_lines += file_stats.get("total_lines", 0)
        symbols += file_stats.get("symbols", 0)
        rel_path = os.path.relpath(path, root)
        lines.append(f"{rel_path}")
        for entry in skeleton:
            lines.append(f"  {entry}")
            skeleton_lines += 1
            if max_lines > 0 and len(lines) >= max_lines:
                truncated = True
                break
        if truncated:
            break

    if truncated:
        lines.append("TRUNCATED: true")

    ratio = 0.0
    if total_lines > 0:
        ratio = round(float(len(lines)) / float(total_lines), 4)

    stats = {
        "total_files": total_files,
        "mapped_files": mapped_files,
        "total_lines": total_lines,
        "skeleton_lines": len(lines),
        "symbols": symbols,
        "compressed_ratio": ratio,
    }
    return {
        "root": root,
        "languages": sorted(allowed_langs),
        "lines": lines,
        "text": "\n".join(lines),
        "stats": stats,
        "truncated": truncated,
    }


def _normalize_languages(languages: list[str] | None) -> list[str]:
    if not languages:
        return sorted(set(LANGUAGE_EXTENSIONS.values()))
    output: list[str] = []
    for value in languages:
        if not value:
            continue
        for part in str(value).replace(";", ",").split(","):
            key = part.strip().lower()
            if not key:
                continue
            mapped = LANGUAGE_ALIASES.get(key)
            if mapped and mapped not in output:
                output.append(mapped)
    return output or sorted(set(LANGUAGE_EXTENSIONS.values()))


def _iter_files(root: str, include_glob: str | None, exclude_glob: str | None) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if include_glob and not fnmatch.fnmatch(name, include_glob):
                continue
            if exclude_glob and fnmatch.fnmatch(name, exclude_glob):
                continue
            yield os.path.join(dirpath, name)


def _build_file_skeleton(path: str, language: str, per_file_lines: int) -> tuple[list[str], dict[str, int]]:
    content = _read_text(path)
    if not content:
        return [], {"total_lines": 0, "symbols": 0}
    total_lines = len(content.splitlines())
    get_parser_fn = _get_ts_parser(language)
    if get_parser_fn is None:
        return _fallback_skeleton(content, language, per_file_lines, total_lines)
    try:
        parser = get_parser_fn(language)
        tree = parser.parse(content.encode("utf-8", errors="ignore"))
    except (RuntimeError, ValueError) as exc:
        _logger.warning("kernelone.context.repo_map.parse_tree failed for %s: %s", path, exc, exc_info=True)
        return _fallback_skeleton(content, language, per_file_lines, total_lines)
    skeleton: list[str] = []
    symbols = 0
    root = tree.root_node
    class_nodes = _collect_nodes(root, _class_types(language), root_only=True)
    func_nodes = _collect_nodes(root, _function_types(language), root_only=True)
    for node in class_nodes:
        name = _ts_extract_name(content, node)
        label = name or "<anonymous>"
        skeleton.append(f"class {label} [{node.start_point[0] + 1}-{node.end_point[0] + 1}]")
        symbols += 1
        for method in _collect_methods(node, language):
            method_name = _ts_extract_name(content, method) or "<anonymous>"
            skeleton.append(f"method {method_name} [{method.start_point[0] + 1}-{method.end_point[0] + 1}]")
            symbols += 1
        if per_file_lines > 0 and len(skeleton) >= per_file_lines:
            break
    if per_file_lines <= 0 or len(skeleton) < per_file_lines:
        for node in func_nodes:
            name = _ts_extract_name(content, node)
            label = name or "<anonymous>"
            skeleton.append(f"function {label} [{node.start_point[0] + 1}-{node.end_point[0] + 1}]")
            symbols += 1
            if per_file_lines > 0 and len(skeleton) >= per_file_lines:
                break
    return skeleton, {"total_lines": total_lines, "symbols": symbols}


def _fallback_skeleton(
    content: str,
    language: str,
    per_file_lines: int,
    total_lines: int,
) -> tuple[list[str], dict[str, int]]:
    # CPU fallback.
    skeleton: list[str] = []
    symbols = 0
    patterns_re = _fallback_patterns(language)
    for kind, pattern in patterns_re:
        for match in pattern.finditer(content):
            name = match.group(1)
            line_no = content[: match.start()].count("\n") + 1
            skeleton.append(f"{kind} {name} [{line_no}-{line_no}]")
            symbols += 1
            if per_file_lines > 0 and len(skeleton) >= per_file_lines:
                return skeleton, {"total_lines": total_lines, "symbols": symbols}
    return skeleton, {"total_lines": total_lines, "symbols": symbols}


def _fallback_patterns_str(language: str) -> list[tuple[str, str]]:
    if language == "python":
        return [
            ("class", r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"),
            ("function", r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ]
    return [
        ("class", r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("function", r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)"),
    ]


def _fallback_patterns(language: str) -> list[tuple[str, re.Pattern[str]]]:
    if language == "python":
        return [
            ("class", re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
            ("function", re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
        ]
    return [
        ("class", re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
        (
            "function",
            re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        ),
    ]


def _get_ts_parser(language: str) -> Any:
    """Get tree-sitter parser for a language.

    Returns a parser function (callable) or None if unavailable.
    """
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
    except (RuntimeError, ValueError) as exc:
        _logger.warning(
            "kernelone.context.repo_map.get_parser import(tree_sitter_language_pack) failed: %s",
            exc,
            exc_info=True,
        )
        try:
            from tree_sitter_languages import get_parser  # type: ignore
        except (RuntimeError, ValueError) as exc2:
            _logger.warning(
                "kernelone.context.repo_map.get_parser import(tree_sitter_languages) failed: %s",
                exc2,
                exc_info=True,
            )
            return None
    try:
        # Return the get_parser function itself (callable) rather than calling it
        # so callers can invoke it with the language parameter
        return get_parser
    except (RuntimeError, ValueError) as exc:
        _logger.warning(
            "kernelone.context.repo_map.get_parser call failed for %s: %s",
            language,
            exc,
            exc_info=True,
        )
        return None


def _ts_extract_name(content: str, node) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        name_node = node.child_by_field_name("property")
    if name_node is None:
        for child in node.children:
            if child.type == "identifier":
                name_node = child
                break
    if name_node is None:
        return ""
    return content[name_node.start_byte : name_node.end_byte]


def _collect_nodes(root, types: tuple[str, ...], root_only: bool) -> list[Any]:
    nodes: list[Any] = []
    if root_only:
        for child in root.children:
            if child.type in types:
                nodes.append(child)
        return nodes
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in types:
            nodes.append(node)
        for child in node.children:
            stack.append(child)
    return nodes


def _collect_methods(class_node, language: str) -> list[Any]:
    method_types = _method_types(language)
    methods: list[Any] = []
    for child in class_node.children:
        if child.type in ("block", "body", "class_body"):
            for grandchild in child.children:
                if grandchild.type in method_types:
                    methods.append(grandchild)
    return methods


def _class_types(language: str) -> tuple[str, ...]:
    if language == "python":
        return ("class_definition",)
    return ("class_declaration", "class_definition")


def _function_types(language: str) -> tuple[str, ...]:
    if language == "python":
        return ("function_definition",)
    return ("function_declaration",)


def _method_types(language: str) -> tuple[str, ...]:
    if language == "python":
        return ("function_definition",)
    return ("method_definition", "method_signature")


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except (RuntimeError, ValueError) as exc:
        _logger.warning("kernelone.context.repo_map.read_text failed: %s", exc, exc_info=True)
        return ""
