"""Event-correlation and TruthLog plumbing for the turn transaction kernel.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

This module hosts the free-function implementations behind the correlation
helpers owned by :class:`TurnTransactionController`. The controller keeps thin
static/class entrypoints that delegate here so that

* the by-class call site ``TurnTransactionController._attach_event_correlation``
  (exercised directly in tests) keeps working, and
* the correlation ContextVar singletons live in exactly ONE module.

Bodies were moved verbatim from ``turn_transaction_controller.py``; only the
``self``/``cls`` plumbing was replaced by explicit free-function arguments.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass, replace
from typing import Any, cast
from uuid import uuid4

from polaris.cells.roles.kernel.internal.transaction.truthlog_recorder import (
    TurnTruthLogRecorder,
)
from polaris.cells.roles.kernel.public.turn_events import TurnEvent
from polaris.cells.storage.layout.public.service import resolve_polaris_roots

logger = logging.getLogger(__name__)

# Correlation ContextVars — single-instance, owned here. Controller entrypoints
# import these so execute()/execute_stream()/_emit_phase_event() share the same
# objects (do NOT redefine them in the controller).
_TURN_REQUEST_ID_CONTEXT: ContextVar[str | None] = ContextVar("_turn_request_id_context", default=None)
_TURN_SPAN_ID_CONTEXT: ContextVar[str | None] = ContextVar("_turn_span_id_context", default=None)
_TURN_PARENT_SPAN_ID_CONTEXT: ContextVar[str | None] = ContextVar("_turn_parent_span_id_context", default=None)


def generate_turn_request_id() -> str:
    """生成单次 execute_stream 调用内稳定的 request id。"""
    return f"turnreq_{uuid4().hex}"


def generate_span_id(*, prefix: str = "span") -> str:
    """生成 span id。"""
    return f"{prefix}_{uuid4().hex}"


def attach_event_correlation(
    event: TurnEvent,
    *,
    turn_request_id: str | None,
    turn_span_id: str | None,
    parent_span_id: str | None,
) -> TurnEvent:
    """给事件附加 correlation 信息（request/span/parent_span）。"""
    has_request_field = hasattr(event, "turn_request_id")
    has_span_field = hasattr(event, "span_id")
    has_parent_span_field = hasattr(event, "parent_span_id")
    if not any((has_request_field, has_span_field, has_parent_span_field)):
        return event

    updates: dict[str, Any] = {}

    if has_request_field and turn_request_id is not None and getattr(event, "turn_request_id", None) != turn_request_id:
        updates["turn_request_id"] = turn_request_id

    if has_span_field:
        current_span = getattr(event, "span_id", None)
        if not current_span:
            updates["span_id"] = generate_span_id(prefix="span")

    if has_parent_span_field:
        current_parent = getattr(event, "parent_span_id", None)
        resolved_parent = parent_span_id or turn_span_id
        if not current_parent and resolved_parent:
            updates["parent_span_id"] = resolved_parent

    if not updates:
        return event
    return cast(TurnEvent, replace(cast(Any, event), **updates))


def resolve_workspace_for_truthlog(context: list[dict]) -> str:
    """Resolve workspace path for turn truthlog persistence."""
    for message in reversed(context):
        if not isinstance(message, Mapping):
            continue

        metadata = message.get("metadata")
        if isinstance(metadata, Mapping):
            workspace = str(metadata.get("workspace", "") or metadata.get("cwd", "")).strip()
            if workspace:
                return os.path.abspath(workspace)

        workspace_direct = str(message.get("workspace", "") or message.get("cwd", "")).strip()
        if workspace_direct:
            return os.path.abspath(workspace_direct)

    env_workspace = str(os.environ.get("KERNELONE_WORKSPACE", "")).strip()
    if env_workspace:
        return os.path.abspath(env_workspace)
    return os.path.abspath(os.getcwd())


def build_turn_truthlog_recorder(context: list[dict]) -> TurnTruthLogRecorder | None:
    """Build per-turn truthlog recorder. Failures are non-fatal for turn execution."""
    try:
        workspace = resolve_workspace_for_truthlog(context)
        runtime_root = resolve_polaris_roots(workspace).runtime_root
        log_path = os.path.join(runtime_root, "events", "kernel.turn.truthlog.events.jsonl")
        return TurnTruthLogRecorder(log_path)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("turn truthlog recorder init failed: %s", exc)
        return None


async def record_turn_truthlog_event(
    recorder: TurnTruthLogRecorder,
    *,
    event: TurnEvent,
    turn_id_fallback: str,
    turn_request_id_fallback: str,
) -> None:
    """Best-effort append of one turn event into TruthLog."""
    if is_dataclass(event) and not isinstance(event, type):
        payload: Any = asdict(event)  # type: ignore[arg-type]  # is_dataclass narrows to DataclassInstance
    else:
        payload = {"repr": repr(event)}

    event_turn_id = str(getattr(event, "turn_id", turn_id_fallback) or turn_id_fallback)
    event_request_id = str(getattr(event, "turn_request_id", turn_request_id_fallback) or turn_request_id_fallback)
    event_type = type(event).__name__

    try:
        await recorder.record(
            turn_id=event_turn_id,
            turn_request_id=event_request_id,
            event_type=event_type,
            payload=payload,
        )
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning("turn truthlog record failed: %s", exc)


async def shutdown_turn_truthlog_recorder(recorder: TurnTruthLogRecorder) -> None:
    """Best-effort flush and shutdown for TruthLog recorder."""
    try:
        await recorder.flush()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning("turn truthlog flush failed: %s", exc)
    try:
        await recorder.shutdown()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning("turn truthlog shutdown failed: %s", exc)
