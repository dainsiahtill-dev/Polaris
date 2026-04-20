"""Legacy Event Emission Adapters.

This module provides adapters that bridge the old emit_event, emit_llm_event,
and emit_dialogue functions to the new log pipeline while maintaining
backward compatibility.

These adapters:
1. Write to the new unified pipeline (CanonicalLogEventV2)
2. Also write to the legacy files for backward compatibility
3. Fix the issues mentioned in Phase C (seq=0, runtime_events, etc.)
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.kernelone.utils.time_utils import utc_now_str

_logger = logging.getLogger(__name__)

# Import the new writer and context
from .run_context import (
    resolve_current_run_id,
)
from .writer import LogEventWriter, get_writer

if TYPE_CHECKING:
    from .canonical_event import (
        LogChannel,
        LogSeverity,
    )

# Import append functions from io_jsonl_ops
try:
    from polaris.kernelone.fs.jsonl.ops import append_jsonl, append_jsonl_atomic
except ImportError:
    # Fallback for script mode - signatures must match the real functions
    def append_jsonl(
        path: str,
        obj: dict[str, Any],
        lock_timeout_sec: float = 5.0,
        buffered: bool | None = None,
    ) -> None:
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def append_jsonl_atomic(
        path: str,
        obj: dict[str, Any],
        lock_timeout_sec: float = 5.0,
    ) -> None:
        append_jsonl(path, obj, lock_timeout_sec)

    def _next_seq_for_path(path: str, fallback_seq: int, key: str = "seq") -> int:
        return fallback_seq


def _new_event_id() -> str:
    return str(uuid.uuid4())


def get_legacy_channel_path(channel: str, workspace: str) -> str:
    """Resolve the legacy channel path.

    Args:
        channel: Legacy channel name (e.g., 'pm_log', 'runtime_events')
        workspace: Workspace directory

    Returns:
        Full path to the legacy channel file
    """
    # Import here to avoid circular imports
    try:
        from polaris.domain.director.constants import CHANNEL_FILES
        from polaris.kernelone.storage.io_paths import resolve_artifact_path
    except ImportError:
        return ""

    rel_path = CHANNEL_FILES.get(channel, "")
    if not rel_path:
        return ""

    return resolve_artifact_path(workspace, "", rel_path) or ""


def _get_writer_for_workspace(workspace: str) -> LogEventWriter | None:
    """Get or create a writer for the workspace."""
    run_id = resolve_current_run_id()
    if not run_id:
        return None
    try:
        return get_writer(workspace, run_id)
    except (RuntimeError, ValueError):
        return None


def adapt_emit_event(
    event_path: str,
    *,
    kind: str,
    actor: str,
    name: str,
    refs: dict[str, Any] | None = None,
    summary: str = "",
    meta: dict[str, Any] | None = None,
    input: dict[str, Any] | None = None,
    ok: bool | None = None,
    output: dict[str, Any] | None = None,
    truncation: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Adapter for emit_event - writes to both legacy and new pipeline.

    This function:
    1. Writes to the legacy file for backward compatibility
    2. Also writes to the new unified pipeline
    3. Fixes seq=0 issue by using proper sequence numbers
    """
    if not event_path:
        return

    # Get workspace from path
    workspace = os.getcwd()
    # Try to extract workspace from event_path
    if "runtime" in event_path:
        parts = event_path.split("runtime")
        if parts[0]:
            workspace = parts[0].rstrip("/\\")

    seq = 0

    # Legacy: Generate sequence using original logic
    try:
        seq = _next_seq_for_path(event_path, 1, key="seq")
    except (RuntimeError, ValueError):
        seq = int(time.time() * 1000) % 100000

    # FIX: Prevent seq=0 by ensuring minimum seq of 1
    if seq == 0:
        seq = 1

    # Build legacy payload
    legacy_payload: dict[str, Any] = {
        "schema_version": 1,
        "ts": utc_now_str(),
        "ts_epoch": time.time(),
        "seq": seq,
        "event_id": _new_event_id(),
        "kind": kind,
        "actor": actor,
        "name": name,
        "refs": refs or {},
        "summary": summary or "",
        "meta": meta or {},
    }

    if kind == "action":
        legacy_payload["input"] = input or {}
    else:
        legacy_payload["ok"] = True if ok is None else bool(ok)
        legacy_payload["output"] = output or {}
        legacy_payload["truncation"] = truncation or {"truncated": False}
        if duration_ms is not None:
            legacy_payload["duration_ms"] = duration_ms
        if error:
            legacy_payload["error"] = error

    # Write to legacy file (backward compatibility)
    try:
        append_jsonl_atomic(event_path, legacy_payload)
    except (RuntimeError, ValueError) as exc:
        _logger.debug("legacy event write failed (non-critical): path=%s: %s", event_path, exc)

    # Also write to new pipeline
    run_id = resolve_current_run_id()
    if run_id:
        writer = _get_writer_for_workspace(workspace)
        if writer:
            try:
                # Determine channel from path
                channel: LogChannel = "system"
                if "subprocess" in event_path or "console" in event_path:
                    channel = "process"
                elif "llm" in event_path:
                    channel = "llm"

                # Determine severity
                severity: LogSeverity = "info"
                if error or kind == "error":
                    severity = "error"

                writer.write_event(
                    message=summary or name,
                    channel=channel,
                    domain="system",
                    severity=severity,
                    kind=kind if kind in {"action", "observation", "state", "output", "error"} else "observation",  # type: ignore[arg-type]
                    actor=actor,
                    source=event_path.rsplit("/", maxsplit=1)[-1].replace(".jsonl", ""),
                    refs=refs or {},
                    raw=legacy_payload,
                    input_data=input,
                    output_data=output,
                    error=error,
                    duration_ms=duration_ms,
                )
            except (RuntimeError, ValueError) as exc:
                _logger.debug("pipeline write failed (non-critical): %s", exc)


