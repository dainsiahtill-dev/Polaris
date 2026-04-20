from __future__ import annotations

from typing import Any

from .semantic_relations import compute_relation_proximity

# ---------------------------------------------------------------------------
# Scoring signal weights — must sum to ~1.0 (excluding changed_boost).
# Adjust these to tune file relevance ranking.
# ---------------------------------------------------------------------------
WEIGHT_SYMBOL_MATCH = 0.24
WEIGHT_REFERENCE_PROXIMITY = 0.20
WEIGHT_DEPENDENCY_IMPACT = 0.13
WEIGHT_SIGNATURE_MATCH = 0.12
WEIGHT_RELATION_PROXIMITY = 0.10
WEIGHT_TEST_RELEVANCE = 0.10
WEIGHT_STRUCTURAL_MATCH = 0.08
WEIGHT_SYNTAX_UNIT_COVERAGE = 0.03
CHANGED_FILE_BOOST = 0.12

# Normalization scales for hit-count → 0-1 conversion.
SCALE_SYMBOL = 1.0
SCALE_SIGNATURE = 1.8
SCALE_REFERENCE = 1.0
SCALE_DEPENDENCY = 1.0
SCALE_STRUCTURAL = 1.2

# Thresholds for coverage normalization.
SYNTAX_UNIT_COVERAGE_DIVISOR = 4.0
TEST_RELEVANCE_DIVISOR = 3.0


def _contains_any(text: str, tokens: list[str]) -> int:
    low = str(text or "").lower()
    return sum(1 for token in tokens if token in low)


def _collect_symbol_metadata(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("symbol", "")),
        str(row.get("qualified_name", "")),
        str(row.get("signature", "")),
        str(row.get("scope", "")),
        str(row.get("return_type", "")),
        str(row.get("kind", "")),
    ]
    for key in ("parameters", "decorators", "bases", "relation_targets", "attributes"):
        value = row.get(key, [])
        if isinstance(value, list):
            parts.extend(str(item) for item in value[:16])
    return " ".join(item for item in parts if item)


def _normalized_hits(hit_count: int, token_count: int, scale: float = 1.0) -> float:
    denominator = max(1.0, float(token_count) * max(0.2, float(scale)))
    return min(1.0, float(hit_count) / denominator)


def score_file(
    file_path: str,
    task_tokens: list[str],
    symbols_by_file: dict[str, list[dict[str, Any]]],
    references_by_file: dict[str, list[dict[str, Any]]],
    deps_by_file: dict[str, list[dict[str, Any]]],
    tests_by_file: dict[str, list[dict[str, Any]]],
    changed_file_set: set[str],
    relation_graph: dict[str, dict[str, float]] | None = None,
    relation_seed_files: list[str] | None = None,
    relation_weight: float = 1.0,
) -> dict[str, Any]:
    symbol_rows = symbols_by_file.get(file_path, [])
    ref_rows = references_by_file.get(file_path, [])
    dep_rows = deps_by_file.get(file_path, [])
    test_rows = tests_by_file.get(file_path, [])

    symbol_hits = 0
    signature_hits = 0
    relation_hits = 0
    syntax_unit_rows = 0
    for row in symbol_rows:
        symbol_hits += _contains_any(str(row.get("symbol", "")), task_tokens)
        symbol_hits += _contains_any(str(row.get("qualified_name", "")), task_tokens)
        signature_hits += _contains_any(_collect_symbol_metadata(row), task_tokens)
        relation_targets = row.get("relation_targets", [])
        if isinstance(relation_targets, list):
            relation_hits += _contains_any(" ".join(str(item) for item in relation_targets), task_tokens)
        line_start = int(row.get("line_start", 1) or 1)
        line_end = int(row.get("line_end", line_start) or line_start)
        if line_end > line_start:
            syntax_unit_rows += 1

    ref_hits = 0
    for row in ref_rows:
        ref_hits += _contains_any(str(row.get("target_symbol", "")), task_tokens)
        ref_hits += _contains_any(str(row.get("source_symbol", "")), task_tokens)

    dep_hits = 0
    for row in dep_rows:
        dep_hits += _contains_any(str(row.get("edge_to", "")), task_tokens)

    test_hits = len(test_rows)

    symbol_match = _normalized_hits(symbol_hits, len(task_tokens), scale=1.0)
    signature_match = _normalized_hits(signature_hits, len(task_tokens), scale=1.8)
    reference_proximity = _normalized_hits(ref_hits, len(task_tokens), scale=1.0)
    dependency_impact = _normalized_hits(dep_hits, len(task_tokens), scale=1.0)
    structural_match = _normalized_hits(relation_hits, len(task_tokens), scale=1.2)
    syntax_unit_coverage = min(1.0, float(syntax_unit_rows) / 4.0)
    test_relevance = min(1.0, test_hits / 3.0)
    relation_proximity_raw = (
        compute_relation_proximity(
            file_path=file_path,
            relation_graph=relation_graph or {},
            seed_files=relation_seed_files or [],
        )
        if relation_graph and relation_seed_files
        else 0.0
    )
    relation_proximity = min(
        1.0,
        max(0.0, float(relation_proximity_raw) * max(0.0, min(1.0, float(relation_weight)))),
    )

    changed_boost = 0.12 if file_path in changed_file_set else 0.0
    score = (
        0.24 * symbol_match
        + 0.20 * reference_proximity
        + 0.13 * dependency_impact
        + 0.10 * test_relevance
        + 0.12 * signature_match
        + 0.08 * structural_match
        + 0.03 * syntax_unit_coverage
        + 0.10 * relation_proximity
        + changed_boost
    )

    reasons: list[str] = []
    if symbol_match > 0:
        reasons.append("symbol_match")
    if signature_match > 0:
        reasons.append("signature_match")
    if reference_proximity > 0:
        reasons.append("reference_proximity")
    if dependency_impact > 0:
        reasons.append("dependency_impact")
    if structural_match > 0:
        reasons.append("structural_match")
    if syntax_unit_coverage > 0:
        reasons.append("syntax_unit_coverage")
    if relation_proximity > 0:
        reasons.append("relation_proximity")
    if test_relevance > 0:
        reasons.append("test_relevance")
    if file_path in changed_file_set:
        reasons.append("changed_file")
    if not reasons:
        reasons.append("baseline")

    return {
        "path": file_path,
        "score": round(score, 6),
        "reasons": reasons,
        "signals": [
            {"signal_name": "symbol_match", "score": round(symbol_match, 6)},
            {"signal_name": "signature_match", "score": round(signature_match, 6)},
            {"signal_name": "reference_proximity", "score": round(reference_proximity, 6)},
            {"signal_name": "dependency_impact", "score": round(dependency_impact, 6)},
            {"signal_name": "structural_match", "score": round(structural_match, 6)},
            {"signal_name": "syntax_unit_coverage", "score": round(syntax_unit_coverage, 6)},
            {"signal_name": "relation_proximity", "score": round(relation_proximity, 6)},
            {"signal_name": "test_relevance", "score": round(test_relevance, 6)},
            {"signal_name": "changed_boost", "score": round(changed_boost, 6)},
        ],
    }
