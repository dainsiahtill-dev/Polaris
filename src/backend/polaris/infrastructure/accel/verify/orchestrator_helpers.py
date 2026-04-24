from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..utils import utc_now_iso as _utc_now

if TYPE_CHECKING:
    from collections.abc import Callable

    from .callbacks import VerifyProgressCallback
logger = logging.getLogger(__name__)


def _normalize_positive_int(value: Any, default_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return max(1, int(default_value))
    return max(1, parsed)


def _normalize_changed_path(project_dir: Path, changed_file: str) -> tuple[str, Path]:
    raw = changed_file.replace("\\", "/").strip()
    candidate = Path(raw)
    abs_path = candidate if candidate.is_absolute() else (project_dir / candidate)
    abs_resolved = abs_path.resolve()
    project_resolved = project_dir.resolve()
    try:
        rel = abs_resolved.relative_to(project_resolved).as_posix()
        return rel, abs_resolved
    except ValueError:
        return raw, abs_resolved


def _build_changed_files_fingerprint(
    project_dir: Path,
    changed_files: list[str] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for changed_file in changed_files or []:
        key, abs_path = _normalize_changed_path(project_dir, str(changed_file))
        row: dict[str, Any] = {"path": key}
        if abs_path.exists():
            stat = abs_path.stat()
            row["exists"] = True
            row["size"] = int(stat.st_size)
            row["mtime_ns"] = int(stat.st_mtime_ns)
            row["is_dir"] = bool(abs_path.is_dir())
        else:
            row["exists"] = False
        rows.append(row)
    rows.sort(key=lambda item: str(item.get("path", "")))
    return rows


def _cache_file_path(paths: dict[str, Path]) -> Path:
    return paths["verify"] / "command_cache.json"


def _cache_key(
    command: str,
    project_dir: Path,
    changed_fingerprint: list[dict[str, Any]],
) -> str:
    payload = {
        "version": 1,
        "command": command,
        "project_dir": str(project_dir.resolve()),
        "changed_files": changed_fingerprint,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_cache_entries(cache_path: Path) -> dict[str, dict[str, Any]]:
    if not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    entries_raw = payload.get("entries", {})
    if not isinstance(entries_raw, dict):
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for key, value in entries_raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            entries[key] = value
    return entries


def _prune_cache_entries(
    entries: dict[str, dict[str, Any]],
    ttl_seconds: int,
    max_entries: int,
) -> tuple[dict[str, dict[str, Any]], bool]:
    now = datetime.now(timezone.utc)
    max_count = max(1, max_entries)

    valid: list[tuple[str, datetime, dict[str, Any]]] = []
    for key, entry in entries.items():
        saved_at = _parse_utc(entry.get("saved_utc"))
        if saved_at is None:
            continue
        entry_ttl_seconds = _normalize_positive_int(entry.get("ttl_seconds", ttl_seconds), ttl_seconds)
        entry_ttl = timedelta(seconds=max(1, entry_ttl_seconds))
        if now - saved_at > entry_ttl:
            continue
        valid.append((key, saved_at, entry))

    valid.sort(key=lambda item: item[1], reverse=True)
    trimmed = valid[:max_count]
    pruned = {key: entry for key, _, entry in trimmed}
    was_pruned = len(pruned) != len(entries)
    return pruned, was_pruned


def _write_cache_entries_atomic(cache_path: Path, entries: dict[str, dict[str, Any]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_utc": _utc_now(),
        "entries": entries,
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(cache_path.parent),
        prefix=f".{cache_path.name}.",
        suffix=".tmp",
        newline="\n",
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
        try:
            tmp_file.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            os.replace(tmp_path, cache_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)


def _is_failure(result: dict[str, Any]) -> bool:
    return int(result.get("exit_code", 1)) != 0


def _is_executor_failure_result(result: dict[str, Any]) -> bool:
    if bool(result.get("timed_out", False)):
        return True
    if bool(result.get("cancelled", False)):
        return True
    if bool(result.get("stalled", False)):
        return True
    cancel_reason = str(result.get("cancel_reason", "")).strip().lower()
    if cancel_reason in {"external_cancel", "stall_timeout"}:
        return True
    stderr = str(result.get("stderr", "")).strip().lower()
    if not stderr:
        return False
    markers = (
        "agent-accel process error:",
        "agent-accel error:",
        "threadpool future error:",
        "threadpool timeout",
        "futures unfinished",
    )
    return any(marker in stderr for marker in markers)


def _classify_verify_failures(results: list[dict[str, Any]]) -> dict[str, Any]:
    failed_rows = [row for row in results if _is_failure(row)]
    failed_commands: list[str] = []
    executor_failed_commands: list[str] = []
    project_failed_commands: list[str] = []
    seen_failed: set[str] = set()
    seen_executor: set[str] = set()
    seen_project: set[str] = set()

    for row in failed_rows:
        command = str(row.get("command", "")).strip()
        if not command:
            continue
        if command not in seen_failed:
            failed_commands.append(command)
            seen_failed.add(command)
        if _is_executor_failure_result(row):
            if command not in seen_executor:
                executor_failed_commands.append(command)
                seen_executor.add(command)
        elif command not in seen_project:
            project_failed_commands.append(command)
            seen_project.add(command)

    if not failed_rows:
        failure_kind = "none"
    elif executor_failed_commands and project_failed_commands:
        failure_kind = "mixed_failed"
    elif executor_failed_commands:
        failure_kind = "executor_failed"
    else:
        failure_kind = "project_gate_failed"

    return {
        "failure_kind": failure_kind,
        "failed_commands": failed_commands,
        "executor_failed_commands": executor_failed_commands,
        "project_failed_commands": project_failed_commands,
        "failure_counts": {
            "failed_total": len(failed_rows),
            "executor_failed": len(executor_failed_commands),
            "project_failed": len(project_failed_commands),
        },
    }


def _normalize_live_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    normalized["command"] = str(result.get("command", ""))
    normalized["exit_code"] = int(result.get("exit_code", 1))
    normalized["duration_seconds"] = float(result.get("duration_seconds", 0.0))
    normalized["stdout"] = str(result.get("stdout", ""))
    normalized["stderr"] = str(result.get("stderr", ""))
    normalized["timed_out"] = bool(result.get("timed_out", False))
    normalized["cancelled"] = bool(result.get("cancelled", False))
    normalized["stalled"] = bool(result.get("stalled", False))
    normalized["cancel_reason"] = str(result.get("cancel_reason", ""))
    normalized["cached"] = False
    return normalized


def _normalize_cached_result(command: str, entry: dict[str, Any]) -> dict[str, Any]:
    stored = entry.get("result", {})
    if not isinstance(stored, dict):
        stored = {}
    cache_kind = str(entry.get("cache_kind", "success") or "success")
    return {
        "command": command,
        "exit_code": int(stored.get("exit_code", 1)),
        "duration_seconds": float(stored.get("duration_seconds", 0.0)),
        "stdout": str(stored.get("stdout", "")),
        "stderr": str(stored.get("stderr", "")),
        "timed_out": bool(stored.get("timed_out", False)),
        "cancelled": bool(stored.get("cancelled", False)),
        "stalled": bool(stored.get("stalled", False)),
        "cancel_reason": str(stored.get("cancel_reason", "")),
        "cached": True,
        "cache_kind": cache_kind,
    }


def _cache_entry_is_failure(entry: dict[str, Any]) -> bool:
    cache_kind = str(entry.get("cache_kind", "success") or "success").strip().lower()
    if cache_kind == "failure":
        return True
    stored = entry.get("result", {})
    if not isinstance(stored, dict):
        return False
    if bool(stored.get("timed_out", False)):
        return True
    return int(stored.get("exit_code", 0)) != 0


def _can_use_cached_entry(entry: dict[str, Any], *, allow_failed: bool) -> bool:
    if allow_failed:
        return True
    return not _cache_entry_is_failure(entry)


def _safe_callback_call(callback: VerifyProgressCallback, method_name: str, *args: Any, **kwargs: Any) -> None:
    method = getattr(callback, method_name, None)
    if method is None:
        return
    try:
        method(*args, **kwargs)
    except TypeError:
        # Backward compatibility for callbacks that do not accept newer keyword args.
        method(*args)


def _tail_output_text(value: Any, limit: int = 600) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


def _remaining_wall_time_seconds(
    *,
    started_at: float,
    max_wall_time_seconds: float | None,
) -> float | None:
    if max_wall_time_seconds is None:
        return None
    elapsed = max(0.0, time.perf_counter() - started_at)
    return max(0.0, float(max_wall_time_seconds) - elapsed)


def _timeboxed_command_timeout(
    *,
    per_command_timeout: int,
    remaining_wall_time: float | None,
) -> int:
    timeout_seconds = int(max(1, int(per_command_timeout)))
    if remaining_wall_time is None:
        return timeout_seconds
    if remaining_wall_time < 1.0:
        return 0
    return int(max(1, min(timeout_seconds, int(remaining_wall_time))))


def _append_unfinished_entries(
    *,
    unfinished_items: list[dict[str, Any]],
    commands: list[str],
    reason: str,
) -> None:
    reason_text = str(reason or "").strip() or "unfinished"
    existing: set[str] = {str(item.get("command", "")) for item in unfinished_items}
    for command in commands:
        command_text = str(command or "")
        if not command_text or command_text in existing:
            continue
        unfinished_items.append({"command": command_text, "reason": reason_text})
        existing.add(command_text)


def _emit_command_complete_event(
    callback: VerifyProgressCallback,
    job_id: str,
    command: str,
    result: dict[str, Any],
    *,
    completed: int,
    total: int,
) -> None:
    _safe_callback_call(
        callback,
        "on_command_complete",
        job_id,
        command,
        int(result.get("exit_code", 1)),
        float(result.get("duration_seconds", 0.0)),
        completed=completed,
        total=total,
        stdout_tail=_tail_output_text(result.get("stdout", "")),
        stderr_tail=_tail_output_text(result.get("stderr", "")),
    )


def _start_command_tick_thread(
    callback: VerifyProgressCallback,
    *,
    job_id: str,
    command: str,
    timeout_seconds: int,
    stall_timeout_seconds: float | None = None,
    activity_probe: Callable[[], float] | None = None,
    auto_cancel_on_stall: bool = False,
    cancel_event: threading.Event | None = None,
    on_stall_auto_cancel: Callable[[float], None] | None = None,
) -> tuple[threading.Event, float, threading.Thread]:
    stop_event = threading.Event()
    started = time.perf_counter()
    timeout_sec = max(1.0, float(timeout_seconds))
    stall_timeout = None
    if stall_timeout_seconds is not None:
        stall_timeout = max(1.0, float(stall_timeout_seconds))
    auto_cancel_triggered = False

    def _ticker() -> None:
        nonlocal auto_cancel_triggered
        while not stop_event.wait(1.0):
            elapsed = max(0.0, time.perf_counter() - started)
            progress_pct = min(99.0, (elapsed / timeout_sec) * 100.0)
            eta_sec = max(0.0, timeout_sec - elapsed)
            last_activity = started
            if activity_probe is not None:
                try:
                    last_activity = float(activity_probe())
                except (TypeError, ValueError):
                    last_activity = started
            stall_detected = False
            stall_elapsed = 0.0
            if stall_timeout is not None:
                idle_sec = max(0.0, time.perf_counter() - last_activity)
                stall_detected = idle_sec >= stall_timeout
                stall_elapsed = max(0.0, idle_sec - stall_timeout)
                if stall_detected and auto_cancel_on_stall and cancel_event is not None and not auto_cancel_triggered:
                    cancel_event.set()
                    auto_cancel_triggered = True
                    if on_stall_auto_cancel is not None:
                        try:
                            on_stall_auto_cancel(idle_sec)
                        except (RuntimeError, ValueError) as e:
                            logger.debug(f"Stall auto-cancel callback failed: {e}")
            _safe_callback_call(
                callback,
                "on_heartbeat",
                job_id,
                elapsed,
                eta_sec,
                "running",
                current_command=command,
                command_elapsed_sec=elapsed,
                command_timeout_sec=timeout_sec,
                command_progress_pct=progress_pct,
                stall_detected=stall_detected if stall_timeout is not None else None,
                stall_elapsed_sec=stall_elapsed if stall_timeout is not None else None,
            )

    thread = threading.Thread(target=_ticker, daemon=True)
    thread.start()
    return stop_event, started, thread
