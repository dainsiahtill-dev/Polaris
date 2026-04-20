from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config_runtime import (
    _apply_env_overrides,
    _validate_effective_config,
    default_accel_home,
)

DEFAULT_PROJECT_CONFIG: dict[str, Any] = {
    "version": 1,
    "project_id": "demo_project",
    "language_profiles": ["python", "typescript"],
    "index": {
        "scope_mode": "auto",
        "include": ["src/**", "accel/**", "tests/**"],
        "exclude": [
            ".git/**",
            "node_modules/**",
            "dist/**",
            "build/**",
            "target/**",
            ".venv/**",
            "venv/**",
            ".polaris/projects/**",
            ".polaris/logs/**",
            ".polaris/snapshots/**",
            ".mypy_cache/**",
            ".pytest_cache/**",
            ".ruff_cache/**",
            ".next/**",
            ".turbo/**",
        ],
        "max_file_mb": 2,
    },
    "context": {
        "top_n_files": 12,
        "snippet_radius": 40,
        "max_chars": 24000,
        "max_snippets": 60,
    },
    "verify": {
        "python": [
            "python -m pytest -q",
            "python -m ruff check .",
            "python -m mypy --explicit-package-bases .",
        ],
        "node": ["npm test --silent", "npm run lint", "npm run typecheck"],
    },
}

DEFAULT_LOCAL_CONFIG: dict[str, Any] = {
    "runtime": {
        "max_workers": "auto",
        "verify_workers": "auto",
        "index_workers": "auto",
        "index_delta_compact_every": 200,
        "verify_max_target_tests": 64,
        "verify_pytest_shard_size": 16,
        "verify_pytest_max_shards": 6,
        "verify_fail_fast": False,
        "verify_cache_enabled": True,
        "verify_cache_failed_results": False,
        "verify_cache_ttl_seconds": 900,
        "verify_cache_failed_ttl_seconds": 120,
        "verify_cache_max_entries": 400,
        "verify_workspace_routing_enabled": True,
        "verify_preflight_enabled": True,
        "verify_preflight_timeout_seconds": 5,
        "verify_stall_timeout_seconds": 20.0,
        "verify_auto_cancel_on_stall": False,
        "verify_max_wall_time_seconds": 3600.0,
        "context_rpc_timeout_seconds": 300.0,
        "sync_verify_wait_seconds": 45.0,
        "sync_index_wait_seconds": 45.0,
        "sync_context_wait_seconds": 45.0,
        "sync_verify_timeout_action": "poll",
        "sync_verify_cancel_grace_seconds": 5.0,
        "sync_context_timeout_action": "fallback_async",
        "token_estimator_backend": "auto",
        "token_estimator_encoding": "cl100k_base",
        "token_estimator_model": "",
        "token_estimator_calibration": 1.0,
        "token_estimator_fallback_chars_per_token": 4.0,
        "context_require_changed_files": False,
        "semantic_cache_enabled": True,
        "semantic_cache_mode": "hybrid",
        "semantic_cache_ttl_seconds": 7200,
        "semantic_cache_hybrid_threshold": 0.86,
        "semantic_cache_max_entries": 800,
        "syntax_parser_enabled": True,
        "syntax_parser_provider": "auto",
        "lexical_ranker_enabled": True,
        "lexical_ranker_provider": "auto",
        "lexical_ranker_max_candidates": 200,
        "lexical_ranker_weight": 0.2,
        "relation_ranking_enabled": True,
        "relation_ranking_weight": 1.0,
        "semantic_ranker_enabled": False,
        "semantic_ranker_provider": "off",
        "semantic_ranker_max_candidates": 120,
        "semantic_ranker_batch_size": 16,
        "semantic_ranker_embed_weight": 0.3,
        "semantic_reranker_enabled": False,
        "semantic_reranker_top_k": 30,
        "semantic_reranker_weight": 0.15,
        "command_plan_cache_enabled": True,
        "command_plan_cache_ttl_seconds": 900,
        "command_plan_cache_max_entries": 600,
        "adaptive_budget_enabled": True,
        "adaptive_budget_min_factor": 0.65,
        "adaptive_budget_max_factor": 1.45,
        "snippet_dedup_structural_threshold": 0.92,
        "snippet_dedup_semantic_threshold": 0.82,
        "constraint_mode": "warn",
        "rule_compression_enabled": True,
        "accel_home": "",
        "per_command_timeout_seconds": 1200,
        "total_verify_timeout_seconds": 3600,
    },
}


