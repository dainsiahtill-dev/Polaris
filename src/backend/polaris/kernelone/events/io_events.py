"""Event emission utilities for KernelOne."""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

from polaris.kernelone._runtime_config import (
    get_workspace,
    get_workspace_metadata_dir_name,
    resolve_env_float,
)
from polaris.kernelone.events.message_bus import Message, MessageType
from polaris.kernelone.events.realtime_bridge import (
    LLMRealtimeEvent,
    publish_llm_realtime_event,
)
from polaris.kernelone.events.registry import get_global_bus
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.jsonl.ops import _commit_seq_for_path, _next_seq_for_path
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.utils.time_utils import utc_now_str

logger = logging.getLogger(__name__)

_dialogue_seq_lock = Lock()
_dialogue_seq = 0
_event_seq_lock = Lock()
_event_seq = 0
_event_guard_lock = Lock()
_event_last_by_path: dict[str, tuple[str, float]] = {}
_EVENT_DEDUP_WINDOW_SEC = max(0.0, resolve_env_float("runtime_event_dedup_window_sec"))
_llm_event_guard_lock = Lock()
_llm_event_last_by_path: dict[str, tuple[str, float]] = {}
_LLM_EVENT_DEDUP_WINDOW_SEC = max(0.0, resolve_env_float("llm_event_dedup_window_sec"))


def _infer_workspace_for_path(path: str) -> str:
    candidate = os.path.abspath(str(path or ""))
    # Use configurable workspace metadata dir name (logical prefix) instead of hardcoded ".polaris"
    metadata_name = get_workspace_metadata_dir_name()
    marker = f"{os.sep}{metadata_name}{os.sep}"
    marker_index = candidate.find(marker)
    if marker_index > 0:
        guessed = candidate[:marker_index]
        if os.path.isdir(guessed):
            return guessed
    # Priority: KERNELONE_WORKSPACE (via _runtime_config), then KERNELONE_WORKSPACE fallback
    configured = get_workspace()
    if configured:
        return os.path.abspath(configured)
    return os.getcwd()


def _append_jsonl_direct(path: str, payload: dict[str, Any]) -> None:
    """Append a JSONL record directly to *path* using the default adapter.

    Used as fallback when the path is outside KernelOne workspace roots
    (e.g., in temp directories during tests).
    """
    line = json.dumps(payload, ensure_ascii=False)
    adapter = get_default_adapter()
    adapter.append_text(str(Path(path)), line + "\n", encoding="utf-8")


def _append_jsonl_via_kernel(path: str, payload: dict[str, Any]) -> None:
    """通过 KernelFileSystem 追加 JSONL。

    Args:
        path: 文件路径
        payload: 要写入的数据

    Raises:
        OSError: 文件系统错误 (PermissionError, FileNotFoundError 等)
        RuntimeError: 内部错误
    """
    if not path:
        return
    try:
        workspace_root = _infer_workspace_for_path(path)
        metadata_dir = get_workspace_metadata_dir_name()
        candidate = Path(str(path or ""))
        if candidate.is_absolute() and metadata_dir:
            metadata_root = Path(workspace_root) / metadata_dir
            with contextlib.suppress(ValueError):
                candidate.relative_to(metadata_root)
                _append_jsonl_direct(path, payload)
                return

        fs = KernelFileSystem(workspace_root, get_default_adapter())
        fs.append_jsonl(path, payload)
    except ValueError:
        # Path is outside workspace roots (e.g., temp dir in tests).
        # Fall back to direct file write so tests and non-standard paths work.
        _append_jsonl_direct(path, payload)
    except PermissionError:
        # 权限错误不应静默失败, 记录后重新抛出
        raise
    except FileNotFoundError:
        # 文件不存在不应静默失败, 记录后重新抛出
        raise
    except (RuntimeError, OSError) as e:
        # 其他运行时错误记录后重新抛出
        logger.error("KernelOne event append failed for %s: %s", path, e, exc_info=True)
        raise


