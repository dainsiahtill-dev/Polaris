from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .language_profiles import (
    resolve_language_profile_registry,
    resolve_selected_language_profiles,
)
from .polaris_paths import default_accel_runtime_home
from .semantic_ranker import clamp_ratio, normalize_semantic_provider

_SYNTAX_PROVIDERS = {"off", "auto", "tree_sitter"}
_LEXICAL_PROVIDERS = {"off", "auto", "tantivy"}


def _cpu_count() -> int:
    return max(1, int(os.cpu_count() or 1))


def _default_max_workers() -> int:
    return max(1, min(12, _cpu_count()))


def _default_index_workers() -> int:
    return max(1, min(96, _cpu_count()))


def default_accel_home(project_dir: Path | None = None) -> Path:
    return default_accel_runtime_home(project_dir)


def _normalize_max_workers(value: Any, default_value: int) -> int:
    if str(value or "").strip().lower() == "auto":
        return max(1, int(default_value))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default_value
    return max(1, parsed)


def _normalize_positive_int(value: Any, default_value: int) -> int:
    if str(value or "").strip().lower() == "auto":
        return max(1, int(default_value))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default_value
    return max(1, parsed)


def _normalize_positive_float(value: Any, default_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default_value)
    if parsed <= 0:
        return float(default_value)
    return float(parsed)


def _normalize_ratio(value: Any, default_value: float) -> float:
    return clamp_ratio(value, default_value)


