"""Core verify orchestration logic."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from polaris.infrastructure.accel.storage.cache import ensure_project_dirs, project_paths
from polaris.infrastructure.accel.utils import utc_now_iso as _utc_now
from polaris.infrastructure.accel.verify.callbacks import NoOpCallback, VerifyProgressCallback
from polaris.infrastructure.accel.verify.orchestrator_helpers import (
    _build_changed_files_fingerprint,
    _cache_file_path,
    _cache_key,
    _can_use_cached_entry,
    _classify_verify_failures,
    _is_failure,
    _load_cache_entries,
    _normalize_cached_result,
    _normalize_live_result,
    _prune_cache_entries,
    _write_cache_entries_atomic,
)
from polaris.infrastructure.accel.verify.sharding import select_verify_commands

from .formatters import (
    command_binary,
    normalize_bool,
    normalize_positive_int,
    resolve_windows_compatible_command,
)
from .gate_checker import (
    detect_missing_python_deps,
    preflight_warnings_for_command,
    should_skip_for_preflight,
)
from .report_generator import (
    append_line,
    build_verify_selection_evidence,
    log_command_result,
)
from .runner_utils import invoke_run_command, store_cache_result


@dataclass(frozen=True, slots=True)
class VerifyConfig:
    """Verify configuration."""

    workspace: str
    output_format: str  # "json" | "markdown" | "html"
    fail_fast: bool
    parallel_jobs: int
    per_command_timeout: int
    cache_enabled: bool
    cache_ttl_seconds: int
    cache_max_entries: int
    preflight_enabled: bool
    preflight_timeout_seconds: int
    stall_timeout_seconds: float | None
    max_wall_time_seconds: float | None
    auto_cancel_on_stall: bool


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Verify result."""

    status: str
    exit_code: int
    nonce: str
    log_path: str
    jsonl_path: str
    commands: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    fail_fast: bool = False
    fail_fast_skipped_commands: list[str] = field(default_factory=list)
    cache_enabled: bool = False
    cache_hits: int = 0
    cache_misses: int = 0
    mode: str = "sequential"
    partial: bool = False
    partial_reason: str = ""
    failure_kind: str = "none"
    failed_commands: list[str] = field(default_factory=list)


def _resolve_verify_workers(runtime_cfg: dict[str, Any]) -> int:
    """Resolve verify worker count from runtime config."""
    fallback: int = normalize_positive_int(runtime_cfg.get("max_workers", 8), 8)
    workers_raw = runtime_cfg.get("verify_workers", fallback)
    return int(normalize_positive_int(workers_raw if workers_raw is not None else fallback, fallback))