def _publish_llm_event_to_realtime_bridge(
    *,
    llm_events_path: str,
    event: str,
    role: str,
    data: dict[str, Any],
    run_id: str,
    iteration: int,
    source: str,
    timestamp: str,
) -> None:
    """Best-effort bridge from KFS-backed LLM events to realtime pipeline."""
    if not llm_events_path:
        return

    try:
        publish_llm_realtime_event(
            LLMRealtimeEvent(
                workspace=_infer_workspace_for_path(llm_events_path),
                run_id=str(run_id or "").strip(),
                role=str(role or "unknown").strip().lower() or "unknown",
                event_type=str(event or "").strip(),
                source=str(source or "system").strip() or "system",
                timestamp=str(timestamp or "").strip(),
                iteration=max(0, int(iteration or 0)),
                data=data if isinstance(data, dict) else {},
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "io_event dispatch failed event_type=%s: %s",
            str(event or "unknown"),
            exc,
            exc_info=True,
        )
        # Realtime bridge failure must not break the durable JSONL audit path.


def set_dialogue_seq(n: int) -> None:
    global _dialogue_seq
    with _dialogue_seq_lock:
        _dialogue_seq = n


def set_event_seq(n: int) -> None:
    global _event_seq
    with _event_seq_lock:
        _event_seq = n


def _next_dialogue_seq() -> int:
    global _dialogue_seq
    with _dialogue_seq_lock:
        _dialogue_seq += 1
        return _dialogue_seq


def _next_event_seq() -> int:
    global _event_seq
    with _event_seq_lock:
        _event_seq += 1
        return _event_seq


def get_event_seq() -> int:
    with _event_seq_lock:
        return _event_seq


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _normalize_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_event_payload(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_normalize_event_payload(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _build_runtime_event_fingerprint(
    *,
    kind: str,
    actor: str,
    name: str,
    refs: dict[str, Any],
    summary: str,
    meta: dict[str, Any],
    input: dict[str, Any],
    ok: bool | None,
    output: dict[str, Any],
    error: str,
) -> str:
    payload = {
        "kind": str(kind or "").strip().lower(),
        "actor": str(actor or "").strip(),
        "name": str(name or "").strip().lower(),
        "refs": _normalize_event_payload(refs),
        "summary": str(summary or "").strip(),
        "meta": _normalize_event_payload(meta),
        "input": _normalize_event_payload(input),
        "ok": None if ok is None else bool(ok),
        "output": _normalize_event_payload(output),
        "error": str(error or "").strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cleanup_stale_events(
    state: dict[str, tuple[str, float]],
    now_ts: float,
    dedup_window: float,
) -> None:
    """清理过期的去重记录。"""
    stale_before = now_ts - max(dedup_window * 10.0, 30.0)

    with _event_guard_lock:
        stale_keys = [entry_key for entry_key, (_, ts) in state.items() if ts < stale_before]
        for stale_key in stale_keys:
            state.pop(stale_key, None)


def _should_suppress_runtime_event(path: str, fingerprint: str, now_ts: float) -> bool:
    """检查是否应该抑制重复事件。"""
    if _EVENT_DEDUP_WINDOW_SEC <= 0:
        return False

    key = os.path.abspath(str(path or ""))
    if not key:
        return False

    with _event_guard_lock:
        previous = _event_last_by_path.get(key)
        if previous and previous[0] == fingerprint and (now_ts - previous[1]) <= _EVENT_DEDUP_WINDOW_SEC:
            return True
        _event_last_by_path[key] = (fingerprint, now_ts)

    # 在锁外执行清理 (异步), 避免高并发时锁持有时间过长
    if len(_event_last_by_path) > 1024:
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(0, _cleanup_stale_events, _event_last_by_path, now_ts, _EVENT_DEDUP_WINDOW_SEC)
        except RuntimeError:
            # 没有运行中的事件循环, 同步清理
            _cleanup_stale_events(_event_last_by_path, now_ts, _EVENT_DEDUP_WINDOW_SEC)

    return False


def _normalize_llm_event_data(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    keys = (
        "stage",
        "backend",
        "error",
        "summary",
        "message",
        "task_count",
        "output_chars",
        "preview",
        "attempt",
        "max_attempts",
        "exit_code",
    )
    normalized: dict[str, Any] = {}
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            compact = value.strip()
            if compact:
                normalized[key] = compact
        elif isinstance(value, (int, float, bool)):
            normalized[key] = value
    return normalized


def _build_llm_event_fingerprint(
    *,
    event: str,
    role: str,
    source: str,
    run_id: str,
    iteration: int,
    data: dict[str, Any],
) -> str:
    payload = {
        "event": str(event or "").strip().lower(),
        "role": str(role or "").strip().lower(),
        "source": str(source or "").strip().lower(),
        "run_id": str(run_id or "").strip(),
        "iteration": int(iteration or 0),
        "data": _normalize_llm_event_data(data),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cleanup_stale_llm_events(
    state: dict[str, tuple[str, float]],
    now_ts: float,
    dedup_window: float,
) -> None:
    """清理过期的 LLM 事件去重记录。"""
    stale_before = now_ts - max(dedup_window * 10.0, 30.0)

    with _llm_event_guard_lock:
        stale_keys = [entry_key for entry_key, (_, ts) in state.items() if ts < stale_before]
        for stale_key in stale_keys:
            state.pop(stale_key, None)


def _should_suppress_llm_event(path: str, fingerprint: str, now_ts: float) -> bool:
    """检查是否应该抑制重复的 LLM 事件。"""
    if _LLM_EVENT_DEDUP_WINDOW_SEC <= 0:
        return False

    key = os.path.abspath(str(path or ""))
    if not key:
        return False

    with _llm_event_guard_lock:
        previous = _llm_event_last_by_path.get(key)
        if previous and previous[0] == fingerprint and (now_ts - previous[1]) <= _LLM_EVENT_DEDUP_WINDOW_SEC:
            return True
        _llm_event_last_by_path[key] = (fingerprint, now_ts)

    # 在锁外执行清理 (异步), 避免高并发时锁持有时间过长
    if len(_llm_event_last_by_path) > 1024:
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(0, _cleanup_stale_llm_events, _llm_event_last_by_path, now_ts, _LLM_EVENT_DEDUP_WINDOW_SEC)
        except RuntimeError:
            # 没有运行中的事件循环, 同步清理
            _cleanup_stale_llm_events(_llm_event_last_by_path, now_ts, _LLM_EVENT_DEDUP_WINDOW_SEC)

    return False


def utc_iso_now() -> str:
    return utc_now_str()


def _publish_runtime_event_to_bus(
    *,
    topic: str,
    event_type: str,
    payload: dict[str, Any],
    sender: str = "io_events",
) -> None:
    """Publish runtime event to MessageBus for downstream subscribers.

    This ensures events flow through the event system (MessageBus) in addition
    to being written to JSONL files, enabling real-time subscribers (like Sinks)
    to receive and process events.

    Note: This is a best-effort publish. If MessageBus is unavailable,
    the event has already been durably written to JSONL, so no data is lost.

    Args:
        topic: The event topic (e.g., TOPIC_RUNTIME_STREAM, TOPIC_RUNTIME_AUDIT).
        event_type: The specific event type within the topic.
        payload: The event payload dict.
        sender: The sender identifier (default: "io_events").
    """
    try:
        bus = get_global_bus()
        if bus is None:
            # MessageBus is not bootstrapped in CLI mode; this is expected and fine.
            # Events are already durably written to JSONL, so no data is lost.
            return

        # Build UEP-style payload for MessageBus
        uep_payload: dict[str, Any] = {
            "topic": topic,
            "event_type": event_type,
            "workspace": _infer_workspace_for_path(payload.get("event_path", "")),
            "payload": dict(payload),
            "timestamp": payload.get("ts", utc_iso_now()),
        }

        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender=sender,
            recipient=None,  # broadcast
            payload=uep_payload,
        )

        # Try to publish, but don't block if MessageBus is busy
        try:
            asyncio.get_running_loop()
            # Schedule publish without awaiting to avoid blocking
            _ = asyncio.create_task(_safe_publish(bus, msg))
            logger.debug("MessageBus publish scheduled: topic=%s", topic)
        except RuntimeError:
            # No running loop, try synchronous publish
            _safe_publish_sync(bus, msg)
    except (RuntimeError, ValueError) as exc:
        # Never let MessageBus publish failure affect the JSONL write
        logger.warning(
            "MessageBus publish failed (JSONL write succeeded): topic=%s error=%s",
            topic,
            exc,
        )


async def _safe_publish(bus: Any, msg: Message) -> None:
    """Safely publish message to MessageBus without raising."""
    try:
        await bus.publish(msg)
    except (RuntimeError, ValueError) as exc:
        logger.warning("MessageBus publish failed: %s", exc)


def _safe_publish_sync(bus: Any, msg: Message) -> None:
    """Safely publish message to MessageBus synchronously without raising.

    Uses the running event loop if available, otherwise falls back to
    running in a thread pool executor to avoid creating a new event loop
    on each call (which can cause resource leaks and unpredictable behavior).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        loop.call_soon_threadsafe(lambda: asyncio.create_task(_safe_publish(bus, msg)))
        return

    try:
        asyncio.get_event_loop().run_until_complete(_safe_publish(bus, msg))
    except (RuntimeError, ValueError) as exc:
        logger.warning("MessageBus sync publish failed: %s", exc)


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
    if not dialogue_path:
        return
    seq = _next_dialogue_seq()
    if dialogue_path:
        seq = _next_seq_for_path(dialogue_path, seq, key="seq")
        set_dialogue_seq(seq)
    payload = {
        "ts": utc_iso_now(),
        "ts_epoch": time.time(),
        "seq": seq,
        "event_id": _new_event_id(),
        "run_id": run_id,
        "pm_iteration": pm_iteration,
        "director_iteration": director_iteration,
        "speaker": speaker,
        "type": type,
        "text": text,
        "summary": summary or (text[:80] if text else ""),
        "refs": refs or {},
        "meta": meta or {},
    }
    _append_jsonl_via_kernel(dialogue_path, payload)


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
    if not event_path:
        return
    refs_payload = refs if isinstance(refs, dict) else {}
    meta_payload = meta if isinstance(meta, dict) else {}
    input_payload = input if isinstance(input, dict) else {}
    output_payload = output if isinstance(output, dict) else {}
    error_text = str(error or "").strip()
    now_ts = time.time()
    fingerprint = _build_runtime_event_fingerprint(
        kind=kind,
        actor=actor,
        name=name,
        refs=refs_payload,
        summary=summary,
        meta=meta_payload,
        input=input_payload,
        ok=ok,
        output=output_payload,
        error=error_text,
    )
    if _should_suppress_runtime_event(event_path, fingerprint, now_ts):
        return
    seq = _next_event_seq()
    # BUG-FIX: Sequence must ONLY be committed AFTER successful event write.
    # Previously, _next_seq_for_path() updated .seq file BEFORE _append_jsonl_via_kernel()
    # which could cause seq/event mismatch if the write failed or returned early.
    pending_seq: int | None = None
    if event_path:
        # Compute next sequence WITHOUT writing to .seq file
        pending_seq = _next_seq_for_path(event_path, seq, key="seq", commit=False)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "ts": utc_iso_now(),
        "ts_epoch": now_ts,
        "seq": pending_seq if pending_seq is not None else seq,
        "event_id": _new_event_id(),
        "kind": kind,
        "actor": actor,
        "name": name,
        "refs": refs_payload,
        "summary": summary or "",
        "meta": meta_payload,
    }
    if kind == "action":
        payload["input"] = input_payload
    else:
        payload["ok"] = True if ok is None else bool(ok)
        payload["output"] = output_payload
        payload["truncation"] = truncation or {"truncated": False}
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if error_text:
            payload["error"] = error_text

    # Store event_path for MessageBus publish (not part of JSONL payload)
    payload["event_path"] = event_path

    try:
        _append_jsonl_via_kernel(event_path, payload)
    except Exception:
        # If JSONL write fails, do NOT commit the sequence - it will be
        # re-incremented on the next attempt, ensuring seq/event consistency.
        raise

    # BUG-FIX: Only commit sequence AFTER confirmed successful event write
    if pending_seq is not None:
        # Use _commit_seq_for_path to directly write pending_seq with proper locking,
        # avoiding re-computing which could cause inconsistency if .seq changed.
        _commit_seq_for_path(event_path, pending_seq)

    # P2-006 Fix: Also publish to MessageBus for real-time subscribers
    # JSONL write is the durable persistence layer; MessageBus enables real-time
    # distribution to subscribers (e.g., JournalSink, ArchiveSink, AuditHashSink).
    _publish_runtime_event_to_bus(
        topic="runtime.audit",
        event_type=f"runtime.{kind}",
        payload=payload,
        sender="io_events.emit_event",
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
    if not llm_events_path:
        return

    event_name = str(event or "").strip()
    role_name = str(role or "").strip().lower() or "unknown"
    source_name = str(source or "").strip().lower() or "system"
    run_name = str(run_id or "").strip()
    iteration_num = int(iteration or 0)
    event_data = data if isinstance(data, dict) else {}

    now_ts = time.time()
    fingerprint = _build_llm_event_fingerprint(
        event=event_name,
        role=role_name,
        source=source_name,
        run_id=run_name,
        iteration=iteration_num,
        data=event_data,
    )
    if _should_suppress_llm_event(llm_events_path, fingerprint, now_ts):
        return

    seq = _next_event_seq()
    seq = _next_seq_for_path(llm_events_path, seq, key="seq")
    set_event_seq(seq)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "ts": utc_iso_now(),
        "ts_epoch": now_ts,
        "seq": seq,
        "event_id": _new_event_id(),
        "run_id": run_name,
        "iteration": iteration_num,
        "role": role_name,
        "source": source_name,
        "event": event_name,
        "data": event_data,
    }

    # Store llm_events_path for MessageBus publish (not part of JSONL payload)
    payload["llm_events_path"] = llm_events_path

    _append_jsonl_via_kernel(llm_events_path, payload)
    _publish_llm_event_to_realtime_bridge(
        llm_events_path=llm_events_path,
        event=event_name,
        role=role_name,
        data=event_data,
        run_id=run_name,
        iteration=iteration_num,
        source=source_name,
        timestamp=str(payload.get("ts") or ""),
    )

    # P2-006 Fix: Also publish to MessageBus for real-time subscribers
    # JSONL write is the durable persistence layer; MessageBus enables real-time
    # distribution to subscribers (e.g., JournalSink, ArchiveSink, AuditHashSink).
    _publish_runtime_event_to_bus(
        topic="runtime.llm",
        event_type=f"llm.{event_name}",
        payload=payload,
        sender="io_events.emit_llm_event",
    )
