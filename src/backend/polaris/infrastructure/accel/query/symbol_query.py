from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..storage.index_cache import load_index_rows
from ..utils import normalize_path_str as _normalize_path
from .semantic_relations import build_semantic_relation_graph

_VALID_SYMBOL_KINDS = {"function", "class", "method", "variable", "constant", "property"}
_VALID_LANGUAGES = {"python", "typescript", "javascript", "jsx", "tsx"}
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _match_score(query: str, symbol: str, qualified_name: str) -> float:
    """Calculate match score for ranking results.

    Returns:
        Score from 0.0 to 1.0, higher is better match.
        - 1.0: exact match on symbol name
        - 0.9: exact match on qualified_name
        - 0.7: prefix match on symbol
        - 0.5: prefix match on qualified_name
        - 0.3: contains match on symbol
        - 0.2: contains match on qualified_name
        - 0.0: no match
    """
    query_low = query.lower()
    symbol_low = symbol.lower()
    qname_low = qualified_name.lower()

    if symbol_low == query_low:
        return 1.0
    if qname_low == query_low:
        return 0.9
    if symbol_low.startswith(query_low):
        return 0.7
    if qname_low.startswith(query_low):
        return 0.5
    if query_low in symbol_low:
        return 0.3
    if query_low in qname_low:
        return 0.2
    return 0.0


def search_symbols(
    index_dir: Path,
    query: str,
    *,
    symbol_kinds: list[str] | None = None,
    languages: list[str] | None = None,
    max_results: int = 50,
    include_signature: bool = True,
) -> list[dict[str, Any]]:
    """Search symbols in the index.

    Args:
        index_dir: Path to the index directory.
        query: Search query string.
        symbol_kinds: Filter by symbol kinds (function, class, method, variable).
        languages: Filter by languages (python, typescript, javascript).
        max_results: Maximum number of results to return.
        include_signature: Whether to include signature in results.

    Returns:
        List of matching symbols with scores.
    """
    query_text = str(query or "").strip()
    if not query_text:
        return []

    max_results = max(1, min(500, int(max_results)))

    kind_filter: set[str] | None = None
    if symbol_kinds:
        kind_filter = {k.lower().strip() for k in symbol_kinds if k.lower().strip() in _VALID_SYMBOL_KINDS}
        if not kind_filter:
            kind_filter = None

    lang_filter: set[str] | None = None
    if languages:
        lang_filter = {lang.lower().strip() for lang in languages if lang.lower().strip() in _VALID_LANGUAGES}
        if not lang_filter:
            lang_filter = None

    all_symbols = load_index_rows(index_dir, kind="symbols", key_field="file")

    scored_results: list[tuple[float, dict[str, Any]]] = []

    for row in all_symbols:
        symbol_name = str(row.get("symbol", "")).strip()
        qualified_name = str(row.get("qualified_name", "")).strip()
        kind = str(row.get("kind", "")).strip().lower()
        lang = str(row.get("lang", "")).strip().lower()

        if kind_filter and kind not in kind_filter:
            continue
        if lang_filter and lang not in lang_filter:
            continue

        score = _match_score(query_text, symbol_name, qualified_name)
        if score <= 0.0:
            continue

        result: dict[str, Any] = {
            "symbol": symbol_name,
            "qualified_name": qualified_name,
            "kind": kind,
            "lang": lang,
            "file": _normalize_path(str(row.get("file", ""))),
            "line_start": int(row.get("line_start", 1)),
            "line_end": int(row.get("line_end", 1)),
            "score": round(score, 4),
        }

        if include_signature:
            signature = str(row.get("signature", "")).strip()
            if signature:
                result["signature"] = signature

        scope = str(row.get("scope", "")).strip()
        if scope:
            result["scope"] = scope

        scored_results.append((score, result))

    scored_results.sort(key=lambda item: (-item[0], item[1]["symbol"].lower()))

    return [result for _, result in scored_results[:max_results]]


