"""Tree-sitter symbol finding handler.

Provides AST-based and regex fallback symbol finding for code intelligence.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.fs import KernelFileSystem


class TreeSitterSymbolHandler:
    """Handles tree-sitter based symbol finding operations.

    This handler provides:
    - Tree-sitter AST parsing for symbol detection
    - Fuzzy name matching with scoring
    - Regex fallback for environments without tree-sitter
    - Multi-language support (Python, JavaScript, TypeScript, etc.)
    """

    def __init__(self, kernel_fs: KernelFileSystem) -> None:
        self._kernel_fs = kernel_fs

    def find_symbol(
        self,
        *,
        language: str | None = None,
        file: str | None = None,
        path: str | None = None,
        symbol: str | None = None,
        name: str | None = None,
        kind: str | None = None,
        max_results: int = 10,
        fuzzy: bool = True,
        context_radius: int = 5,
    ) -> dict[str, Any]:
        """Find symbol definitions using tree-sitter AST analysis with fuzzy match.

        Args:
            language: Programming language (python, javascript, typescript, etc.)
            file: Target file path (alias for path)
            path: Target file path
            symbol: Symbol name to search for (alias for name)
            name: Symbol name to search for
            kind: Filter by node kind/type
            max_results: Maximum number of results to return
            fuzzy: Enable fuzzy matching
            context_radius: Lines of context around each match

        Returns:
            Dict with ok, results, engine, warnings, etc.
        """
        from polaris.kernelone.llm.toolkit.executor.utils import (
            resolve_workspace_path,
            to_workspace_relative_path,
        )

        target_file = file or path
        query = symbol or name

        if not target_file:
            return {"ok": False, "error": "Missing file path (file or path)"}

        lang = (language or "").strip().lower()
        if not lang:
            return {"ok": False, "error": "Missing language parameter"}

        try:
            target = resolve_workspace_path(self._kernel_fs, str(target_file))
            rel = to_workspace_relative_path(self._kernel_fs, target)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": f"Invalid path: {exc}"}

        if not self._kernel_fs.workspace_exists(rel):
            return {"ok": False, "error": f"File not found: {target_file}"}

        try:
            raw = self._kernel_fs.workspace_read_bytes(rel)
            content = raw.decode("utf-8", errors="replace")
        except (OSError, UnicodeDecodeError) as exc:
            return {"ok": False, "error": f"Failed to read file: {exc}"}

        max_results_int = max(1, min(int(max_results), 50))
        context_radius_int = max(0, min(int(context_radius), 20))

        results: list[dict[str, Any]] = []
        warnings: list[str] = []

        query_str = query or ""

        ts_matches = self._find_symbol_nodes_ts(content, query_str, lang)
        if not ts_matches:
            warnings.append("Tree-sitter parsing failed or no matches. Using regex fallback.")

        if ts_matches:
            if kind:
                kind_filter = kind.lower()
                ts_matches = [m for m in ts_matches if kind_filter in m["node_type"].lower()]

            for match in ts_matches[:max_results_int]:
                snippet_lines = self._read_context_snippet(content, match["line"], context_radius_int)
                results.append(
                    {
                        "file": rel,
                        "line": match["line"],
                        "end_line": match["end_line"],
                        "col": match["col"],
                        "node_type": match["node_type"],
                        "name": match["name"],
                        "match_score": match["score"],
                        "match_type": "exact"
                        if match["score"] >= 1.0
                        else ("prefix" if match["score"] >= 0.8 else "fuzzy"),
                        "snippet": snippet_lines,
                        "engine": "tree-sitter",
                    }
                )

        if not results:
            fallback_results = self._find_symbol_nodes_regex(content, query_str, lang, max_results_int)
            if fallback_results:
                warnings.append("Tree-sitter not available or no matches found; used regex fallback.")
                for match in fallback_results[:max_results_int]:
                    snippet_lines = self._read_context_snippet(content, match["line"], context_radius_int)
                    results.append(
                        {
                            "file": rel,
                            "line": match["line"],
                            "end_line": match["line"],
                            "col": match.get("col", 0),
                            "node_type": match.get("node_type", "regex_match"),
                            "name": match.get("name", query),
                            "match_score": 0.5,
                            "match_type": "regex",
                            "snippet": snippet_lines,
                            "engine": "regex",
                        }
                    )

        if not results:
            return {
                "ok": True,
                "file": rel,
                "symbol": query,
                "language": lang,
                "results": [],
                "engine": "none",
                "warnings": warnings if warnings else None,
                "suggestion": f"No symbol '{query}' found in {rel}. Check spelling or use repo_rg.",
            }

        engines = list(dict.fromkeys(r["engine"] for r in results))
        return {
            "ok": True,
            "file": rel,
            "symbol": query,
            "language": lang,
            "results": results,
            "total_found": len(results),
            "engine": "/".join(engines),
            "warnings": warnings if warnings else None,
        }

    def _get_ts_parser(self, language: str):
        """Get a tree-sitter parser for the given language."""
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError:
            return None
        try:
            return get_parser(language)  # type: ignore[arg-type]
        except (AttributeError, TypeError):
            return None

    def _find_symbol_nodes_ts(self, content: str, symbol_query: str, language: str) -> list[dict[str, Any]]:
        """Find AST nodes matching symbol_query using tree-sitter."""
        parser = self._get_ts_parser(language)
        if parser is None:
            return []

        try:
            tree = parser.parse(content.encode("utf-8", errors="replace"))
        except (AttributeError, TypeError, ValueError):
            return []

        matches = []
        query_lower = symbol_query.lower()
        root = tree.root_node
        target_types = self._symbol_definition_types(language)

        def score_name(name: str) -> float:
            n = name.lower()
            q = query_lower
            if n == q:
                return 1.0
            if n.startswith(q):
                return 0.8
            if q in n:
                return 0.6
            if self._fuzzy_contains(q, n):
                return 0.4
            return 0.0

        def search_node(node) -> None:
            if node.type in target_types:
                name = self._extract_node_name(content, node)
                if name and score_name(name) > 0:
                    score = score_name(name)
                    matches.append(
                        {
                            "line": node.start_point[0] + 1,
                            "col": node.start_point[1],
                            "end_line": node.end_point[0] + 1,
                            "node_type": node.type,
                            "name": name,
                            "score": score,
                        }
                    )
            for child in node.children:
                search_node(child)

        search_node(root)
        matches.sort(key=lambda m: (-m["score"], m["line"]))
        return matches

    def _symbol_definition_types(self, language: str) -> tuple[str, ...]:
        """Return tree-sitter node types that represent symbol definitions."""
        if language == "python":
            return ("class_definition", "function_definition", "decorated_definition")
        if language in ("javascript", "typescript", "jsx", "tsx"):
            return (
                "class_declaration",
                "function_declaration",
                "function_definition",
                "arrow_function",
                "method_definition",
            )
        return ("class_declaration", "class_definition", "function_declaration", "function_definition")

    def _extract_node_name(self, content: str, node) -> str:
        """Extract the name identifier from an AST node."""
        for field in ("name", "property", "identifier"):
            child = node.child_by_field_name(field)
            if child and child.type == "identifier":
                return content[child.start_byte : child.end_byte]
        for child in node.children:
            if child.type == "identifier":
                return content[child.start_byte : child.end_byte]
        return ""

    def _fuzzy_contains(self, query: str, name: str) -> bool:
        """Check if query is close enough to name (Levenshtein-like, single edit)."""
        if len(query) < 2 or len(name) < 2:
            return False
        for i in range(len(name) - len(query) + 1):
            window = name[i : i + len(query)]
            if self._is_within_edit_dist(query, window, max_dist=1):
                return True
        return False

    def _is_within_edit_dist(self, s1: str, s2: str, max_dist: int) -> bool:
        """Check if s1 and s2 are within max_dist edits of each other."""
        if len(s1) == len(s2):
            dist = sum(c1 != c2 for c1, c2 in zip(s1, s2, strict=True))
            return dist <= max_dist
        if abs(len(s1) - len(s2)) > max_dist:
            return False
        shorter, longer = (s1, s2) if len(s1) < len(s2) else (s2, s1)
        longer_idx = 0
        diffs = 0
        for ch in shorter:
            while longer_idx < len(longer) and longer[longer_idx] != ch:
                longer_idx += 1
                diffs += 1
                if diffs > max_dist:
                    return False
            longer_idx += 1
        return diffs <= max_dist

    def _read_context_snippet(self, content: str, center_line: int, radius: int = 5) -> list[dict[str, Any]]:
        """Extract lines around center_line with line numbers."""
        lines = content.splitlines()
        start = max(0, center_line - 1 - radius)
        end = min(len(lines), center_line - 1 + radius + 1)
        result = []
        for i in range(start, end):
            result.append(
                {
                    "line_no": i + 1,
                    "content": lines[i],
                    "is_center": (i == center_line - 1),
                }
            )
        return result

    def _find_symbol_nodes_regex(
        self, content: str, query: str, language: str, max_results: int
    ) -> list[dict[str, Any]]:
        """Regex fallback for finding symbol definitions."""
        lines = content.splitlines()
        query_lower = query.lower()
        matches: list[dict[str, Any]] = []
        patterns = self._regex_definition_patterns(language)

        for i, line_text in enumerate(lines):
            line_num = i + 1
            for kind_label, pattern in patterns:
                m = re.search(pattern, line_text, re.MULTILINE)
                if m and query_lower in m.group(1).lower():
                    matches.append(
                        {
                            "line": line_num,
                            "col": m.start(1),
                            "name": m.group(1).strip(),
                            "node_type": kind_label,
                        }
                    )
                    break
            if len(matches) >= max_results:
                break

        return matches

    def _regex_definition_patterns(self, language: str) -> list[tuple[str, str]]:
        """Return (kind_label, pattern) tuples for regex-based symbol finding."""
        if language == "python":
            return [
                ("class_definition", r"^class\s+([A-Za-z_][A-Za-z0-9_]*)"),
                ("function_definition", r"^def\s+([A-Za-z_][A-Za-z0-9_]*)"),
                ("async_function", r"^async\s+def\s+([A-Za-z_][A-Za-z0-9_]*)"),
            ]
        if language in ("javascript", "typescript", "jsx", "tsx"):
            return [
                ("class_declaration", r"^class\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
                ("function_declaration", r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
                ("method_definition", r"^\s*(?:async\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)\s*\{"),
                ("const_declaration", r"^const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*="),
                ("let_declaration", r"^let\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*="),
            ]
        return [
            ("class_definition", r"^class\s+([A-Za-z_][A-Za-z0-9_]*)"),
            ("function_definition", r"^function\s+([A-Za-z_][A-Za-z0-9_]*)"),
            ("definition", r"^\s*(?:def|func|fn|proc|sub)\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ]


def create_treesitter_handler(kernel_fs: KernelFileSystem) -> TreeSitterSymbolHandler:
    """Factory function to create a TreeSitterSymbolHandler.

    Args:
        kernel_fs: KernelFileSystem instance for file operations.

    Returns:
        TreeSitterSymbolHandler instance.
    """
    return TreeSitterSymbolHandler(kernel_fs)