def run_verify(
    project_dir: Path,
    config: dict[str, Any],
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """Run verification without callback."""
    accel_home = Path(config["runtime"]["accel_home"]).resolve()
    paths = project_paths(accel_home, project_dir)
    ensure_project_dirs(paths)

    runtime_cfg = config.get("runtime", {})
    verify_workers = _resolve_verify_workers(runtime_cfg)
    per_command_timeout = int(runtime_cfg.get("per_command_timeout_seconds", 1200))
    verify_fail_fast = normalize_bool(runtime_cfg.get("verify_fail_fast", False), False)
    verify_cache_enabled_cfg = normalize_bool(runtime_cfg.get("verify_cache_enabled", True), True)
    verify_cache_failed_results = normalize_bool(runtime_cfg.get("verify_cache_failed_results", False), False)
    verify_cache_ttl_seconds = normalize_positive_int(runtime_cfg.get("verify_cache_ttl_seconds", 900), 900)
    verify_cache_max_entries = normalize_positive_int(runtime_cfg.get("verify_cache_max_entries", 400), 400)
    verify_cache_failed_ttl_seconds = normalize_positive_int(
        runtime_cfg.get("verify_cache_failed_ttl_seconds", 120), 120
    )
    verify_preflight_enabled = normalize_bool(runtime_cfg.get("verify_preflight_enabled", True), True)
    verify_preflight_timeout_seconds = normalize_positive_int(runtime_cfg.get("verify_preflight_timeout_seconds", 5), 5)
    verify_mode = "fail_fast" if verify_fail_fast else ("parallel" if verify_workers > 1 else "sequential")

    nonce = uuid4().hex[:12]
    log_path = paths["verify"] / f"verify_{nonce}.log"
    jsonl_path = paths["verify"] / f"verify_{nonce}.jsonl"
    commands = select_verify_commands(config=config, changed_files=changed_files)
    selection_evidence = build_verify_selection_evidence(
        config=config,
        changed_files=changed_files,
        commands=commands,
    )
    selected_commands_count = len(commands)

    append_line(log_path, f"VERIFICATION_START {nonce} {_utc_now()}")
    append_line(log_path, f"ENV cwd={project_dir} python={shutil.which('python') or ''}")
    append_line(
        log_path,
        "SELECTION_EVIDENCE " + json.dumps(selection_evidence, ensure_ascii=False),
    )

    changed_fingerprint = _build_changed_files_fingerprint(project_dir=project_dir, changed_files=changed_files)
    cache_enabled = bool(verify_cache_enabled_cfg and changed_fingerprint)
    cache_path = _cache_file_path(paths)
    cache_entries: dict[str, dict[str, Any]] = {}
    cache_dirty = False
    cache_hits = 0
    cache_misses = 0
    if cache_enabled:
        loaded_entries = _load_cache_entries(cache_path)
        cache_entries, was_pruned = _prune_cache_entries(
            loaded_entries,
            ttl_seconds=verify_cache_ttl_seconds,
            max_entries=verify_cache_max_entries,
        )
        cache_dirty = was_pruned
    elif verify_cache_enabled_cfg:
        append_line(log_path, "CACHE_DISABLED no changed_files fingerprint")

    import_probe_cache: dict[tuple[str, str], bool] = {}
    runnable_commands: list[str] = []
    degraded = False
    degrade_reasons: list[str] = []

    if selected_commands_count == 0:
        degraded = True
        degrade_reasons.append("no verify commands selected")
        append_line(log_path, "NO_COMMANDS selected verify command list is empty")

    for command in commands:
        effective_command, compat_reason = resolve_windows_compatible_command(project_dir, command)
        if compat_reason:
            append_line(log_path, f"CMD_COMPAT {command} -> {effective_command} ({compat_reason})")
        if verify_preflight_enabled:
            warnings = preflight_warnings_for_command(
                project_dir=project_dir,
                command=effective_command,
                timeout_seconds=verify_preflight_timeout_seconds,
                import_probe_cache=import_probe_cache,
            )
            preflight_skip_reason = ""
            for warning in warnings:
                if warning not in degrade_reasons:
                    degrade_reasons.append(warning)
                degraded = True
                if not preflight_skip_reason and should_skip_for_preflight(warning):
                    preflight_skip_reason = warning
                append_line(log_path, f"PREFLIGHT_WARN {effective_command} ({warning})")
            if preflight_skip_reason:
                append_line(log_path, f"SKIP {effective_command} ({preflight_skip_reason})")
                continue
        binary = command_binary(effective_command)
        if binary and shutil.which(binary) is None:
            degraded = True
            reason = f"missing command binary: {binary}"
            degrade_reasons.append(reason)
            append_line(log_path, f"SKIP {effective_command} ({reason})")
            continue
        runnable_commands.append(effective_command)

    results: list[dict[str, Any]] = []
    fail_fast_skipped_commands: list[str] = []
    unfinished_items: list[dict[str, Any]] = []

    for command in runnable_commands:
        cache_key: str | None = None
        if cache_enabled:
            cache_key = _cache_key(
                command=command,
                project_dir=project_dir,
                changed_fingerprint=changed_fingerprint,
            )
            cached_entry = cache_entries.get(cache_key)
            if cached_entry is not None and _can_use_cached_entry(
                cached_entry,
                allow_failed=verify_cache_failed_results,
            ):
                cache_hits += 1
                cached_result = _normalize_cached_result(command=command, entry=cached_entry)
                results.append(cached_result)
                append_line(log_path, f"CACHE_HIT {command}")
                continue
            cache_misses += 1

        live_result = _normalize_live_result(invoke_run_command(command, project_dir, per_command_timeout))
        results.append(live_result)
        log_command_result(
            log_path,
            jsonl_path,
            live_result,
            mode=verify_mode,
            fail_fast=verify_fail_fast,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            utc_now=_utc_now(),
        )
        if (
            cache_enabled
            and cache_key
            and store_cache_result(
                cache_entries,
                cache_key,
                command,
                live_result,
                cache_failed_results=verify_cache_failed_results,
                failed_ttl_seconds=verify_cache_failed_ttl_seconds,
                utc_now_fn=_utc_now,
                is_failure_fn=_is_failure,
            )
        ):
            cache_dirty = True

    if cache_enabled and cache_dirty:
        cache_entries, _ = _prune_cache_entries(
            cache_entries,
            ttl_seconds=verify_cache_ttl_seconds,
            max_entries=verify_cache_max_entries,
        )
        _write_cache_entries_atomic(cache_path, cache_entries)

    results.sort(key=lambda row: row["command"])
    missing_python_deps = detect_missing_python_deps(results)
    if missing_python_deps:
        degraded = True
        reason_text = "missing python dependencies: " + ", ".join(missing_python_deps)
        if reason_text not in degrade_reasons:
            degrade_reasons.append(reason_text)

    failure_summary = _classify_verify_failures(results)
    failure_kind = str(failure_summary.get("failure_kind", "none"))
    has_failure = any(_is_failure(item) for item in results)

    if has_failure:
        exit_code = 3
        status = "failed"
    elif degraded:
        exit_code = 2
        status = "degraded"
    else:
        exit_code = 0
        status = "success"

    append_line(log_path, f"VERIFICATION_END {nonce} {_utc_now()}")

    return {
        "status": status,
        "exit_code": exit_code,
        "nonce": nonce,
        "log_path": str(log_path),
        "jsonl_path": str(jsonl_path),
        "commands": commands,
        "results": results,
        "degraded": degraded,
        "fail_fast": verify_fail_fast,
        "fail_fast_skipped_commands": fail_fast_skipped_commands,
        "cache_enabled": cache_enabled,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "mode": verify_mode,
        "partial": False,
        "partial_reason": "",
        "unfinished_items": unfinished_items,
        "failure_kind": failure_kind,
        "failed_commands": list(failure_summary.get("failed_commands", [])),
        "failure_counts": dict(failure_summary.get("failure_counts", {})),
        "selected_commands_count": int(selected_commands_count),
        "runnable_commands_count": len(runnable_commands),
        "verify_selection_evidence": selection_evidence,
    }


def run_verify_with_callback(
    project_dir: Path,
    config: dict[str, Any],
    changed_files: list[str] | None = None,
    callback: VerifyProgressCallback | None = None,
) -> dict[str, Any]:
    """Run verification with progress callback."""
    if callback is None:
        callback = NoOpCallback()
    result = run_verify(project_dir, config, changed_files)
    callback.on_complete(result["nonce"], result["status"], result["exit_code"])
    return result


__all__ = [
    "VerifyConfig",
    "VerifyResult",
    "_resolve_verify_workers",
    "run_verify",
    "run_verify_with_callback",
]
