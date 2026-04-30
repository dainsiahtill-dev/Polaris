"""Tags extraction using tree-sitter def/ref queries.

This module provides unified tree-sitter based extraction of:
- Definitions: class, function, method, constant, etc.
- References: symbol references within the code

The extraction uses tree-sitter queries with `name.definition.*` and
`name.reference.*` captures, falling back to regex for unsupported languages.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tree_sitter_language_pack import get_parser as _get_parser_tsp
    from tree_sitter_languages import get_parser as _get_parser_tsl

    _TSParserGetter = _get_parser_tsp | _get_parser_tsl | None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class TagKind(StrEnum):
    """Tag kind enumeration."""

    DEFINITION = "def"
    REFERENCE = "ref"


@dataclass(frozen=True)
class FileTag:
    """A single tag extracted from a file.

    Attributes:
        rel_fname: Relative file path from workspace root.
        fname: Absolute file path.
        name: Symbol name.
        kind: DEFINITION or REFERENCE.
        line: 0-indexed line number.
    """

    rel_fname: str
    fname: str
    name: str
    kind: TagKind
    line: int

    def __repr__(self) -> str:
        return f"FileTag({self.kind.value} {self.name} @ {self.rel_fname}:{self.line + 1})"


# ---------------------------------------------------------------------------
# Language support
# ---------------------------------------------------------------------------

LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
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
    "go": "go",
    "rust": "rust",
    "rs": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "h": "c",
    "hpp": "cpp",
}

# Node type patterns per language
_CLASS_TYPES: dict[str, tuple[str, ...]] = {
    "python": ("class_definition",),
    "javascript": ("class_declaration", "class_expression"),
    "typescript": ("class_declaration", "class_expression"),
    "jsx": ("class_declaration", "class_expression"),
    "tsx": ("class_declaration", "class_expression"),
    "go": ("type_declaration",),
    "rust": ("struct_item", "enum_item"),
    "java": ("class_declaration", "interface_declaration"),
    "c": ("struct_specifier", "union_specifier", "enum_specifier"),
    "cpp": ("class_specifier", "struct_specifier", "union_specifier", "enum_specifier"),
}

_FUNCTION_TYPES: dict[str, tuple[str, ...]] = {
    "python": ("function_definition", "async_function_definition"),
    "javascript": ("function_declaration", "function", "arrow_function"),
    "typescript": ("function_declaration", "function", "arrow_function"),
    "jsx": ("function_declaration", "function", "arrow_function"),
    "tsx": ("function_declaration", "function", "arrow_function"),
    "go": ("function_declaration", "method_declaration"),
    "rust": ("function_item", "method_declaration"),
    "java": ("method_declaration", "constructor_declaration"),
    "c": ("function_definition",),
    "cpp": ("function_definition", "method_declaration"),
}

_METHOD_TYPES: dict[str, tuple[str, ...]] = {
    "python": ("function_definition",),
    "javascript": ("method_definition", "property_identifier"),
    "typescript": ("method_definition", "method_signature"),
    "jsx": ("method_definition", "method_signature"),
    "tsx": ("method_definition", "method_signature"),
    "go": ("method_declaration",),
    "rust": ("method_declaration",),
    "java": ("method_declaration", "constructor_declaration"),
    "c": (),  # C doesn't have methods
    "cpp": ("method_declaration", "function_definition"),
}


def get_language_from_ext(ext: str) -> str | None:
    """Get normalized language name from file extension."""
    normalized = ext.lower().lstrip(".")
    return LANGUAGE_ALIASES.get(normalized) or LANGUAGE_EXTENSIONS.get(ext.lower())


def get_language_from_filename(filename: str) -> str | None:
    """Get normalized language name from filename."""
    _, ext = os.path.splitext(filename)
    return get_language_from_ext(ext)


# ---------------------------------------------------------------------------
# Fallback patterns (regex-based)
# ---------------------------------------------------------------------------

_FALLBACK_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "python": [
        ("class", re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
        ("function", re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
    ],
    "javascript": [
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE)),
    ],
    "typescript": [
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE)),
    ],
    "go": [
        ("class", re.compile(r"^\s*type\s+(\w+)\s+(?:struct|interface)", re.MULTILINE)),
        ("function", re.compile(r"^\s*func\s+(\w+)", re.MULTILINE)),
    ],
    "rust": [
        ("class", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum)\s+(\w+)", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)", re.MULTILINE)),
    ],
    "java": [
        ("class", re.compile(r"^\s*(?:public\s+)?(?:class|interface)\s+(\w+)", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:public\s+)?(?:static\s+)?(?:\w+\s+)*(\w+)\s*\(", re.MULTILINE)),
    ],
    "default": [
        ("class", re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
        ("function", re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
    ],
}


def _get_fallback_patterns(language: str) -> list[tuple[str, re.Pattern[str]]]:
    """Get fallback regex patterns for a language."""
    return _FALLBACK_PATTERNS.get(language, _FALLBACK_PATTERNS["default"])


# ---------------------------------------------------------------------------
# Tree-sitter helpers
# ---------------------------------------------------------------------------


def _get_ts_parser(language: str) -> Any:
    """Get tree-sitter parser function for a language.

    Returns a parser function (callable) or None if unavailable.
    """
    try:
        from tree_sitter_language_pack import get_parser
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "TagsExtractor: failed to import tree_sitter_language_pack: %s",
            exc,
        )
        try:
            from tree_sitter_languages import get_parser
        except (RuntimeError, ValueError) as exc2:
            logger.warning(
                "TagsExtractor: failed to import tree_sitter_languages: %s",
                exc2,
            )
            return None
    try:
        # Return the get_parser function itself (callable) rather than calling it
        # so callers can invoke it with the language parameter
        return get_parser
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "TagsExtractor: get_parser(%s) failed: %s",
            language,
            exc,
        )
        return None


def _ts_extract_name(content: str, node: Any) -> str:
    """Extract name from a tree-sitter node."""
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


def _collect_nodes(
    root: Any,
    types: tuple[str, ...],
    root_only: bool = False,
) -> list[Any]:
    """Collect nodes matching given types."""
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


def _collect_methods(class_node: Any, language: str) -> list[Any]:
    """Collect method nodes from a class node."""
    method_types = _METHOD_TYPES.get(language, ())
    methods: list[Any] = []
    for child in class_node.children:
        if child.type in ("block", "body", "class_body"):
            for grandchild in child.children:
                if grandchild.type in method_types:
                    methods.append(grandchild)
    return methods


def _read_text(path: str) -> str:
    """Read text file with UTF-8 encoding."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "TagsExtractor: failed to read %s: %s",
            path,
            exc,
        )
        return ""