def _normalize_bool(value: Any, default_value: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default_value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def _normalize_timeout_action(value: Any, default_value: str = "poll") -> str:
    token = str(value or default_value).strip().lower()
    if token in {"poll", "cancel"}:
        return token
    fallback = str(default_value or "poll").strip().lower()
    return fallback if fallback in {"poll", "cancel"} else "poll"


def _normalize_context_timeout_action(value: Any, default_value: str = "fallback_async") -> str:
    token = str(value or default_value).strip().lower()
    if token == "poll":
        token = "fallback_async"
    if token in {"fallback_async", "cancel"}:
        return token
    fallback = str(default_value or "fallback_async").strip().lower()
    if fallback == "poll":
        fallback = "fallback_async"
    return fallback if fallback in {"fallback_async", "cancel"} else "fallback_async"


def _normalize_constraint_mode(value: Any, default_value: str = "warn") -> str:
    token = str(value or default_value).strip().lower()
    if token in {"enforce", "error", "errors"}:
        token = "strict"
    elif token in {"on", "default"}:
        token = "warn"
    if token in {"off", "warn", "strict"}:
        return token
    fallback = str(default_value or "warn").strip().lower()
    if fallback in {"enforce", "error", "errors"}:
        fallback = "strict"
    elif fallback in {"on", "default"}:
        fallback = "warn"
    return fallback if fallback in {"off", "warn", "strict"} else "warn"


def normalize_syntax_provider(value: Any, default_value: str = "off") -> str:
    token = str(value or default_value).strip().lower()
    if token in _SYNTAX_PROVIDERS:
        return token
    fallback = str(default_value or "off").strip().lower()
    return fallback if fallback in _SYNTAX_PROVIDERS else "off"


def normalize_lexical_provider(value: Any, default_value: str = "off") -> str:
    token = str(value or default_value).strip().lower()
    if token in _LEXICAL_PROVIDERS:
        return token
    fallback = str(default_value or "off").strip().lower()
    return fallback if fallback in _LEXICAL_PROVIDERS else "off"


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    runtime = dict(config.get("runtime", {}))

    if os.environ.get("ACCEL_HOME"):
        runtime["accel_home"] = os.environ["ACCEL_HOME"]
    if os.environ.get("ACCEL_MAX_WORKERS"):
        max_workers_default = _normalize_max_workers(
            runtime.get("max_workers", _default_max_workers()),
            _default_max_workers(),
        )
        runtime["max_workers"] = _normalize_max_workers(os.environ["ACCEL_MAX_WORKERS"], max_workers_default)
    if os.environ.get("ACCEL_VERIFY_WORKERS"):
        verify_workers_default = _normalize_positive_int(
            runtime.get("verify_workers", runtime.get("max_workers", _default_max_workers())),
            _default_max_workers(),
        )
        runtime["verify_workers"] = _normalize_positive_int(
            os.environ["ACCEL_VERIFY_WORKERS"],
            verify_workers_default,
        )
    if os.environ.get("ACCEL_INDEX_WORKERS"):
        index_workers_default = _normalize_positive_int(
            runtime.get("index_workers", _default_index_workers()),
            _default_index_workers(),
        )
        runtime["index_workers"] = _normalize_positive_int(os.environ["ACCEL_INDEX_WORKERS"], index_workers_default)
    if os.environ.get("ACCEL_INDEX_COMPACT_EVERY"):
        runtime["index_delta_compact_every"] = _normalize_positive_int(
            os.environ["ACCEL_INDEX_COMPACT_EVERY"],
            int(runtime.get("index_delta_compact_every", 200)),
        )
    if os.environ.get("ACCEL_VERIFY_MAX_TARGET_TESTS"):
        runtime["verify_max_target_tests"] = _normalize_positive_int(
            os.environ["ACCEL_VERIFY_MAX_TARGET_TESTS"],
            int(runtime.get("verify_max_target_tests", 64)),
        )
    if os.environ.get("ACCEL_VERIFY_PYTEST_SHARD_SIZE"):
        runtime["verify_pytest_shard_size"] = _normalize_positive_int(
            os.environ["ACCEL_VERIFY_PYTEST_SHARD_SIZE"],
            int(runtime.get("verify_pytest_shard_size", 16)),
        )
    if os.environ.get("ACCEL_VERIFY_PYTEST_MAX_SHARDS"):
        runtime["verify_pytest_max_shards"] = _normalize_positive_int(
            os.environ["ACCEL_VERIFY_PYTEST_MAX_SHARDS"],
            int(runtime.get("verify_pytest_max_shards", 6)),
        )
    if os.environ.get("ACCEL_VERIFY_FAIL_FAST") is not None:
        runtime["verify_fail_fast"] = _normalize_bool(
            os.environ["ACCEL_VERIFY_FAIL_FAST"],
            bool(runtime.get("verify_fail_fast", False)),
        )
    if os.environ.get("ACCEL_VERIFY_CACHE_ENABLED") is not None:
        runtime["verify_cache_enabled"] = _normalize_bool(
            os.environ["ACCEL_VERIFY_CACHE_ENABLED"],
            bool(runtime.get("verify_cache_enabled", True)),
        )
    if os.environ.get("ACCEL_VERIFY_CACHE_FAILED_RESULTS") is not None:
        runtime["verify_cache_failed_results"] = _normalize_bool(
            os.environ["ACCEL_VERIFY_CACHE_FAILED_RESULTS"],
            bool(runtime.get("verify_cache_failed_results", False)),
        )
    if os.environ.get("ACCEL_VERIFY_CACHE_TTL_SECONDS"):
        runtime["verify_cache_ttl_seconds"] = _normalize_positive_int(
            os.environ["ACCEL_VERIFY_CACHE_TTL_SECONDS"],
            int(runtime.get("verify_cache_ttl_seconds", 900)),
        )
    if os.environ.get("ACCEL_VERIFY_CACHE_FAILED_TTL_SECONDS"):
        runtime["verify_cache_failed_ttl_seconds"] = _normalize_positive_int(
            os.environ["ACCEL_VERIFY_CACHE_FAILED_TTL_SECONDS"],
            int(runtime.get("verify_cache_failed_ttl_seconds", 120)),
        )
    if os.environ.get("ACCEL_VERIFY_CACHE_MAX_ENTRIES"):
        runtime["verify_cache_max_entries"] = _normalize_positive_int(
            os.environ["ACCEL_VERIFY_CACHE_MAX_ENTRIES"],
            int(runtime.get("verify_cache_max_entries", 400)),
        )
    if os.environ.get("ACCEL_VERIFY_WORKSPACE_ROUTING_ENABLED") is not None:
        runtime["verify_workspace_routing_enabled"] = _normalize_bool(
            os.environ["ACCEL_VERIFY_WORKSPACE_ROUTING_ENABLED"],
            bool(runtime.get("verify_workspace_routing_enabled", True)),
        )
    if os.environ.get("ACCEL_VERIFY_PREFLIGHT_ENABLED") is not None:
        runtime["verify_preflight_enabled"] = _normalize_bool(
            os.environ["ACCEL_VERIFY_PREFLIGHT_ENABLED"],
            bool(runtime.get("verify_preflight_enabled", True)),
        )
    if os.environ.get("ACCEL_VERIFY_PREFLIGHT_TIMEOUT_SECONDS"):
        runtime["verify_preflight_timeout_seconds"] = _normalize_positive_int(
            os.environ["ACCEL_VERIFY_PREFLIGHT_TIMEOUT_SECONDS"],
            int(runtime.get("verify_preflight_timeout_seconds", 5)),
        )
    if os.environ.get("ACCEL_VERIFY_STALL_TIMEOUT_SECONDS"):
        runtime["verify_stall_timeout_seconds"] = _normalize_positive_float(
            os.environ["ACCEL_VERIFY_STALL_TIMEOUT_SECONDS"],
            float(runtime.get("verify_stall_timeout_seconds", 20.0)),
        )
    if os.environ.get("ACCEL_VERIFY_AUTO_CANCEL_ON_STALL") is not None:
        runtime["verify_auto_cancel_on_stall"] = _normalize_bool(
            os.environ["ACCEL_VERIFY_AUTO_CANCEL_ON_STALL"],
            bool(runtime.get("verify_auto_cancel_on_stall", False)),
        )
    if os.environ.get("ACCEL_VERIFY_MAX_WALL_TIME_SECONDS"):
        default_wall_time = _normalize_positive_float(
            runtime.get(
                "verify_max_wall_time_seconds",
                runtime.get("total_verify_timeout_seconds", 3600.0),
            ),
            3600.0,
        )
        runtime["verify_max_wall_time_seconds"] = _normalize_positive_float(
            os.environ["ACCEL_VERIFY_MAX_WALL_TIME_SECONDS"],
            default_wall_time,
        )
    if os.environ.get("ACCEL_CONTEXT_RPC_TIMEOUT_SECONDS"):
        runtime["context_rpc_timeout_seconds"] = _normalize_positive_float(
            os.environ["ACCEL_CONTEXT_RPC_TIMEOUT_SECONDS"],
            float(runtime.get("context_rpc_timeout_seconds", 300.0)),
        )
    if os.environ.get("ACCEL_SYNC_VERIFY_WAIT_SECONDS"):
        runtime["sync_verify_wait_seconds"] = _normalize_positive_float(
            os.environ["ACCEL_SYNC_VERIFY_WAIT_SECONDS"],
            float(runtime.get("sync_verify_wait_seconds", 45.0)),
        )
    if os.environ.get("ACCEL_SYNC_INDEX_WAIT_SECONDS"):
        runtime["sync_index_wait_seconds"] = _normalize_positive_float(
            os.environ["ACCEL_SYNC_INDEX_WAIT_SECONDS"],
            float(runtime.get("sync_index_wait_seconds", 45.0)),
        )
    if os.environ.get("ACCEL_SYNC_CONTEXT_WAIT_SECONDS"):
        runtime["sync_context_wait_seconds"] = _normalize_positive_float(
            os.environ["ACCEL_SYNC_CONTEXT_WAIT_SECONDS"],
            float(runtime.get("sync_context_wait_seconds", 45.0)),
        )
    if os.environ.get("ACCEL_TOKEN_ESTIMATOR_BACKEND"):
        runtime["token_estimator_backend"] = str(os.environ["ACCEL_TOKEN_ESTIMATOR_BACKEND"]).strip().lower()
    if os.environ.get("ACCEL_TOKEN_ESTIMATOR_ENCODING"):
        runtime["token_estimator_encoding"] = str(os.environ["ACCEL_TOKEN_ESTIMATOR_ENCODING"]).strip()
    if os.environ.get("ACCEL_TOKEN_ESTIMATOR_MODEL"):
        runtime["token_estimator_model"] = str(os.environ["ACCEL_TOKEN_ESTIMATOR_MODEL"]).strip()
    if os.environ.get("ACCEL_TOKEN_ESTIMATOR_CALIBRATION"):
        runtime["token_estimator_calibration"] = _normalize_positive_float(
            os.environ["ACCEL_TOKEN_ESTIMATOR_CALIBRATION"],
            float(runtime.get("token_estimator_calibration", 1.0)),
        )
    if os.environ.get("ACCEL_TOKEN_ESTIMATOR_FALLBACK_CHARS_PER_TOKEN"):
        runtime["token_estimator_fallback_chars_per_token"] = _normalize_positive_float(
            os.environ["ACCEL_TOKEN_ESTIMATOR_FALLBACK_CHARS_PER_TOKEN"],
            float(runtime.get("token_estimator_fallback_chars_per_token", 4.0)),
        )
    if os.environ.get("ACCEL_CONTEXT_REQUIRE_CHANGED_FILES") is not None:
        runtime["context_require_changed_files"] = _normalize_bool(
            os.environ["ACCEL_CONTEXT_REQUIRE_CHANGED_FILES"],
            bool(runtime.get("context_require_changed_files", False)),
        )
    if os.environ.get("ACCEL_SEMANTIC_CACHE_ENABLED") is not None:
        runtime["semantic_cache_enabled"] = _normalize_bool(
            os.environ["ACCEL_SEMANTIC_CACHE_ENABLED"],
            bool(runtime.get("semantic_cache_enabled", True)),
        )
    if os.environ.get("ACCEL_SEMANTIC_CACHE_MODE"):
        runtime["semantic_cache_mode"] = str(os.environ["ACCEL_SEMANTIC_CACHE_MODE"]).strip().lower()
    if os.environ.get("ACCEL_SEMANTIC_CACHE_TTL_SECONDS"):
        runtime["semantic_cache_ttl_seconds"] = _normalize_positive_int(
            os.environ["ACCEL_SEMANTIC_CACHE_TTL_SECONDS"],
            int(runtime.get("semantic_cache_ttl_seconds", 7200)),
        )
    if os.environ.get("ACCEL_SEMANTIC_CACHE_HYBRID_THRESHOLD"):
        runtime["semantic_cache_hybrid_threshold"] = _normalize_positive_float(
            os.environ["ACCEL_SEMANTIC_CACHE_HYBRID_THRESHOLD"],
            float(runtime.get("semantic_cache_hybrid_threshold", 0.86)),
        )
    if os.environ.get("ACCEL_SEMANTIC_CACHE_MAX_ENTRIES"):
        runtime["semantic_cache_max_entries"] = _normalize_positive_int(
            os.environ["ACCEL_SEMANTIC_CACHE_MAX_ENTRIES"],
            int(runtime.get("semantic_cache_max_entries", 800)),
        )
    if os.environ.get("ACCEL_SYNTAX_PARSER_ENABLED") is not None:
        runtime["syntax_parser_enabled"] = _normalize_bool(
            os.environ["ACCEL_SYNTAX_PARSER_ENABLED"],
            bool(runtime.get("syntax_parser_enabled", True)),
        )
    if os.environ.get("ACCEL_SYNTAX_PARSER_PROVIDER"):
        runtime["syntax_parser_provider"] = normalize_syntax_provider(
            os.environ["ACCEL_SYNTAX_PARSER_PROVIDER"],
            str(runtime.get("syntax_parser_provider", "auto")),
        )
    if os.environ.get("ACCEL_LEXICAL_RANKER_ENABLED") is not None:
        runtime["lexical_ranker_enabled"] = _normalize_bool(
            os.environ["ACCEL_LEXICAL_RANKER_ENABLED"],
            bool(runtime.get("lexical_ranker_enabled", True)),
        )
    if os.environ.get("ACCEL_LEXICAL_RANKER_PROVIDER"):
        runtime["lexical_ranker_provider"] = normalize_lexical_provider(
            os.environ["ACCEL_LEXICAL_RANKER_PROVIDER"],
            str(runtime.get("lexical_ranker_provider", "auto")),
        )
    if os.environ.get("ACCEL_LEXICAL_RANKER_MAX_CANDIDATES"):
        runtime["lexical_ranker_max_candidates"] = _normalize_positive_int(
            os.environ["ACCEL_LEXICAL_RANKER_MAX_CANDIDATES"],
            int(runtime.get("lexical_ranker_max_candidates", 200)),
        )
    if os.environ.get("ACCEL_LEXICAL_RANKER_WEIGHT"):
        runtime["lexical_ranker_weight"] = _normalize_ratio(
            os.environ["ACCEL_LEXICAL_RANKER_WEIGHT"],
            float(runtime.get("lexical_ranker_weight", 0.2)),
        )
    if os.environ.get("ACCEL_RELATION_RANKING_ENABLED") is not None:
        runtime["relation_ranking_enabled"] = _normalize_bool(
            os.environ["ACCEL_RELATION_RANKING_ENABLED"],
            bool(runtime.get("relation_ranking_enabled", True)),
        )
    if os.environ.get("ACCEL_RELATION_RANKING_WEIGHT"):
        runtime["relation_ranking_weight"] = _normalize_ratio(
            os.environ["ACCEL_RELATION_RANKING_WEIGHT"],
            float(runtime.get("relation_ranking_weight", 1.0)),
        )
    if os.environ.get("ACCEL_SEMANTIC_RANKER_ENABLED") is not None:
        runtime["semantic_ranker_enabled"] = _normalize_bool(
            os.environ["ACCEL_SEMANTIC_RANKER_ENABLED"],
            bool(runtime.get("semantic_ranker_enabled", False)),
        )
    if os.environ.get("ACCEL_SEMANTIC_RANKER_PROVIDER"):
        runtime["semantic_ranker_provider"] = normalize_semantic_provider(
            os.environ["ACCEL_SEMANTIC_RANKER_PROVIDER"],
            str(runtime.get("semantic_ranker_provider", "off")),
        )
    if os.environ.get("ACCEL_SEMANTIC_RANKER_MAX_CANDIDATES"):
        runtime["semantic_ranker_max_candidates"] = _normalize_positive_int(
            os.environ["ACCEL_SEMANTIC_RANKER_MAX_CANDIDATES"],
            int(runtime.get("semantic_ranker_max_candidates", 120)),
        )
    if os.environ.get("ACCEL_SEMANTIC_RANKER_BATCH_SIZE"):
        runtime["semantic_ranker_batch_size"] = _normalize_positive_int(
            os.environ["ACCEL_SEMANTIC_RANKER_BATCH_SIZE"],
            int(runtime.get("semantic_ranker_batch_size", 16)),
        )
    if os.environ.get("ACCEL_SEMANTIC_RANKER_EMBED_WEIGHT"):
        runtime["semantic_ranker_embed_weight"] = _normalize_ratio(
            os.environ["ACCEL_SEMANTIC_RANKER_EMBED_WEIGHT"],
            float(runtime.get("semantic_ranker_embed_weight", 0.3)),
        )
    if os.environ.get("ACCEL_SEMANTIC_RERANKER_ENABLED") is not None:
        runtime["semantic_reranker_enabled"] = _normalize_bool(
            os.environ["ACCEL_SEMANTIC_RERANKER_ENABLED"],
            bool(runtime.get("semantic_reranker_enabled", False)),
        )
    if os.environ.get("ACCEL_SEMANTIC_RERANKER_TOP_K"):
        runtime["semantic_reranker_top_k"] = _normalize_positive_int(
            os.environ["ACCEL_SEMANTIC_RERANKER_TOP_K"],
            int(runtime.get("semantic_reranker_top_k", 30)),
        )
    if os.environ.get("ACCEL_SEMANTIC_RERANKER_WEIGHT"):
        runtime["semantic_reranker_weight"] = _normalize_ratio(
            os.environ["ACCEL_SEMANTIC_RERANKER_WEIGHT"],
            float(runtime.get("semantic_reranker_weight", 0.15)),
        )
    if os.environ.get("ACCEL_COMMAND_PLAN_CACHE_ENABLED") is not None:
        runtime["command_plan_cache_enabled"] = _normalize_bool(
            os.environ["ACCEL_COMMAND_PLAN_CACHE_ENABLED"],
            bool(runtime.get("command_plan_cache_enabled", True)),
        )
    if os.environ.get("ACCEL_COMMAND_PLAN_CACHE_TTL_SECONDS"):
        runtime["command_plan_cache_ttl_seconds"] = _normalize_positive_int(
            os.environ["ACCEL_COMMAND_PLAN_CACHE_TTL_SECONDS"],
            int(runtime.get("command_plan_cache_ttl_seconds", 900)),
        )
    if os.environ.get("ACCEL_COMMAND_PLAN_CACHE_MAX_ENTRIES"):
        runtime["command_plan_cache_max_entries"] = _normalize_positive_int(
            os.environ["ACCEL_COMMAND_PLAN_CACHE_MAX_ENTRIES"],
            int(runtime.get("command_plan_cache_max_entries", 600)),
        )
    if os.environ.get("ACCEL_ADAPTIVE_BUDGET_ENABLED") is not None:
        runtime["adaptive_budget_enabled"] = _normalize_bool(
            os.environ["ACCEL_ADAPTIVE_BUDGET_ENABLED"],
            bool(runtime.get("adaptive_budget_enabled", True)),
        )
    if os.environ.get("ACCEL_ADAPTIVE_BUDGET_MIN_FACTOR"):
        runtime["adaptive_budget_min_factor"] = _normalize_positive_float(
            os.environ["ACCEL_ADAPTIVE_BUDGET_MIN_FACTOR"],
            float(runtime.get("adaptive_budget_min_factor", 0.65)),
        )
    if os.environ.get("ACCEL_ADAPTIVE_BUDGET_MAX_FACTOR"):
        runtime["adaptive_budget_max_factor"] = _normalize_positive_float(
            os.environ["ACCEL_ADAPTIVE_BUDGET_MAX_FACTOR"],
            float(runtime.get("adaptive_budget_max_factor", 1.45)),
        )
    if os.environ.get("ACCEL_SNIPPET_DEDUP_STRUCTURAL_THRESHOLD"):
        runtime["snippet_dedup_structural_threshold"] = _normalize_ratio(
            os.environ["ACCEL_SNIPPET_DEDUP_STRUCTURAL_THRESHOLD"],
            float(runtime.get("snippet_dedup_structural_threshold", 0.92)),
        )
    if os.environ.get("ACCEL_SNIPPET_DEDUP_SEMANTIC_THRESHOLD"):
        runtime["snippet_dedup_semantic_threshold"] = _normalize_ratio(
            os.environ["ACCEL_SNIPPET_DEDUP_SEMANTIC_THRESHOLD"],
            float(runtime.get("snippet_dedup_semantic_threshold", 0.82)),
        )
    if os.environ.get("ACCEL_CONSTRAINT_MODE"):
        runtime["constraint_mode"] = _normalize_constraint_mode(
            os.environ["ACCEL_CONSTRAINT_MODE"],
            str(runtime.get("constraint_mode", "warn")),
        )
    if os.environ.get("ACCEL_RULE_COMPRESSION_ENABLED") is not None:
        runtime["rule_compression_enabled"] = _normalize_bool(
            os.environ["ACCEL_RULE_COMPRESSION_ENABLED"],
            bool(runtime.get("rule_compression_enabled", True)),
        )
    if os.environ.get("ACCEL_SYNC_VERIFY_TIMEOUT_ACTION"):
        runtime["sync_verify_timeout_action"] = _normalize_timeout_action(
            os.environ["ACCEL_SYNC_VERIFY_TIMEOUT_ACTION"],
            str(runtime.get("sync_verify_timeout_action", "poll")),
        )
    if os.environ.get("ACCEL_SYNC_VERIFY_CANCEL_GRACE_SECONDS"):
        runtime["sync_verify_cancel_grace_seconds"] = _normalize_positive_float(
            os.environ["ACCEL_SYNC_VERIFY_CANCEL_GRACE_SECONDS"],
            float(runtime.get("sync_verify_cancel_grace_seconds", 5.0)),
        )
    if os.environ.get("ACCEL_SYNC_CONTEXT_TIMEOUT_ACTION"):
        runtime["sync_context_timeout_action"] = _normalize_context_timeout_action(
            os.environ["ACCEL_SYNC_CONTEXT_TIMEOUT_ACTION"],
            str(runtime.get("sync_context_timeout_action", "fallback_async")),
        )
    if os.environ.get("ACCEL_LOCAL_CONFIG"):
        config["meta"] = dict(config.get("meta", {}))
        config["meta"]["local_config_path"] = os.environ["ACCEL_LOCAL_CONFIG"]

    config["runtime"] = runtime
    return config


def _validate_effective_config(config: dict[str, Any]) -> None:
    if int(config.get("version", 0)) <= 0:
        raise ValueError("version must be a positive integer")

    config["language_profile_registry"] = resolve_language_profile_registry(config)
    config["language_profiles"] = resolve_selected_language_profiles(config)

    index = config.get("index", {})
    if not isinstance(index, dict):
        raise ValueError("index must be an object")
    index_scope_mode = str(index.get("scope_mode", "auto")).strip().lower()
    if index_scope_mode not in {"auto", "configured", "git", "git_tracked", "all"}:
        index_scope_mode = "auto"
    index["scope_mode"] = "git" if index_scope_mode == "git_tracked" else index_scope_mode
    include_raw = index.get("include", ["**/*"])
    if isinstance(include_raw, list):
        include_items = [str(item).strip() for item in include_raw if str(item).strip()]
    else:
        include_items = [str(include_raw).strip()] if str(include_raw).strip() else ["**/*"]
    index["include"] = include_items or ["**/*"]
    exclude_raw = index.get("exclude", [])
    if isinstance(exclude_raw, list):
        exclude_items = [str(item).strip() for item in exclude_raw if str(item).strip()]
    else:
        exclude_items = [str(exclude_raw).strip()] if str(exclude_raw).strip() else []
    index["exclude"] = exclude_items
    index["max_file_mb"] = _normalize_positive_int(index.get("max_file_mb", 2), default_value=2)
    index["max_files_to_scan"] = _normalize_positive_int(index.get("max_files_to_scan", 10000), default_value=10000)
    index["scan_timeout_seconds"] = _normalize_positive_int(index.get("scan_timeout_seconds", 60), default_value=60)
    config["index"] = index

    context = config.get("context", {})
    if not isinstance(context, dict):
        raise ValueError("context must be an object")
    for key in ("top_n_files", "snippet_radius", "max_chars", "max_snippets"):
        if int(context.get(key, 0)) <= 0:
            raise ValueError(f"context.{key} must be a positive integer")

    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be an object")
    runtime["max_workers"] = _normalize_max_workers(
        runtime.get("max_workers", _default_max_workers()),
        default_value=_default_max_workers(),
    )
    runtime["verify_workers"] = _normalize_positive_int(
        runtime.get("verify_workers", runtime.get("max_workers", _default_max_workers())),
        default_value=int(runtime.get("max_workers", _default_max_workers())),
    )
    runtime["index_workers"] = _normalize_positive_int(
        runtime.get("index_workers", _default_index_workers()),
        default_value=_default_index_workers(),
    )
    runtime["index_delta_compact_every"] = _normalize_positive_int(
        runtime.get("index_delta_compact_every", 200), default_value=200
    )
    runtime["verify_max_target_tests"] = _normalize_positive_int(
        runtime.get("verify_max_target_tests", 64), default_value=64
    )
    runtime["verify_pytest_shard_size"] = _normalize_positive_int(
        runtime.get("verify_pytest_shard_size", 16), default_value=16
    )
    runtime["verify_pytest_max_shards"] = _normalize_positive_int(
        runtime.get("verify_pytest_max_shards", 6), default_value=6
    )
    runtime["verify_fail_fast"] = _normalize_bool(runtime.get("verify_fail_fast", False), default_value=False)
    runtime["verify_cache_enabled"] = _normalize_bool(runtime.get("verify_cache_enabled", True), default_value=True)
    runtime["verify_cache_failed_results"] = _normalize_bool(
        runtime.get("verify_cache_failed_results", False), default_value=False
    )
    runtime["verify_cache_ttl_seconds"] = _normalize_positive_int(
        runtime.get("verify_cache_ttl_seconds", 900), default_value=900
    )
    runtime["verify_cache_failed_ttl_seconds"] = _normalize_positive_int(
        runtime.get("verify_cache_failed_ttl_seconds", 120), default_value=120
    )
    runtime["verify_cache_max_entries"] = _normalize_positive_int(
        runtime.get("verify_cache_max_entries", 400), default_value=400
    )
    runtime["verify_workspace_routing_enabled"] = _normalize_bool(
        runtime.get("verify_workspace_routing_enabled", True),
        default_value=True,
    )
    runtime["verify_preflight_enabled"] = _normalize_bool(
        runtime.get("verify_preflight_enabled", True),
        default_value=True,
    )
    runtime["verify_preflight_timeout_seconds"] = _normalize_positive_int(
        runtime.get("verify_preflight_timeout_seconds", 5),
        default_value=5,
    )
    runtime["verify_stall_timeout_seconds"] = _normalize_positive_float(
        runtime.get("verify_stall_timeout_seconds", 20.0),
        default_value=20.0,
    )
    runtime["verify_auto_cancel_on_stall"] = _normalize_bool(
        runtime.get("verify_auto_cancel_on_stall", False),
        default_value=False,
    )
    runtime["verify_max_wall_time_seconds"] = _normalize_positive_float(
        runtime.get(
            "verify_max_wall_time_seconds",
            runtime.get("total_verify_timeout_seconds", 3600.0),
        ),
        default_value=float(runtime.get("total_verify_timeout_seconds", 3600.0)),
    )
    runtime["context_rpc_timeout_seconds"] = _normalize_positive_float(
        runtime.get("context_rpc_timeout_seconds", 300.0),
        default_value=300.0,
    )
    runtime["sync_verify_wait_seconds"] = _normalize_positive_float(
        runtime.get("sync_verify_wait_seconds", 45.0),
        default_value=45.0,
    )
    runtime["sync_index_wait_seconds"] = _normalize_positive_float(
        runtime.get("sync_index_wait_seconds", 45.0),
        default_value=45.0,
    )
    runtime["sync_context_wait_seconds"] = _normalize_positive_float(
        runtime.get("sync_context_wait_seconds", 45.0),
        default_value=45.0,
    )
    runtime["token_estimator_backend"] = str(runtime.get("token_estimator_backend", "auto")).strip().lower() or "auto"
    if runtime["token_estimator_backend"] not in {"auto", "tiktoken", "heuristic"}:
        runtime["token_estimator_backend"] = "auto"
    runtime["token_estimator_encoding"] = (
        str(runtime.get("token_estimator_encoding", "cl100k_base")).strip() or "cl100k_base"
    )
    runtime["token_estimator_model"] = str(runtime.get("token_estimator_model", "")).strip()
    runtime["token_estimator_calibration"] = _normalize_positive_float(
        runtime.get("token_estimator_calibration", 1.0),
        default_value=1.0,
    )
    runtime["token_estimator_fallback_chars_per_token"] = _normalize_positive_float(
        runtime.get("token_estimator_fallback_chars_per_token", 4.0),
        default_value=4.0,
    )
    runtime["context_require_changed_files"] = _normalize_bool(
        runtime.get("context_require_changed_files", False),
        default_value=False,
    )
    runtime["semantic_cache_enabled"] = _normalize_bool(
        runtime.get("semantic_cache_enabled", True),
        default_value=True,
    )
    runtime["semantic_cache_mode"] = str(runtime.get("semantic_cache_mode", "hybrid")).strip().lower()
    if runtime["semantic_cache_mode"] not in {"exact", "hybrid"}:
        runtime["semantic_cache_mode"] = "hybrid"
    runtime["semantic_cache_ttl_seconds"] = _normalize_positive_int(
        runtime.get("semantic_cache_ttl_seconds", 7200),
        default_value=7200,
    )
    runtime["semantic_cache_hybrid_threshold"] = _normalize_positive_float(
        runtime.get("semantic_cache_hybrid_threshold", 0.86),
        default_value=0.86,
    )
    runtime["semantic_cache_hybrid_threshold"] = min(runtime["semantic_cache_hybrid_threshold"], 1.0)
    runtime["semantic_cache_max_entries"] = _normalize_positive_int(
        runtime.get("semantic_cache_max_entries", 800),
        default_value=800,
    )
    runtime["syntax_parser_enabled"] = _normalize_bool(
        runtime.get("syntax_parser_enabled", True),
        default_value=True,
    )
    runtime["syntax_parser_provider"] = normalize_syntax_provider(
        runtime.get("syntax_parser_provider", "auto"),
        default_value="auto",
    )
    runtime["lexical_ranker_enabled"] = _normalize_bool(
        runtime.get("lexical_ranker_enabled", True),
        default_value=True,
    )
    runtime["lexical_ranker_provider"] = normalize_lexical_provider(
        runtime.get("lexical_ranker_provider", "auto"),
        default_value="auto",
    )
    runtime["lexical_ranker_max_candidates"] = _normalize_positive_int(
        runtime.get("lexical_ranker_max_candidates", 200),
        default_value=200,
    )
    runtime["lexical_ranker_weight"] = _normalize_ratio(
        runtime.get("lexical_ranker_weight", 0.2),
        default_value=0.2,
    )
    runtime["relation_ranking_enabled"] = _normalize_bool(
        runtime.get("relation_ranking_enabled", True),
        default_value=True,
    )
    runtime["relation_ranking_weight"] = _normalize_ratio(
        runtime.get("relation_ranking_weight", 1.0),
        default_value=1.0,
    )
    runtime["semantic_ranker_enabled"] = _normalize_bool(
        runtime.get("semantic_ranker_enabled", False),
        default_value=False,
    )
    runtime["semantic_ranker_provider"] = normalize_semantic_provider(
        runtime.get("semantic_ranker_provider", "off"),
        default_value="off",
    )
    runtime["semantic_ranker_max_candidates"] = _normalize_positive_int(
        runtime.get("semantic_ranker_max_candidates", 120),
        default_value=120,
    )
    runtime["semantic_ranker_batch_size"] = _normalize_positive_int(
        runtime.get("semantic_ranker_batch_size", 16),
        default_value=16,
    )
    runtime["semantic_ranker_embed_weight"] = _normalize_ratio(
        runtime.get("semantic_ranker_embed_weight", 0.3),
        default_value=0.3,
    )
    runtime["semantic_reranker_enabled"] = _normalize_bool(
        runtime.get("semantic_reranker_enabled", False),
        default_value=False,
    )
    runtime["semantic_reranker_top_k"] = _normalize_positive_int(
        runtime.get("semantic_reranker_top_k", 30),
        default_value=30,
    )
    runtime["semantic_reranker_weight"] = _normalize_ratio(
        runtime.get("semantic_reranker_weight", 0.15),
        default_value=0.15,
    )
    runtime["command_plan_cache_enabled"] = _normalize_bool(
        runtime.get("command_plan_cache_enabled", True),
        default_value=True,
    )
    runtime["command_plan_cache_ttl_seconds"] = _normalize_positive_int(
        runtime.get("command_plan_cache_ttl_seconds", 900),
        default_value=900,
    )
    runtime["command_plan_cache_max_entries"] = _normalize_positive_int(
        runtime.get("command_plan_cache_max_entries", 600),
        default_value=600,
    )
    runtime["adaptive_budget_enabled"] = _normalize_bool(
        runtime.get("adaptive_budget_enabled", True),
        default_value=True,
    )
    runtime["adaptive_budget_min_factor"] = _normalize_positive_float(
        runtime.get("adaptive_budget_min_factor", 0.65),
        default_value=0.65,
    )
    runtime["adaptive_budget_max_factor"] = _normalize_positive_float(
        runtime.get("adaptive_budget_max_factor", 1.45),
        default_value=1.45,
    )
    if runtime["adaptive_budget_max_factor"] < runtime["adaptive_budget_min_factor"]:
        runtime["adaptive_budget_max_factor"] = float(runtime["adaptive_budget_min_factor"])
    runtime["snippet_dedup_structural_threshold"] = _normalize_ratio(
        runtime.get("snippet_dedup_structural_threshold", 0.92),
        default_value=0.92,
    )
    runtime["snippet_dedup_semantic_threshold"] = _normalize_ratio(
        runtime.get("snippet_dedup_semantic_threshold", 0.82),
        default_value=0.82,
    )
    runtime["constraint_mode"] = _normalize_constraint_mode(
        runtime.get("constraint_mode", "warn"),
        default_value="warn",
    )
    runtime["rule_compression_enabled"] = _normalize_bool(
        runtime.get("rule_compression_enabled", True),
        default_value=True,
    )
    runtime["sync_verify_timeout_action"] = _normalize_timeout_action(
        runtime.get("sync_verify_timeout_action", "poll"),
        default_value="poll",
    )
    runtime["sync_verify_cancel_grace_seconds"] = _normalize_positive_float(
        runtime.get("sync_verify_cancel_grace_seconds", 5.0),
        default_value=5.0,
    )
    runtime["sync_context_timeout_action"] = _normalize_context_timeout_action(
        runtime.get("sync_context_timeout_action", "fallback_async"),
        default_value="fallback_async",
    )

    accel_home = runtime.get("accel_home")
    if not accel_home:
        project_dir_value = str(config.get("meta", {}).get("project_dir", "")).strip()
        project_dir = Path(project_dir_value) if project_dir_value else None
        runtime["accel_home"] = str(default_accel_home(project_dir))
    config["runtime"] = runtime