def get_symbol_details(
    index_dir: Path,
    symbol_name: str,
    file_path: str = "",
    include_relations: bool = True,
) -> dict[str, Any] | None:
    """Get detailed information about a specific symbol.

    Args:
        index_dir: Path to the index directory.
        symbol_name: Name of the symbol to find.
        file_path: Optional file path to narrow search.
        include_relations: Whether to include relation information.

    Returns:
        Symbol details dict or None if not found.
    """
    symbol_text = str(symbol_name or "").strip()
    if not symbol_text:
        return None

    file_filter = _normalize_path(file_path).lower() if file_path else ""

    all_symbols = load_index_rows(index_dir, kind="symbols", key_field="file")

    candidates: list[dict[str, Any]] = []
    for row in all_symbols:
        row_symbol = str(row.get("symbol", "")).strip()
        row_qualified = str(row.get("qualified_name", "")).strip()
        row_file = _normalize_path(str(row.get("file", "")))

        if file_filter and row_file.lower() != file_filter:
            continue

        if (
            row_symbol.lower() == symbol_text.lower()
            or row_qualified.lower() == symbol_text.lower()
            or row_qualified.lower().endswith("." + symbol_text.lower())
        ):
            candidates.append(row)

    if not candidates:
        return None

    candidates.sort(
        key=lambda r: (
            0 if str(r.get("symbol", "")).lower() == symbol_text.lower() else 1,
            int(r.get("line_start", 1)),
        )
    )

    row = candidates[0]

    result: dict[str, Any] = {
        "symbol": str(row.get("symbol", "")),
        "qualified_name": str(row.get("qualified_name", "")),
        "kind": str(row.get("kind", "")),
        "lang": str(row.get("lang", "")),
        "file": _normalize_path(str(row.get("file", ""))),
        "line_start": int(row.get("line_start", 1)),
        "line_end": int(row.get("line_end", 1)),
    }

    for key in ("signature", "scope", "return_type"):
        value = str(row.get(key, "")).strip()
        if value:
            result[key] = value

    for key in ("parameters", "decorators", "bases", "type_parameters", "attributes"):
        value = row.get(key, [])
        if isinstance(value, list) and value:
            result[key] = value

    if include_relations:
        relations = _build_symbol_relations(index_dir, row)
        if relations:
            result["relations"] = relations

    if len(candidates) > 1:
        result["other_definitions"] = [
            {
                "file": _normalize_path(str(c.get("file", ""))),
                "line_start": int(c.get("line_start", 1)),
                "qualified_name": str(c.get("qualified_name", "")),
            }
            for c in candidates[1:5]
        ]

    return result


def _build_symbol_relations(
    index_dir: Path,
    symbol_row: dict[str, Any],
) -> dict[str, Any]:
    """Build relation information for a symbol."""
    relations: dict[str, Any] = {}

    relation_targets = symbol_row.get("relation_targets", [])
    if isinstance(relation_targets, list) and relation_targets:
        relations["calls"] = list(relation_targets[:20])

    symbol_name = str(symbol_row.get("symbol", "")).strip().lower()
    qualified_name = str(symbol_row.get("qualified_name", "")).strip().lower()
    symbol_file = _normalize_path(str(symbol_row.get("file", ""))).lower()

    all_refs = load_index_rows(index_dir, kind="references", key_field="file")

    called_by: list[str] = []
    for ref in all_refs:
        target = str(ref.get("target_symbol", "")).strip().lower()
        edge_to = str(ref.get("edge_to", "")).strip().lower()

        if target in (symbol_name, qualified_name) or edge_to == symbol_file:
            source_symbol = str(ref.get("source_symbol", "")).strip()
            if source_symbol and source_symbol not in called_by:
                called_by.append(source_symbol)

    if called_by:
        relations["called_by"] = called_by[:20]

    bases = symbol_row.get("bases", [])
    if isinstance(bases, list) and bases:
        relations["extends"] = list(bases)

    return relations