def adapt_emit_llm_event(
    llm_events_path: str,
    *,
    event: str,
    role: str,
    data: dict[str, Any],
    run_id: str = "",
    iteration: int = 0,
    source: str = "system",
) -> None:
    """Adapter for emit_llm_event - writes to both legacy and new pipeline.

    This function:
    1. Writes to the legacy file for backward compatibility
    2. Also writes to the new unified pipeline (llm channel)
    3. Fixes target mixing issue by always using 'llm' channel
    """
    if not llm_events_path:
        return

    # Get workspace from path
    workspace = os.getcwd()
    if "runtime" in llm_events_path:
        parts = llm_events_path.split("runtime")
        if parts[0]:
            workspace = parts[0].rstrip("/\\")

    # Use run_id from parameter or resolve from context
    effective_run_id = run_id or resolve_current_run_id()

    seq = 0

    # Legacy: Generate sequence using original logic
    try:
        seq = _next_seq_for_path(llm_events_path, 1, key="seq")
    except (RuntimeError, ValueError):
        seq = int(time.time() * 1000) % 100000

    # FIX: Prevent seq=0
    if seq == 0:
        seq = 1

    # Build legacy payload
    legacy_payload: dict[str, Any] = {
        "schema_version": 1,
        "ts": utc_now_str(),
        "ts_epoch": time.time(),
        "seq": seq,
        "event_id": _new_event_id(),
        "run_id": effective_run_id,
        "iteration": int(iteration or 0),
        "role": str(role or "").strip().lower() or "unknown",
        "source": str(source or "").strip().lower() or "system",
        "event": str(event or "").strip(),
        "data": data if isinstance(data, dict) else {},
    }

    # Write to legacy file
    try:
        append_jsonl(llm_events_path, legacy_payload)
    except (RuntimeError, ValueError) as exc:
        _logger.debug("legacy event write failed (non-critical): path=%s: %s", llm_events_path, exc)

    # Also write to new pipeline (always use 'llm' channel)
    if effective_run_id:
        writer = _get_writer_for_workspace(workspace)
        if writer:
            try:
                writer.write_event(
                    message=f"[{role}] {event}: {str(data)[:200]}",
                    channel="llm",
                    domain="llm",
                    severity="info",
                    kind="observation",
                    actor=role,
                    source=source,
                    run_id=effective_run_id,
                    refs={"iteration": iteration},
                    raw=legacy_payload,
                )
            except (RuntimeError, ValueError) as exc:
                _logger.debug("pipeline write failed (non-critical): %s", exc)


def adapt_emit_dialogue(
    dialogue_path: str,
    *,
    speaker: str,
    type: str,
    text: str,
    summary: str | None = None,
    run_id: str | None = None,
    pm_iteration: int | None = None,
    director_iteration: int | None = None,
    refs: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Adapter for emit_dialogue - writes to both legacy and new pipeline.

    This function:
    1. Writes to the legacy dialogue file for backward compatibility
    2. Also writes to the new unified pipeline
    """
    if not dialogue_path:
        return

    # Get workspace from path
    workspace = os.getcwd()
    if "runtime" in dialogue_path:
        parts = dialogue_path.split("runtime")
        if parts[0]:
            workspace = parts[0].rstrip("/\\")

    # Use run_id from parameter or resolve from context
    effective_run_id = run_id or resolve_current_run_id()

    seq = 0

    # Legacy: Generate sequence
    try:
        seq = _next_seq_for_path(dialogue_path, 1, key="seq")
    except (RuntimeError, ValueError):
        seq = int(time.time() * 1000) % 100000

    # FIX: Prevent seq=0
    if seq == 0:
        seq = 1

    # Build legacy payload
    legacy_payload = {
        "ts": utc_now_str(),
        "ts_epoch": time.time(),
        "seq": seq,
        "event_id": _new_event_id(),
        "run_id": effective_run_id,
        "pm_iteration": pm_iteration,
        "director_iteration": director_iteration,
        "speaker": speaker,
        "type": type,
        "text": text,
        "summary": summary or (text[:80] if text else ""),
        "refs": refs or {},
        "meta": meta or {},
    }

    # Write to legacy file
    try:
        append_jsonl_atomic(dialogue_path, legacy_payload)
    except (RuntimeError, ValueError) as exc:
        _logger.debug("legacy event write failed (non-critical): path=%s: %s", dialogue_path, exc)

    # Also write to new pipeline (system channel for dialogue)
    if effective_run_id:
        writer = _get_writer_for_workspace(workspace)
        if writer:
            try:
                writer.write_event(
                    message=text[:5000],
                    channel="system",
                    domain="system",
                    severity="info",
                    kind="observation",
                    actor=speaker,
                    source="dialogue",
                    run_id=effective_run_id,
                    refs=refs or {},
                    raw=legacy_payload,
                )
            except (RuntimeError, ValueError) as exc:
                _logger.debug("pipeline write failed (non-critical): %s", exc)