# ---------------------------------------------------------------------------
# TagsExtractor
# ---------------------------------------------------------------------------


class TagsExtractor:
    """Extracts def/ref tags from source files using tree-sitter.

    Usage:
        extractor = TagsExtractor(workspace="/repo")
        tags = extractor.get_tags("src/main.py")

        for tag in tags:
            print(f"{tag.kind}: {tag.name} at {tag.rel_fname}:{tag.line}")
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        languages: list[str] | None = None,
    ) -> None:
        self.workspace = str(workspace)
        self._allowed_langs = self._normalize_languages(languages)
        self._ts_parsers: dict[str, Any] = {}

    def _normalize_languages(self, languages: list[str] | None) -> list[str]:
        """Normalize language list."""
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

    def _get_parser(self, language: str) -> Any:
        """Get or create a tree-sitter parser for a language.

        Returns a parser function (callable) or None if unavailable.
        """
        if language not in self._ts_parsers:
            self._ts_parsers[language] = _get_ts_parser(language)
        get_parser_fn = self._ts_parsers[language]
        if get_parser_fn is None:
            return None
        # Call the parser function with the language to get the actual parser
        try:
            return get_parser_fn(language)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "TagsExtractor: parser creation failed for %s: %s",
                language,
                exc,
            )
            return None

    def _is_allowed_language(self, language: str) -> bool:
        """Check if a language is in the allowed list."""
        return language in self._allowed_langs

    def get_tags(self, abs_path: str) -> list[FileTag]:
        """Extract tags from a file.

        Args:
            abs_path: Absolute path to the file.

        Returns:
            List of FileTag objects.
        """
        if not os.path.isfile(abs_path):
            return []

        rel_path = self._get_rel_path(abs_path)
        ext = os.path.splitext(abs_path)[1].lower()
        language = get_language_from_ext(ext)

        if not language or not self._is_allowed_language(language):
            return []

        tags = list(self._get_tags_tree_sitter(abs_path, rel_path, language))
        return tags

    def _get_rel_path(self, abs_path: str) -> str:
        """Get relative path from workspace."""
        try:
            rel = os.path.relpath(abs_path, self.workspace)
            # Normalize to forward slashes for cross-platform consistency
            return rel.replace("\\", "/")
        except ValueError:
            return abs_path.replace("\\", "/")

    def _get_tags_tree_sitter(
        self,
        abs_path: str,
        rel_path: str,
        language: str,
    ) -> Iterator[FileTag]:
        """Extract tags using tree-sitter parsing."""
        content = _read_text(abs_path)
        if not content:
            return

        parser = self._get_parser(language)
        if parser is None:
            yield from self._get_tags_fallback(abs_path, rel_path, language)
            return

        try:
            tree = parser.parse(content.encode("utf-8", errors="ignore"))
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "TagsExtractor: tree-sitter parse failed for %s: %s",
                abs_path,
                exc,
            )
            yield from self._get_tags_fallback(abs_path, rel_path, language)
            return

        root = tree.root_node

        # Extract class definitions
        class_types = _CLASS_TYPES.get(language, ())
        class_nodes = _collect_nodes(root, class_types, root_only=True)
        for node in class_nodes:
            name = _ts_extract_name(content, node)
            if name:
                yield FileTag(
                    rel_fname=rel_path,
                    fname=abs_path,
                    name=name,
                    kind=TagKind.DEFINITION,
                    line=node.start_point[0],
                )

            # Extract methods
            for method in _collect_methods(node, language):
                method_name = _ts_extract_name(content, method)
                if method_name:
                    yield FileTag(
                        rel_fname=rel_path,
                        fname=abs_path,
                        name=method_name,
                        kind=TagKind.DEFINITION,
                        line=method.start_point[0],
                    )

        # Extract function definitions (top-level)
        func_types = _FUNCTION_TYPES.get(language, ())
        func_nodes = _collect_nodes(root, func_types, root_only=True)
        for node in func_nodes:
            name = _ts_extract_name(content, node)
            if name:
                yield FileTag(
                    rel_fname=rel_path,
                    fname=abs_path,
                    name=name,
                    kind=TagKind.DEFINITION,
                    line=node.start_point[0],
                )

    def _get_tags_fallback(
        self,
        abs_path: str,
        rel_path: str,
        language: str,
    ) -> Iterator[FileTag]:
        """Extract tags using regex fallback."""
        content = _read_text(abs_path)
        if not content:
            return

        patterns = _get_fallback_patterns(language)
        for _kind_str, pattern in patterns:
            for match in pattern.finditer(content):
                name = match.group(1)
                line_no = content[: match.start()].count("\n")
                yield FileTag(
                    rel_fname=rel_path,
                    fname=abs_path,
                    name=name,
                    kind=TagKind.DEFINITION,
                    line=line_no,
                )


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def get_tags_for_file(
    workspace: str | Path,
    abs_path: str,
    *,
    languages: list[str] | None = None,
) -> list[FileTag]:
    """Convenience function to extract tags from a file.

    Args:
        workspace: Workspace root path.
        abs_path: Absolute path to the file.
        languages: Optional language filter.

    Returns:
        List of FileTag objects.
    """
    extractor = TagsExtractor(workspace, languages=languages)
    return extractor.get_tags(abs_path)


__all__ = [
    "LANGUAGE_ALIASES",
    "LANGUAGE_EXTENSIONS",
    "FileTag",
    "TagKind",
    "TagsExtractor",
    "get_tags_for_file",
]