def build_call_graph(
    index_dir: Path,
    start_symbol: str = "",
    start_file: str = "",
    depth: int = 2,
    direction: str = "both",
) -> dict[str, Any]:
    """Build a call graph from the specified starting point.

    Args:
        index_dir: Path to the index directory.
        start_symbol: Starting symbol name (optional).
        start_file: Starting file path (optional).
        depth: Maximum depth to traverse.
        direction: Direction to traverse (up/down/both).

    Returns:
        Call graph structure with nodes and edges.
    """
    depth = max(1, min(5, int(depth)))
    direction = str(direction or "both").strip().lower()
    if direction not in {"up", "down", "both"}:
        direction = "both"

    symbols_by_file = _group_by_file(load_index_rows(index_dir, kind="symbols", key_field="file"), "file")
    references_by_file = _group_by_file(load_index_rows(index_dir, kind="references", key_field="file"), "file")
    deps_by_file = _group_by_file(load_index_rows(index_dir, kind="dependencies", key_field="file"), "file")
    indexed_files = list(symbols_by_file.keys())

    relation_graph = build_semantic_relation_graph(
        symbols_by_file=symbols_by_file,
        references_by_file=references_by_file,
        deps_by_file=deps_by_file,
        indexed_files=indexed_files,
    )

    start_nodes: set[str] = set()
    start_file_norm = _normalize_path(start_file).lower()
    start_symbol_lower = str(start_symbol or "").strip().lower()

    if start_file_norm:
        for file_path in indexed_files:
            if _normalize_path(file_path).lower() == start_file_norm:
                start_nodes.add(_normalize_path(file_path))
                break

    if start_symbol_lower and not start_nodes:
        for file_path, rows in symbols_by_file.items():
            for row in rows:
                symbol = str(row.get("symbol", "")).strip().lower()
                qualified = str(row.get("qualified_name", "")).strip().lower()
                if start_symbol_lower in (symbol, qualified):
                    start_nodes.add(_normalize_path(file_path))

    if not start_nodes:
        start_nodes = {_normalize_path(f) for f in indexed_files[:3]}

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    visited: set[str] = set()

    def _add_node(file_path: str, level: int) -> None:
        if file_path in nodes:
            return
        symbol_count = len(symbols_by_file.get(file_path, []))
        nodes[file_path] = {
            "file": file_path,
            "level": level,
            "symbol_count": symbol_count,
        }

    def _traverse_down(node: str, current_depth: int) -> None:
        if current_depth > depth or node in visited:
            return
        visited.add(node)
        _add_node(node, current_depth)

        targets = relation_graph.get(node, {})
        for target, weight in sorted(targets.items(), key=lambda x: -x[1])[:10]:
            _add_node(target, current_depth + 1)
            edges.append(
                {
                    "source": node,
                    "target": target,
                    "weight": round(weight, 4),
                    "direction": "down",
                }
            )
            if current_depth < depth:
                _traverse_down(target, current_depth + 1)

    def _traverse_up(node: str, current_depth: int) -> None:
        if current_depth > depth or node in visited:
            return
        visited.add(node)
        _add_node(node, current_depth)

        for source, targets in relation_graph.items():
            if node in targets:
                weight = targets[node]
                _add_node(source, current_depth + 1)
                edges.append(
                    {
                        "source": source,
                        "target": node,
                        "weight": round(weight, 4),
                        "direction": "up",
                    }
                )
                if current_depth < depth:
                    _traverse_up(source, current_depth + 1)

    for start in start_nodes:
        if direction in {"down", "both"}:
            _traverse_down(start, 0)
        visited.clear()
        if direction in {"up", "both"}:
            _traverse_up(start, 0)

    return {
        "start_nodes": list(start_nodes),
        "depth": depth,
        "direction": direction,
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }


def _group_by_file(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """Group rows by file path."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        file_value = _normalize_path(str(row.get(key, "")))
        if file_value:
            grouped[file_value].append(row)
    return dict(grouped)


def get_symbol_context(
    index_dir: Path,
    project_dir: Path,
    symbol_name: str,
    context_type: str = "definition",
    max_files: int = 10,
) -> dict[str, Any]:
    """Get context for a specific symbol.

    Args:
        index_dir: Path to the index directory.
        project_dir: Path to the project directory.
        symbol_name: Name of the symbol.
        context_type: Type of context (definition/usage/related).
        max_files: Maximum number of files to include.

    Returns:
        Dict with symbol context including code snippets.
    """
    symbol_text = str(symbol_name or "").strip()
    if not symbol_text:
        return {"status": "error", "error": "empty_symbol_name"}

    context_type = str(context_type or "definition").strip().lower()
    if context_type not in {"definition", "usage", "related"}:
        context_type = "definition"

    max_files = max(1, min(50, int(max_files)))

    symbol_details = get_symbol_details(
        index_dir=index_dir,
        symbol_name=symbol_text,
        include_relations=True,
    )

    if symbol_details is None:
        return {
            "status": "not_found",
            "symbol_name": symbol_text,
            "message": f"Symbol '{symbol_text}' not found.",
        }

    result: dict[str, Any] = {
        "status": "ok",
        "symbol": symbol_details,
        "context_type": context_type,
        "snippets": [],
    }

    definition_file = _normalize_path(str(symbol_details.get("file", "")))
    line_start = int(symbol_details.get("line_start", 1))
    line_end = int(symbol_details.get("line_end", line_start))

    if definition_file and context_type in {"definition", "related"}:
        definition_snippet = _read_file_snippet(project_dir, definition_file, line_start, line_end, context_lines=5)
        if definition_snippet:
            result["snippets"].append(
                {
                    "type": "definition",
                    "file": definition_file,
                    "line_start": max(1, line_start - 5),
                    "line_end": line_end + 5,
                    "content": definition_snippet,
                }
            )

    relations = symbol_details.get("relations", {})

    if context_type in {"usage", "related"}:
        called_by = relations.get("called_by", [])
        if isinstance(called_by, list):
            usage_files = _find_symbol_files(index_dir, called_by[:max_files])
            for caller, file_info in list(usage_files.items())[: max_files - len(result["snippets"])]:
                snippet = _read_file_snippet(
                    project_dir,
                    file_info["file"],
                    file_info["line_start"],
                    file_info["line_end"],
                    context_lines=3,
                )
                if snippet:
                    result["snippets"].append(
                        {
                            "type": "usage",
                            "caller": caller,
                            "file": file_info["file"],
                            "line_start": file_info["line_start"],
                            "line_end": file_info["line_end"],
                            "content": snippet,
                        }
                    )

    if context_type == "related":
        calls = relations.get("calls", [])
        if isinstance(calls, list):
            callee_files = _find_symbol_files(index_dir, calls[:max_files])
            for callee, file_info in list(callee_files.items())[: max(2, max_files - len(result["snippets"]))]:
                snippet = _read_file_snippet(
                    project_dir,
                    file_info["file"],
                    file_info["line_start"],
                    file_info["line_end"],
                    context_lines=3,
                )
                if snippet:
                    result["snippets"].append(
                        {
                            "type": "callee",
                            "symbol": callee,
                            "file": file_info["file"],
                            "line_start": file_info["line_start"],
                            "line_end": file_info["line_end"],
                            "content": snippet,
                        }
                    )

    test_files = _find_test_files(index_dir, symbol_text, definition_file)
    for test_info in test_files[: max(1, max_files - len(result["snippets"]))]:
        snippet = _read_file_snippet(
            project_dir,
            test_info["file"],
            test_info["line_start"],
            test_info["line_end"],
            context_lines=3,
        )
        if snippet:
            result["snippets"].append(
                {
                    "type": "test",
                    "file": test_info["file"],
                    "line_start": test_info["line_start"],
                    "line_end": test_info["line_end"],
                    "content": snippet,
                }
            )

    result["snippet_count"] = len(result["snippets"])
    result["files_included"] = list({s["file"] for s in result["snippets"]})

    return result


def _read_file_snippet(
    project_dir: Path,
    rel_path: str,
    line_start: int,
    line_end: int,
    context_lines: int = 3,
) -> str:
    """Read a snippet from a file with context lines."""
    try:
        file_path = project_dir / rel_path
        if not file_path.exists():
            return ""

        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, line_start - 1 - context_lines)
        end = min(len(lines), line_end + context_lines)

        return "\n".join(lines[start:end])
    except (OSError, UnicodeDecodeError):
        return ""


def _find_symbol_files(
    index_dir: Path,
    symbol_names: list[str],
) -> dict[str, dict[str, Any]]:
    """Find file locations for a list of symbol names."""
    if not symbol_names:
        return {}

    all_symbols = load_index_rows(index_dir, kind="symbols", key_field="file")
    name_set = {s.lower() for s in symbol_names}

    result: dict[str, dict[str, Any]] = {}
    for row in all_symbols:
        symbol = str(row.get("symbol", "")).strip()
        qualified = str(row.get("qualified_name", "")).strip()

        if symbol.lower() in name_set or qualified.lower() in name_set:
            key = symbol if symbol.lower() in name_set else qualified
            if key not in result:
                result[key] = {
                    "file": _normalize_path(str(row.get("file", ""))),
                    "line_start": int(row.get("line_start", 1)),
                    "line_end": int(row.get("line_end", 1)),
                }

    return result


def _find_test_files(
    index_dir: Path,
    symbol_name: str,
    definition_file: str,
) -> list[dict[str, Any]]:
    """Find test files that might test a symbol."""
    all_symbols = load_index_rows(index_dir, kind="symbols", key_field="file")

    symbol_lower = symbol_name.lower()
    def_stem = Path(definition_file).stem.lower()

    tests: list[dict[str, Any]] = []

    for row in all_symbols:
        file_path = _normalize_path(str(row.get("file", ""))).lower()
        symbol = str(row.get("symbol", "")).strip().lower()

        is_test_file = (
            "test" in file_path
            or file_path.startswith("tests/")
            or "_test." in file_path
            or file_path.endswith("_test.py")
        )

        if not is_test_file:
            continue

        if symbol_lower in symbol or def_stem in file_path:
            tests.append(
                {
                    "file": _normalize_path(str(row.get("file", ""))),
                    "line_start": int(row.get("line_start", 1)),
                    "line_end": int(row.get("line_end", 1)),
                    "symbol": str(row.get("symbol", "")),
                }
            )

    seen_files: set[str] = set()
    unique_tests: list[dict[str, Any]] = []
    for test in tests:
        if test["file"] not in seen_files:
            seen_files.add(test["file"])
            unique_tests.append(test)

    return unique_tests[:5]
