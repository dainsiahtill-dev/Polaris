"""
DEPRECATED MODULE - io_utils Compatibility Layer

.. deprecated::
   ``polaris.infrastructure.compat.io_utils`` is deprecated.
   This module preserves the former ``io_utils`` import surface for callers
   that still need a broad utility namespace.

   Migration: All capabilities should be migrated back to their respective
   KernelOne contracts or Cell-local ports.

   Canonical locations:
   - File I/O: ``polaris.kernelone.fs.*``
   - JSONL: ``polaris.kernelone.fs.jsonl.ops``
   - Events: ``polaris.kernelone.events``
   - Storage: ``polaris.kernelone.storage.*``
   - Tool execution: ``polaris.kernelone.tool_execution``

   This module will be removed in a future release.
   All new code should import directly from the canonical locations.
"""

from __future__ import annotations

import logging
import os
import warnings
from typing import Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.events import io_events
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.control_flags import (
    clear_director_stop_flag,
    clear_stop_flag,
    director_stop_flag_path,
    director_stop_requested,
    interrupt_notice_path,
    pause_flag_path,
    pause_requested,
    stop_flag_path,
    stop_requested,
)
from polaris.kernelone.fs.encoding import build_utf8_env, enforce_utf8
from polaris.kernelone.fs.fsync_mode import is_fsync_enabled
from polaris.kernelone.fs.jsonl import ops as io_jsonl_ops
from polaris.kernelone.fs.memory_snapshot import (
    ensure_memory_dir,
    get_memory_summary,
    read_memory_snapshot as _read_memory_snapshot_fallback,
    write_loop_warning as _write_loop_warning_fallback,
)
from polaris.kernelone.fs.text_ops import (
    ensure_parent_dir,
    extract_field,
    read_file_safe as _read_file_safe_fallback,
)
from polaris.kernelone.storage import (
    default_ramdisk_root,
    normalize_ramdisk_root,
    resolve_ramdisk_root,
    state_to_ramdisk_enabled,
)
from polaris.kernelone.storage.io_paths import (
    build_cache_root,
    find_workspace_root,
    is_hot_artifact_path,
    normalize_artifact_rel_path,
    resolve_artifact_path,
    resolve_run_dir,
    resolve_workspace_path,
    update_latest_pointer,
    workspace_has_docs,
)
from polaris.kernelone.tool_execution import io_tools

