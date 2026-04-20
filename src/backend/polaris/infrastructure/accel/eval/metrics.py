from __future__ import annotations

from statistics import fmean


def _normalize_paths(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item or "").replace("\\", "/").strip().lower()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def recall_at_k(expected_files: list[str], predicted_files: list[str], k: int) -> float:
    expected = _normalize_paths(expected_files)
    if not expected:
        return 0.0
    predicted = _normalize_paths(predicted_files)[: max(1, int(k))]
    hit_count = sum(1 for item in expected if item in set(predicted))
    return round(float(hit_count) / float(len(expected)), 6)


def reciprocal_rank(expected_files: list[str], predicted_files: list[str]) -> float:
    expected = set(_normalize_paths(expected_files))
    if not expected:
        return 0.0
    ranked = _normalize_paths(predicted_files)
    for idx, item in enumerate(ranked, start=1):
        if item in expected:
            return round(1.0 / float(idx), 6)
    return 0.0


def symbol_hit_rate(expected_symbols: list[str], observed_symbols: list[str]) -> float:
    expected = {str(item or "").strip().lower() for item in expected_symbols if str(item or "").strip()}
    if not expected:
        return 0.0
    observed = {str(item or "").strip().lower() for item in observed_symbols if str(item or "").strip()}
    hits = sum(1 for item in expected if item in observed)
    return round(float(hits) / float(len(expected)), 6)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(float(fmean(values)), 6)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = round(0.95 * (len(ordered) - 1))
    return round(float(ordered[index]), 6)


def aggregate_case_metrics(case_metrics: list[dict[str, float]]) -> dict[str, float]:
    recalls_5 = [float(item.get("recall_at_5", 0.0)) for item in case_metrics]
    recalls_10 = [float(item.get("recall_at_10", 0.0)) for item in case_metrics]
    mrr_values = [float(item.get("mrr", 0.0)) for item in case_metrics]
    context_chars = [float(item.get("context_chars", 0.0)) for item in case_metrics]
    latency_ms = [float(item.get("latency_ms", 0.0)) for item in case_metrics]
    symbol_hits = [float(item.get("symbol_hit_rate", 0.0)) for item in case_metrics]
    return {
        "case_count": float(len(case_metrics)),
        "recall_at_5": _mean(recalls_5),
        "recall_at_10": _mean(recalls_10),
        "mrr": _mean(mrr_values),
        "symbol_hit_rate": _mean(symbol_hits),
        "avg_context_chars": _mean(context_chars),
        "p95_context_chars": _p95(context_chars),
        "avg_latency_ms": _mean(latency_ms),
        "p95_latency_ms": _p95(latency_ms),
    }
