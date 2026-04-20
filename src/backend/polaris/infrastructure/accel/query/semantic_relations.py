from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.$]{1,127}")


from ..utils import normalize_path_str as _normalize_path


def _normalize_module_name(name: str) -> str:
    normalized = str(name or "").strip().replace("\\", "/")
    while normalized.startswith("."):
        normalized = normalized[1:]
    return normalized.replace("/", ".").strip(".")


def _symbol_tokens(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("symbol", "qualified_name"):
        raw = str(row.get(key, "")).strip()
        if raw:
            values.append(raw)
            values.extend(_TOKEN_RE.findall(raw))
    relation_targets = row.get("relation_targets", [])
    if isinstance(relation_targets, list):
        for item in relation_targets[:24]:
            text = str(item).strip()
            if text:
                values.append(text)
                values.extend(_TOKEN_RE.findall(text))
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token:
            continue
        token_low = token.lower()
        if token_low in seen:
            continue
        seen.add(token_low)
        deduped.append(token)
    return deduped


def _build_symbol_to_files(symbols_by_file: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    symbol_to_files: dict[str, set[str]] = defaultdict(set)
    for raw_path, rows in symbols_by_file.items():
        file_path = _normalize_path(raw_path)
        if not file_path:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for token in _symbol_tokens(row):
                symbol_to_files[token.lower()].add(file_path)
    return symbol_to_files


def _build_module_to_files(indexed_files: list[str]) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = defaultdict(set)
    for raw_path in indexed_files:
        rel_path = _normalize_path(raw_path)
        if not rel_path:
            continue
        path = Path(rel_path)
        suffix = path.suffix.lower()
        if suffix not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            continue
        parts = list(path.with_suffix("").parts)
        if not parts:
            continue
        dotted = ".".join(parts)
        mapping[dotted.lower()].add(rel_path)
        mapping[path.stem.lower()].add(rel_path)
        if path.stem == "__init__" and len(parts) > 1:
            package_name = ".".join(parts[:-1])
            mapping[package_name.lower()].add(rel_path)
    return mapping


def _iter_row_candidates(
    row: dict[str, Any],
    *,
    symbol_to_files: dict[str, set[str]],
    module_to_files: dict[str, set[str]],
) -> set[str]:
    targets: set[str] = set()
    for key in ("target_symbol", "edge_to"):
        raw = str(row.get(key, "")).strip()
        if not raw:
            continue
        normalized_module = _normalize_module_name(raw).lower()
        for token in {raw.lower(), normalized_module}:
            targets.update(symbol_to_files.get(token, set()))
            targets.update(module_to_files.get(token, set()))
        for token in _TOKEN_RE.findall(raw):
            token_low = token.lower()
            targets.update(symbol_to_files.get(token_low, set()))
            targets.update(module_to_files.get(token_low, set()))
    return targets


def build_semantic_relation_graph(
    *,
    symbols_by_file: dict[str, list[dict[str, Any]]],
    references_by_file: dict[str, list[dict[str, Any]]],
    deps_by_file: dict[str, list[dict[str, Any]]],
    indexed_files: list[str],
) -> dict[str, dict[str, float]]:
    symbol_to_files = _build_symbol_to_files(symbols_by_file)
    module_to_files = _build_module_to_files(indexed_files)
    weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for raw_source, rows in references_by_file.items():
        source = _normalize_path(raw_source)
        if not source:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for target in _iter_row_candidates(row, symbol_to_files=symbol_to_files, module_to_files=module_to_files):
                if target and target != source:
                    weights[source][target] += 1.0

    for raw_source, rows in deps_by_file.items():
        source = _normalize_path(raw_source)
        if not source:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for target in _iter_row_candidates(row, symbol_to_files=symbol_to_files, module_to_files=module_to_files):
                if target and target != source:
                    weights[source][target] += 0.8

    for raw_source, rows in symbols_by_file.items():
        source = _normalize_path(raw_source)
        if not source:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            relation_targets = row.get("relation_targets", [])
            if not isinstance(relation_targets, list):
                continue
            for item in relation_targets[:24]:
                token = str(item).strip()
                if not token:
                    continue
                for target in symbol_to_files.get(token.lower(), set()):
                    if target and target != source:
                        weights[source][target] += 0.7
                normalized_module = _normalize_module_name(token).lower()
                for target in module_to_files.get(normalized_module, set()):
                    if target and target != source:
                        weights[source][target] += 0.6

    graph: dict[str, dict[str, float]] = {}
    for source, target_weights in weights.items():
        if not target_weights:
            continue
        max_weight = max(float(value) for value in target_weights.values())
        if max_weight <= 0.0:
            continue
        graph[source] = {
            target: round(min(1.0, float(weight) / max_weight), 6)
            for target, weight in target_weights.items()
            if float(weight) > 0.0
        }
    return graph


def normalize_changed_files(changed_files: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in changed_files:
        normalized = _normalize_path(item)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def collect_seed_files(
    *,
    changed_files: list[str],
    hints: list[str] | None,
    candidate_files: list[str],
) -> list[str]:
    candidate_norm = [_normalize_path(item) for item in candidate_files if item]
    candidate_set = {item.lower() for item in candidate_norm if item}
    seeds = normalize_changed_files(changed_files)
    hint_values = [str(item).strip().lower() for item in (hints or []) if str(item).strip()]
    if hint_values:
        for path in candidate_norm:
            path_low = path.lower()
            if path_low in candidate_set and any(token in path_low for token in hint_values):
                seeds.append(path)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in seeds:
        key = item.lower()
        if key in seen:
            continue
        if key not in candidate_set:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def compute_relation_proximity(
    *,
    file_path: str,
    relation_graph: dict[str, dict[str, float]],
    seed_files: list[str],
) -> float:
    if not seed_files:
        return 0.0
    node = _normalize_path(file_path)
    if not node:
        return 0.0
    node_edges = relation_graph.get(node, {})
    direct = max((float(node_edges.get(seed, 0.0)) for seed in seed_files), default=0.0)
    reverse = max(
        (float(relation_graph.get(seed, {}).get(node, 0.0)) for seed in seed_files),
        default=0.0,
    )
    return round(min(1.0, (0.7 * direct) + (0.3 * reverse)), 6)


def expand_candidates_with_relations(
    *,
    candidates: list[str],
    relation_graph: dict[str, dict[str, float]],
    seed_files: list[str],
    changed_files: list[str],
) -> list[str]:
    if not candidates:
        return []
    if not relation_graph or not seed_files:
        return list(candidates)
    changed = {item.lower() for item in normalize_changed_files(changed_files)}
    index_map = {path: idx for idx, path in enumerate(candidates)}
    pinned = [path for path in candidates if path.lower() in changed]
    rest = [path for path in candidates if path.lower() not in changed]
    rest.sort(
        key=lambda path: (
            -compute_relation_proximity(
                file_path=path,
                relation_graph=relation_graph,
                seed_files=seed_files,
            ),
            index_map.get(path, 10**9),
        )
    )
    return pinned + rest


def relation_graph_stats(graph: dict[str, dict[str, float]]) -> dict[str, int]:
    node_count = len(graph)
    edge_count = int(sum(len(targets) for targets in graph.values()))
    return {
        "nodes": node_count,
        "edges": edge_count,
    }
