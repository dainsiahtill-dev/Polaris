"""Report generation and logging utilities for verify orchestrator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...language_profiles import (
    resolve_enabled_verify_groups,
    resolve_extension_verify_group_map,
)
from .formatters import extract_pytest_targets

if TYPE_CHECKING:
    from collections.abc import Callable


def append_line(path: Path, line: str) -> None:
    """Append a line to a log file."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """Append a JSON line to a jsonl file."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def default_backfill_deadline(hours: int = 24) -> str:
    """Generate default backfill deadline timestamp."""
    return (
        (datetime.now(timezone.utc) + timedelta(hours=max(1, int(hours))))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_verify_selection_evidence(
    *,
    config: dict[str, Any],
    changed_files: list[str] | None,
    commands: list[str],
) -> dict[str, Any]:
    """Build evidence for verify command selection."""
    verify_cfg = config.get("verify", {})
    verify_group_order = [
        str(group).strip().lower() for group, raw_commands in dict(verify_cfg).items() if isinstance(raw_commands, list)
    ]
    verify_group_commands: dict[str, list[str]] = {}
    for group in verify_group_order:
        raw_commands = verify_cfg.get(group, [])
        if isinstance(raw_commands, list):
            verify_group_commands[group] = [str(item) for item in raw_commands]

    enabled_groups = resolve_enabled_verify_groups(config)
    if not enabled_groups:
        enabled_groups = list(verify_group_order)
    extension_group_map = resolve_extension_verify_group_map(config)

    changed = [str(item).replace("\\", "/").lower() for item in (changed_files or [])]
    changed_groups: set[str] = set()
    for item in changed:
        suffix = Path(item).suffix
        if not suffix:
            continue
        group = str(extension_group_map.get(suffix, "")).strip().lower()
        if group:
            changed_groups.add(group)
    run_all = len(changed) == 0

    baseline_commands: list[str] = []
    for group in verify_group_order:
        if group not in enabled_groups:
            continue
        if not run_all and group not in changed_groups:
            continue
        baseline_commands.extend(verify_group_commands.get(group, []))

    accelerated_commands = [command for command in commands if command not in baseline_commands]
    targeted_tests: list[str] = []
    seen_targets: set[str] = set()
    for command in commands:
        for target in extract_pytest_targets(command):
            if target in seen_targets:
                continue
            seen_targets.add(target)
            targeted_tests.append(target)

    return {
        "run_all": bool(run_all),
        "changed_files": list(changed_files or []),
        "enabled_verify_groups": list(enabled_groups),
        "changed_verify_groups": sorted(changed_groups),
        "layers": [
            {
                "layer": "safety_baseline",
                "reason": "run_all" if run_all else "changed_verify_groups_match",
                "commands": list(baseline_commands),
            },
            {
                "layer": "incremental_acceleration",
                "commands": list(accelerated_commands),
                "targeted_tests": list(targeted_tests),
            },
        ],
        "baseline_commands_count": len(baseline_commands),
        "accelerated_commands_count": len(accelerated_commands),
        "final_commands_count": len(commands),
    }


def log_command_result(
    log_path: Path,
    jsonl_path: Path,
    result: dict[str, Any],
    *,
    mode: str,
    fail_fast: bool,
    cache_hits: int,
    cache_misses: int,
    command_index: int | None = None,
    total_commands: int | None = None,
    fail_fast_skipped: bool = False,
    utc_now: str,
) -> None:
    """Log a command result to log and jsonl files."""
    append_line(log_path, f"CMD {result['command']}")
    append_line(log_path, f"EXIT {result['exit_code']} DURATION {result['duration_seconds']}s")
    if result["stdout"]:
        append_line(log_path, f"STDOUT {str(result['stdout']).strip()[:2000]}")
    if result["stderr"]:
        append_line(log_path, f"STDERR {str(result['stderr']).strip()[:2000]}")
    append_jsonl(
        jsonl_path,
        {
            "event": "command_result",
            "ts": utc_now,
            "command": result["command"],
            "exit_code": result["exit_code"],
            "duration_seconds": result["duration_seconds"],
            "timed_out": result["timed_out"],
            "cancelled": bool(result.get("cancelled", False)),
            "stalled": bool(result.get("stalled", False)),
            "cancel_reason": str(result.get("cancel_reason", "")),
            "cached": bool(result.get("cached", False)),
            "cache_kind": str(result.get("cache_kind", "success")),
            "mode": str(mode),
            "fail_fast": bool(fail_fast),
            "cache_hits": int(cache_hits),
            "cache_misses": int(cache_misses),
            "fail_fast_skipped": bool(fail_fast_skipped),
            "command_index": int(command_index) if command_index is not None else None,
            "total_commands": int(total_commands) if total_commands is not None else None,
        },
    )


class ReportGenerator:
    """Report generator for verify results."""

    __slots__ = ("_jsonl_path", "_log_path", "_nonce", "_utc_now_fn")

    def __init__(
        self,
        log_path: Path,
        jsonl_path: Path,
        nonce: str,
        utc_now_fn: Callable[[], str],
    ) -> None:
        self._log_path = log_path
        self._jsonl_path = jsonl_path
        self._nonce = nonce
        self._utc_now_fn = utc_now_fn

    def write_verification_start(
        self,
        project_dir: Path,
        python_path: str,
        selection_evidence: dict[str, Any],
        mode: str,
        fail_fast: bool,
        cache_failed_results: bool,
        cache_failed_ttl_seconds: int,
        preflight_enabled: bool,
        stall_timeout_seconds: float | None,
        auto_cancel_on_stall: bool,
        max_wall_time_seconds: float | None,
        workspace_routing_enabled: bool,
    ) -> None:
        """Write verification start log entry."""
        ts = self._utc_now_fn()
        append_line(self._log_path, f"VERIFICATION_START {self._nonce} {ts}")
        append_line(self._log_path, f"ENV cwd={project_dir} python={python_path}")
        append_line(
            self._log_path,
            "SELECTION_EVIDENCE " + json.dumps(selection_evidence, ensure_ascii=False),
        )
        append_jsonl(
            self._jsonl_path,
            {
                "event": "verification_start",
                "nonce": self._nonce,
                "ts": ts,
                "mode": mode,
                "fail_fast": bool(fail_fast),
                "cache_failed_results": bool(cache_failed_results),
                "cache_failed_ttl_seconds": int(cache_failed_ttl_seconds),
                "preflight_enabled": bool(preflight_enabled),
                "stall_timeout_seconds": (float(stall_timeout_seconds) if stall_timeout_seconds is not None else None),
                "auto_cancel_on_stall": bool(auto_cancel_on_stall),
                "max_wall_time_seconds": (float(max_wall_time_seconds) if max_wall_time_seconds is not None else None),
                "workspace_routing_enabled": bool(workspace_routing_enabled),
                "selection_evidence": selection_evidence,
            },
        )

    def write_verification_end(
        self,
        status: str,
        exit_code: int,
        mode: str,
        fail_fast: bool,
        cache_hits: int,
        cache_misses: int,
        fail_fast_skipped_count: int,
        cache_failed_results: bool,
        cache_failed_ttl_seconds: int,
        partial: bool,
        partial_reason: str,
        unfinished_count: int,
        failure_kind: str,
        failure_counts: dict[str, Any],
        selected_commands_count: int,
        runnable_commands_count: int,
    ) -> None:
        """Write verification end log entry."""
        ts = self._utc_now_fn()
        append_line(self._log_path, f"VERIFICATION_END {self._nonce} {ts}")
        append_jsonl(
            self._jsonl_path,
            {
                "event": "verification_end",
                "nonce": self._nonce,
                "status": status,
                "exit_code": exit_code,
                "ts": ts,
                "mode": mode,
                "fail_fast": bool(fail_fast),
                "cache_hits": int(cache_hits),
                "cache_misses": int(cache_misses),
                "fail_fast_skipped": int(fail_fast_skipped_count),
                "cache_failed_results": bool(cache_failed_results),
                "cache_failed_ttl_seconds": int(cache_failed_ttl_seconds),
                "partial": bool(partial),
                "partial_reason": str(partial_reason),
                "unfinished_count": int(unfinished_count),
                "failure_kind": failure_kind,
                "failure_counts": failure_counts,
                "selected_commands_count": int(selected_commands_count),
                "runnable_commands_count": int(runnable_commands_count),
            },
        )

    def write_cache_hit(self, command: str) -> None:
        """Write cache hit log entry."""
        append_line(self._log_path, f"CACHE_HIT {command}")
        append_jsonl(
            self._jsonl_path,
            {
                "event": "command_cache_hit",
                "command": command,
                "ts": self._utc_now_fn(),
            },
        )

    def write_command_skipped(
        self,
        command: str,
        reason: str,
        mode: str,
        fail_fast: bool,
        cache_hits: int,
        cache_misses: int,
        fail_fast_skipped: bool,
    ) -> None:
        """Write command skipped log entry."""
        append_line(self._log_path, f"SKIP {command} ({reason})")
        append_jsonl(
            self._jsonl_path,
            {
                "event": "command_skipped",
                "command": command,
                "reason": reason,
                "ts": self._utc_now_fn(),
                "mode": mode,
                "fail_fast": bool(fail_fast),
                "cache_hits": int(cache_hits),
                "cache_misses": int(cache_misses),
                "fail_fast_skipped": bool(fail_fast_skipped),
            },
        )


__all__ = [
    "ReportGenerator",
    "append_jsonl",
    "append_line",
    "build_verify_selection_evidence",
    "default_backfill_deadline",
    "log_command_result",
]
