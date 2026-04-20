from __future__ import annotations

from typing import Any

_INTENT_KEYWORDS: dict[str, set[str]] = {
    "bug_fix": {
        "fix",
        "bug",
        "error",
        "crash",
        "fail",
        "failing",
        "regression",
        "incident",
        "broken",
    },
    "feature_add": {
        "add",
        "implement",
        "support",
        "introduce",
        "extend",
        "new",
        "feature",
    },
    "refactor": {
        "refactor",
        "cleanup",
        "simplify",
        "rename",
        "restructure",
        "optimize",
    },
    "documentation": {
        "doc",
        "docs",
        "readme",
        "comment",
        "explain",
        "guide",
    },
    "test_focus": {
        "test",
        "tests",
        "coverage",
        "assert",
        "pytest",
    },
}

_INTENT_FACTORS: dict[str, dict[str, float]] = {
    "bug_fix": {
        "max_chars": 0.88,
        "top_n_files": 0.9,
        "snippet_radius": 0.88,
        "max_snippets": 0.92,
    },
    "feature_add": {
        "max_chars": 1.1,
        "top_n_files": 1.12,
        "snippet_radius": 1.0,
        "max_snippets": 1.05,
    },
    "refactor": {
        "max_chars": 1.24,
        "top_n_files": 1.2,
        "snippet_radius": 1.22,
        "max_snippets": 1.18,
    },
    "documentation": {
        "max_chars": 0.72,
        "top_n_files": 0.76,
        "snippet_radius": 0.72,
        "max_snippets": 0.8,
    },
    "test_focus": {
        "max_chars": 0.92,
        "top_n_files": 0.95,
        "snippet_radius": 0.9,
        "max_snippets": 1.0,
    },
    "general": {
        "max_chars": 1.0,
        "top_n_files": 1.0,
        "snippet_radius": 1.0,
        "max_snippets": 1.0,
    },
}


def _safe_int(value: Any, default_value: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default_value)


def _safe_float(value: Any, default_value: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default_value)


def _clamp(value: float, *, low: float, high: float) -> float:
    return max(float(low), min(float(high), float(value)))


def classify_task_intent(task: str, task_tokens: list[str]) -> str:
    tokens = {str(token).lower() for token in task_tokens if str(token).strip()}
    task_text = str(task or "").lower()
    best_intent = "general"
    best_score = 0
    for intent, keywords in _INTENT_KEYWORDS.items():
        overlap = sum(1 for keyword in keywords if keyword in tokens or keyword in task_text)
        if overlap > best_score:
            best_score = overlap
            best_intent = intent
    return best_intent


def _complexity_factor(
    *,
    task_tokens: list[str],
    changed_files: list[str],
    ranked_files: list[dict[str, Any]],
) -> float:
    token_complexity = min(1.0, float(len(task_tokens)) / 24.0)
    change_complexity = min(1.0, float(len(changed_files)) / 12.0)
    score_spread = 0.0
    if ranked_files:
        sample = ranked_files[: min(12, len(ranked_files))]
        scores = [float(item.get("score", 0.0) or 0.0) for item in sample]
        if scores:
            score_spread = min(1.0, max(scores) - min(scores))
    return _clamp(
        (0.45 * token_complexity) + (0.4 * change_complexity) + (0.15 * score_spread),
        low=0.0,
        high=1.0,
    )


def resolve_adaptive_budget(
    *,
    context_cfg: dict[str, Any],
    runtime_cfg: dict[str, Any],
    task: str,
    task_tokens: list[str],
    changed_files: list[str],
    ranked_files: list[dict[str, Any]],
    budget_override: dict[str, int] | None,
) -> tuple[dict[str, int], dict[str, Any]]:
    base_budget = {
        "max_chars": max(1, _safe_int(context_cfg.get("max_chars", 24000), 24000)),
        "max_snippets": max(1, _safe_int(context_cfg.get("max_snippets", 60), 60)),
        "top_n_files": max(1, _safe_int(context_cfg.get("top_n_files", 12), 12)),
        "snippet_radius": max(1, _safe_int(context_cfg.get("snippet_radius", 40), 40)),
    }
    override = dict(budget_override or {})
    override_keys = {key for key in ("max_chars", "max_snippets", "top_n_files", "snippet_radius") if key in override}

    adaptive_enabled = bool(runtime_cfg.get("adaptive_budget_enabled", True))
    min_factor = _safe_float(runtime_cfg.get("adaptive_budget_min_factor", 0.65), 0.65)
    max_factor = _safe_float(runtime_cfg.get("adaptive_budget_max_factor", 1.45), 1.45)
    max_factor = max(max_factor, min_factor)

    intent = classify_task_intent(task, task_tokens)
    intent_factors = dict(_INTENT_FACTORS.get(intent, _INTENT_FACTORS["general"]))
    complexity = _complexity_factor(
        task_tokens=task_tokens,
        changed_files=changed_files,
        ranked_files=ranked_files,
    )
    complexity_scale = 0.75 + (0.55 * complexity)

    effective = dict(base_budget)
    factor_by_key: dict[str, float] = {}
    if adaptive_enabled:
        for key, base_value in base_budget.items():
            if key in override_keys:
                continue
            intent_factor = float(intent_factors.get(key, 1.0))
            combined_factor = _clamp(
                intent_factor * complexity_scale,
                low=min_factor,
                high=max_factor,
            )
            factor_by_key[key] = round(combined_factor, 4)
            effective[key] = max(1, round(float(base_value) * combined_factor))
    else:
        factor_by_key = dict.fromkeys(base_budget, 1.0)

    for key in override_keys:
        effective[key] = max(1, _safe_int(override.get(key), effective[key]))

    meta = {
        "adaptive_enabled": adaptive_enabled,
        "intent": intent,
        "complexity_factor": round(complexity, 4),
        "complexity_scale": round(complexity_scale, 4),
        "min_factor": round(min_factor, 4),
        "max_factor": round(max_factor, 4),
        "factors": factor_by_key,
        "override_keys": sorted(override_keys),
        "base_budget": base_budget,
    }
    return effective, meta