# Emit deprecation warning when this module is imported
warnings.warn(
    "polaris.infrastructure.compat.io_utils is deprecated. "
    "Import from canonical KernelOne modules instead. "
    "See: MIGRATION_DEBT_INVENTORY_20260409.md",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger(__name__)

# Re-export compatibility layer (deprecated, migrate to KernelOne)


def ensure_process_utf8() -> None:
    """Explicit UTF-8 bootstrap hook for CLI entrypoints."""
    enforce_utf8()


def _infer_workspace_for_path(path: str) -> str:
    candidate = os.path.abspath(str(path or ""))
    # Support both legacy POLARIS metadata dir and current KERNELONE dir
    # via the dynamic bootstrap-configured name.
    metadata_name = get_workspace_metadata_dir_name()
    marker = f"{os.sep}{metadata_name}{os.sep}"
    marker_index = candidate.find(marker)
    if marker_index > 0:
        workspace_guess = candidate[:marker_index]
        if os.path.isdir(workspace_guess):
            return workspace_guess

    configured = str(os.environ.get("KERNELONE_WORKSPACE") or "").strip()
    if configured:
        return os.path.abspath(configured)
    return os.getcwd()


def _kernel_fs_for_path(path: str) -> KernelFileSystem:
    return KernelFileSystem(_infer_workspace_for_path(path), get_default_adapter())


def _resolve_jsonl_path(path: str) -> str:
    if not path:
        return ""
    if os.path.isabs(path):
        return os.path.abspath(path)
    return str(_kernel_fs_for_path(path).resolve_path(path))


def write_text_atomic(path: str, text: str, *, encoding: str = "utf-8") -> None:
    """Write UTF-8 text through the KernelFileSystem boundary."""
    if not path:
        return
    _kernel_fs_for_path(path).write_text(path, text or "", encoding=encoding)


def write_json_atomic(path: str, data: dict[str, Any]) -> None:
    """Write JSON through the KernelFileSystem boundary."""
    if not path:
        return
    _kernel_fs_for_path(path).write_json(path, data, indent=2, ensure_ascii=False)


def append_jsonl_atomic(
    path: str,
    obj: dict[str, Any],
    lock_timeout_sec: float = 5.0,
) -> None:
    """Append one JSONL record using the canonical JSONL backend."""
    io_jsonl_ops.append_jsonl_atomic(
        _resolve_jsonl_path(path),
        obj,
        lock_timeout_sec=lock_timeout_sec,
    )


def append_jsonl(
    path: str,
    obj: dict[str, Any],
    lock_timeout_sec: float = 5.0,
    buffered: bool | None = None,
) -> None:
    """Append one JSONL record using the canonical JSONL backend."""
    io_jsonl_ops.append_jsonl(
        _resolve_jsonl_path(path),
        obj,
        lock_timeout_sec=lock_timeout_sec,
        buffered=buffered,
    )


def configure_jsonl_buffer(
    buffered: bool | None = None,
    flush_interval_sec: float | None = None,
    flush_batch: int | None = None,
    max_buffer: int | None = None,
    lock_stale_sec: float | None = None,
    buffer_ttl_sec: float | None = None,
    max_paths: int | None = None,
    cleanup_interval_sec: float | None = None,
) -> None:
    io_jsonl_ops.configure_jsonl_buffer(
        buffered=buffered,
        flush_interval_sec=flush_interval_sec,
        flush_batch=flush_batch,
        max_buffer=max_buffer,
        lock_stale_sec=lock_stale_sec,
        buffer_ttl_sec=buffer_ttl_sec,
        max_paths=max_paths,
        cleanup_interval_sec=cleanup_interval_sec,
    )


def flush_jsonl_buffers(force: bool = False, lock_timeout_sec: float = 5.0) -> None:
    io_jsonl_ops.flush_jsonl_buffers(force=force, lock_timeout_sec=lock_timeout_sec)


def _fsync_enabled() -> bool:
    return is_fsync_enabled()


def set_dialogue_seq(n: int) -> None:
    io_events.set_dialogue_seq(n)


def set_event_seq(n: int) -> None:
    io_events.set_event_seq(n)


def get_event_seq() -> int:
    return io_events.get_event_seq()


def scan_last_seq(path: str, key: str = "seq") -> int:
    return io_jsonl_ops.scan_last_seq(_resolve_jsonl_path(path), key=key)


def emit_dialogue(
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
    io_events.emit_dialogue(
        dialogue_path,
        speaker=speaker,
        type=type,
        text=text,
        summary=summary,
        run_id=run_id,
        pm_iteration=pm_iteration,
        director_iteration=director_iteration,
        refs=refs,
        meta=meta,
    )


def emit_event(
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
    io_events.emit_event(
        event_path,
        kind=kind,
        actor=actor,
        name=name,
        refs=refs,
        summary=summary,
        meta=meta,
        input=input,
        ok=ok,
        output=output,
        truncation=truncation,
        duration_ms=duration_ms,
        error=error,
    )


def emit_llm_event(
    llm_events_path: str,
    *,
    event: str,
    role: str,
    data: dict[str, Any],
    run_id: str = "",
    iteration: int = 0,
    source: str = "system",
) -> None:
    io_events.emit_llm_event(
        llm_events_path,
        event=event,
        role=role,
        data=data,
        run_id=run_id,
        iteration=iteration,
        source=source,
    )


def utc_iso_now() -> str:
    return io_events.utc_iso_now()


def resolve_codex_path() -> str | None:
    return io_tools.resolve_codex_path()


def ensure_codex_available() -> str:
    return io_tools.ensure_codex_available()


def resolve_ollama_path() -> str | None:
    return io_tools.resolve_ollama_path()


def ensure_ollama_available() -> str:
    return io_tools.ensure_ollama_available()


def ensure_tools_available() -> None:
    io_tools.ensure_tools_available()


def read_file_safe(path: str) -> str:
    """Read UTF-8 text through KFS, with a raw-file fallback for loose files."""
    if not path:
        return ""
    try:
        return _kernel_fs_for_path(path).read_text(path, encoding="utf-8")
    except (RuntimeError, ValueError):
        # SECURITY FIX (P2-014): Log fallback for audit trail.
        logger.debug("KFS read failed, using raw file fallback: path=%s", path)
        return _read_file_safe_fallback(path)


def read_memory_snapshot(path: str) -> dict[str, Any] | None:
    """Read JSON snapshot through KFS, with a raw-file fallback."""
    if not path:
        return None
    try:
        payload = _kernel_fs_for_path(path).read_json(path)
    except (RuntimeError, ValueError):
        # SECURITY FIX (P2-014): Log fallback for audit trail.
        logger.debug("KFS JSON read failed, using memory snapshot fallback: path=%s", path)
        return _read_memory_snapshot_fallback(path)
    return payload if isinstance(payload, dict) else None


def write_memory_snapshot(path: str, data: dict[str, Any]) -> None:
    """Write memory snapshot through KFS."""
    if not path:
        return
    try:
        _kernel_fs_for_path(path).write_json(path, data, indent=2, ensure_ascii=False)
    except (RuntimeError, ValueError):
        # SECURITY FIX (P2-014): Exception already logged, add context.
        logger.exception("Failed to write memory snapshot to %s", path)


def write_loop_warning(log_path: str, message: str) -> None:
    """Write loop warning to the log file and logger."""
    if not log_path:
        logger.warning("%s", message)
        return
    try:
        _kernel_fs_for_path(log_path).append_text(
            log_path,
            f"[WARN] {message}\n",
            encoding="utf-8",
        )
    except (RuntimeError, ValueError):
        # SECURITY FIX (P2-014): Log fallback for audit trail.
        logger.debug("KFS write failed, using loop warning fallback: path=%s", log_path)
        _write_loop_warning_fallback(log_path, message)
        return
    logger.warning("%s", message)


__all__ = [
    "append_jsonl",
    "append_jsonl_atomic",
    "build_cache_root",
    "build_utf8_env",
    "clear_director_stop_flag",
    "clear_stop_flag",
    "configure_jsonl_buffer",
    "default_ramdisk_root",
    "director_stop_flag_path",
    "director_stop_requested",
    "emit_dialogue",
    "emit_event",
    "emit_llm_event",
    "enforce_utf8",
    "ensure_codex_available",
    "ensure_memory_dir",
    "ensure_ollama_available",
    "ensure_parent_dir",
    "ensure_process_utf8",
    "ensure_tools_available",
    "extract_field",
    "find_workspace_root",
    "flush_jsonl_buffers",
    "get_event_seq",
    "get_memory_summary",
    "interrupt_notice_path",
    "is_hot_artifact_path",
    "normalize_artifact_rel_path",
    "normalize_ramdisk_root",
    "pause_flag_path",
    "pause_requested",
    "read_file_safe",
    "read_memory_snapshot",
    "resolve_artifact_path",
    "resolve_codex_path",
    "resolve_ollama_path",
    "resolve_ramdisk_root",
    "resolve_run_dir",
    "resolve_workspace_path",
    "scan_last_seq",
    "set_dialogue_seq",
    "set_event_seq",
    "state_to_ramdisk_enabled",
    "stop_flag_path",
    "stop_requested",
    "update_latest_pointer",
    "utc_iso_now",
    "workspace_has_docs",
    "write_json_atomic",
    "write_loop_warning",
    "write_memory_snapshot",
    "write_text_atomic",
]
