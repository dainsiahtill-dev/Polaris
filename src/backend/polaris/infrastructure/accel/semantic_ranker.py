from __future__ import annotations

from typing import Any

# Keep legacy tokens for backward-compatible config parsing.
_SEMANTIC_PROVIDERS = {"off", "auto", "flagembedding"}


def normalize_semantic_provider(value: Any, default_value: str = "off") -> str:
    token = str(value or default_value).strip().lower()
    if token in _SEMANTIC_PROVIDERS:
        return token
    fallback = str(default_value or "off").strip().lower()
    return fallback if fallback in _SEMANTIC_PROVIDERS else "off"


def clamp_ratio(value: Any, default_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default_value)
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return float(parsed)


def probe_semantic_runtime(config: dict[str, Any]) -> dict[str, Any]:
    runtime_cfg = dict(config.get("runtime", {}))
    requested_provider = normalize_semantic_provider(
        runtime_cfg.get("semantic_ranker_provider", "off"),
        "off",
    )
    requested_enabled = bool(runtime_cfg.get("semantic_ranker_enabled", False))
    return {
        "enabled": bool(requested_enabled),
        "provider_requested": requested_provider,
        "provider_resolved": "off",
        "flagembedding_available": False,
        "reason": "removed_from_build",
        "embedding_model_path": "",
        "reranker_model_path": "",
        "embedding_model_exists": False,
        "reranker_model_exists": False,
    }


def apply_semantic_ranking(
    *,
    ranked: list[dict[str, Any]],
    config: dict[str, Any],
    task: str,
    task_tokens: list[str],
    hints: list[str] | None,
    symbols_by_file: dict[str, list[dict[str, Any]]],
    references_by_file: dict[str, list[dict[str, Any]]],
    deps_by_file: dict[str, list[dict[str, Any]]],
    tests_by_file: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    del task, task_tokens, hints, symbols_by_file, references_by_file, deps_by_file, tests_by_file
    probe = probe_semantic_runtime(config)
    metadata: dict[str, Any] = {
        "enabled": bool(probe.get("enabled", False)),
        "provider_requested": str(probe.get("provider_requested", "off")),
        "provider_resolved": "off",
        "reason": "removed_from_build",
        "applied": False,
        "embedding_applied": False,
        "reranker_applied": False,
        "candidate_count": 0,
        "device": "cpu",
    }
    return ranked, metadata
