"""Runner utilities for verify orchestrator."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ...utils import utc_now_iso as _utc_now
from ..runners import run_command
from .formatters import normalize_positive_int
from .report_generator import append_jsonl, append_line

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from pathlib import Path


def invoke_run_command(
    command: str,
    project_dir: Path,
    timeout_seconds: int,
    *,
    output_callback: Callable[[str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    stall_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Invoke run_command with optional kwargs."""
    kwargs: dict[str, Any] = {}
    if output_callback is not None:
        kwargs["output_callback"] = output_callback
    if cancel_event is not None:
        kwargs["cancel_event"] = cancel_event
    if stall_timeout_seconds is not None:
        kwargs["stall_timeout_seconds"] = stall_timeout_seconds
    if kwargs:
        try:
            return run_command(command, project_dir, timeout_seconds, **kwargs)
        except TypeError:
            if output_callback is not None:
                try:
                    return run_command(
                        command,
                        project_dir,
                        timeout_seconds,
                        output_callback=output_callback,
                    )
                except TypeError:
                    pass
    return run_command(command, project_dir, timeout_seconds)


def run_with_timeout_detection(
    command: str,
    project_dir: Path,
    timeout_seconds: int,
    log_path: Path,
    jsonl_path: Path,
    output_callback: Callable[[str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    stall_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run command with enhanced timeout detection and logging."""
    start_time = time.perf_counter()
    try:
        result = invoke_run_command(
            command,
            project_dir,
            timeout_seconds,
            output_callback=output_callback,
            cancel_event=cancel_event,
            stall_timeout_seconds=stall_timeout_seconds,
        )
        elapsed = time.perf_counter() - start_time
        append_line(log_path, f"COMMAND_COMPLETE {command} DURATION={elapsed:.3f}s")
        return result
    except (RuntimeError, ValueError) as exc:
        elapsed = time.perf_counter() - start_time
        append_line(log_path, f"COMMAND_ERROR {command} DURATION={elapsed:.3f}s ERROR={exc!r}")
        append_jsonl(
            jsonl_path,
            {
                "event": "command_error",
                "command": command,
                "duration_seconds": elapsed,
                "error": str(exc),
                "ts": _utc_now(),
            },
        )
        return {
            "command": command,
            "exit_code": 1,
            "duration_seconds": elapsed,
            "stdout": "",
            "stderr": f"agent-accel error: {exc}",
            "timed_out": False,
            "cancelled": False,
            "stalled": False,
            "cancel_reason": "",
        }


def store_cache_result(
    entries: dict[str, dict[str, Any]],
    key: str,
    command: str,
    result: dict[str, Any],
    *,
    cache_failed_results: bool,
    failed_ttl_seconds: int,
    utc_now_fn: Callable[[], str],
    is_failure_fn: Callable[[dict[str, Any]], bool],
) -> bool:
    """Store result in cache if appropriate."""
    from ..orchestrator_helpers import _is_failure

    is_failure = _is_failure(result) or bool(result.get("timed_out", False))
    if is_failure and not cache_failed_results:
        return False
    cache_kind = "failure" if is_failure else "success"
    ttl_seconds = normalize_positive_int(failed_ttl_seconds, 120) if is_failure else None
    entries[key] = {
        "saved_utc": utc_now_fn(),
        "command": command,
        "cache_kind": cache_kind,
        "ttl_seconds": ttl_seconds,
        "result": {
            "exit_code": int(result.get("exit_code", 0)),
            "duration_seconds": float(result.get("duration_seconds", 0.0)),
            "stdout": str(result.get("stdout", "")),
            "stderr": str(result.get("stderr", "")),
            "timed_out": bool(result.get("timed_out", False)),
            "cancelled": bool(result.get("cancelled", False)),
            "stalled": bool(result.get("stalled", False)),
            "cancel_reason": str(result.get("cancel_reason", "")),
        },
    }
    return True


__all__ = [
    "invoke_run_command",
    "run_with_timeout_detection",
    "store_cache_result",
]