def _normalize_path(path: Path) -> Path:
    # Path.resolve() on some Windows/Python setups may produce duplicated segments.
    return Path(os.path.abspath(str(path)))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            # Add timeout protection for YAML loading
            import threading

            import yaml  # type: ignore[import-untyped]

            class TimeoutError(Exception):
                pass

            loaded = None
            timeout_error = None

            def load_yaml_with_timeout() -> None:
                nonlocal loaded, timeout_error
                try:
                    loaded = yaml.safe_load(text)
                except (RuntimeError, ValueError) as exc:
                    timeout_error = exc

            # Use threading for timeout (works on Windows and Unix)
            thread = threading.Thread(target=load_yaml_with_timeout)
            thread.daemon = True
            thread.start()
            thread.join(timeout=5.0)  # 5 second timeout

            if thread.is_alive():
                # Timeout occurred
                import logging

                logger = logging.getLogger("accel_config")
                logger.warning("YAML config loading timed out, using empty config")
                return {}

            if timeout_error:
                raise timeout_error

            if loaded is None:
                return {}
            if not isinstance(loaded, dict):
                raise ValueError(f"Config root must be an object: {path}")
            return loaded
        except (RuntimeError, ValueError) as exc:  # pragma: no cover - fallback guard
            raise ValueError(
                f"Failed to parse config file {path}. Use JSON-compatible YAML or install PyYAML."
            ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be an object: {path}")
    return data


def resolve_effective_config(
    project_dir: Path,
    cli_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_dir = _normalize_path(project_dir)
    project_cfg_path = project_dir / "accel.yaml"
    env_local = os.environ.get("ACCEL_LOCAL_CONFIG")
    local_cfg_path = _normalize_path(Path(env_local)) if env_local else (project_dir / "accel.local.yaml")

    project_cfg = _load_config_file(project_cfg_path)
    local_cfg = _load_config_file(local_cfg_path)
    merged = _deep_merge(DEFAULT_PROJECT_CONFIG, project_cfg)
    merged = _deep_merge(merged, DEFAULT_LOCAL_CONFIG)
    merged = _deep_merge(merged, local_cfg)
    merged = _apply_env_overrides(merged)
    if cli_overrides:
        merged = _deep_merge(merged, cli_overrides)

    merged["meta"] = dict(merged.get("meta", {}))
    merged["meta"]["project_dir"] = str(project_dir)
    merged["meta"]["project_config_path"] = str(project_cfg_path)
    merged["meta"]["local_config_path"] = str(local_cfg_path)

    _validate_effective_config(merged)
    return merged


def _dump_json_as_yaml(path: Path, data: dict[str, Any]) -> None:
    # JSON is valid YAML. This keeps dependencies optional and files portable.
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def init_project(project_dir: Path, force: bool = False) -> dict[str, Any]:
    project_dir = _normalize_path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    project_cfg_path = project_dir / "accel.yaml"
    local_example_path = project_dir / "accel.local.yaml.example"

    created: list[str] = []
    skipped: list[str] = []

    if force or not project_cfg_path.exists():
        _dump_json_as_yaml(project_cfg_path, DEFAULT_PROJECT_CONFIG)
        created.append(str(project_cfg_path))
    else:
        skipped.append(str(project_cfg_path))

    if force or not local_example_path.exists():
        local = json.loads(json.dumps(DEFAULT_LOCAL_CONFIG))
        local["runtime"]["accel_home"] = str(default_accel_home(project_dir)).replace("\\", "/")
        _dump_json_as_yaml(local_example_path, local)
        created.append(str(local_example_path))
    else:
        skipped.append(str(local_example_path))

    gitignore_path = project_dir / ".gitignore"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    if "accel.local.yaml" not in existing:
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        gitignore_path.write_text(
            existing + suffix + "accel.local.yaml\n",
            encoding="utf-8",
        )
        created.append(str(gitignore_path))

    return {"created": created, "skipped": skipped}
