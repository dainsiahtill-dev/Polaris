"""Interview routes for the LLM router."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from polaris.cells.llm.evaluation.public.service import (
    generate_interview_answer,
    generate_interview_answer_streaming,
    save_interview_report,
)
from polaris.delivery.http.routers._shared import StructuredHTTPException, get_state, legacy_sse_removed, require_auth
from polaris.delivery.http.schemas import (
    InterviewAskResponse,
    InterviewCancelResponse,
    InterviewSaveResponse,
)
from polaris.delivery.http.workspace import active_workspace_value
from polaris.infrastructure.messaging.nats.nats_types import create_runtime_event
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.llm.model_identity import model_identity_equal
from polaris.kernelone.storage.io_paths import build_cache_root

from .llm_models import InterviewAskPayload, InterviewCancelPayload, InterviewSavePayload
from .sse_utils import publish_to_jetstream

logger = logging.getLogger(__name__)
_create_internal_task = asyncio.create_task
_BACKGROUND_JETSTREAM_TASKS: set[asyncio.Task[None]] = set()
_SAFE_EVENT_ID_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


# 适配器函数，保持与旧接口的兼容性
def _active_role_binding(settings: Any, workspace: str, role: str) -> dict[str, Any] | None:
    role_key = str(role or "").strip().lower()
    if not role_key:
        return None

    try:
        cache_root = build_cache_root(str(getattr(settings, "ramdisk_root", "") or ""), workspace)
        config = llm_config.load_llm_config(workspace, cache_root, settings=settings)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.debug("Failed to load LLM config for interview readiness guard: %s", exc)
        return None

    roles = config.get("roles") if isinstance(config.get("roles"), dict) else {}
    if not isinstance(roles, dict):
        return None
    role_cfg = roles.get(role_key)
    if isinstance(role_cfg, dict):
        return role_cfg
    for key, value in roles.items():
        if str(key or "").strip().lower() == role_key and isinstance(value, dict):
            return value
    return {}


def _can_update_role_readiness(
    *,
    settings: Any,
    workspace: str,
    role: str,
    provider_id: str,
    model: str | None,
) -> bool:
    """Only the current role/provider/model binding may overwrite role readiness."""

    role_cfg = _active_role_binding(settings, workspace, role)
    if role_cfg is None:
        return True
    if not role_cfg:
        return False

    active_provider = str(role_cfg.get("provider_id") or "").strip()
    active_model = str(role_cfg.get("model") or "").strip()
    tested_provider = str(provider_id or "").strip()
    tested_model = str(model or "").strip()
    if not active_provider or not active_model:
        return False
    if active_provider != tested_provider:
        return False
    return bool(tested_model and model_identity_equal(active_model, tested_model))


async def run_interactive_interview_question(settings, role, provider_id, model, question, **kwargs):
    """兼容旧接口的面试问答函数"""
    workspace = active_workspace_value(settings)

    result = await generate_interview_answer(
        workspace=workspace,
        settings=settings,
        role=role,
        provider_id=provider_id,
        model=model,
        question=question,
        context=kwargs.get("context"),
        criteria=kwargs.get("criteria"),
    )

    if result is None:
        raise StructuredHTTPException(
            status_code=500,
            code="INTERVIEW_GENERATION_FAILED",
            message="Failed to generate interview answer",
        )

    return {
        "ok": True,
        "session_id": kwargs.get("session_id") or str(uuid4()),
        "output": result.get("raw_output", ""),
        "thinking": result.get("thinking", ""),
        "answer": result.get("answer", ""),
        "evaluation": result.get("evaluation", {}),
    }


def save_interactive_interview_report(settings, role, provider_id, model, report, **kwargs):
    """兼容旧接口的保存报告函数"""
    workspace = active_workspace_value(settings)
    update_role_readiness = _can_update_role_readiness(
        settings=settings,
        workspace=workspace,
        role=role,
        provider_id=provider_id,
        model=model,
    )
    return save_interview_report(
        workspace=workspace,
        role=role,
        provider_id=provider_id,
        model=model,
        report=report,
        session_id=kwargs.get("session_id"),
        update_role_readiness=update_role_readiness,
    )


async def run_interactive_interview_streaming(
    settings, role, provider_id, model, question, output_queue, **kwargs
) -> None:
    """兼容旧接口的流式面试函数"""
    workspace = active_workspace_value(settings)
    await generate_interview_answer_streaming(
        workspace=workspace,
        settings=settings,
        role=role,
        provider_id=provider_id,
        model=model,
        question=question,
        output_queue=output_queue,
        context=kwargs.get("context"),
        criteria=kwargs.get("criteria"),
    )


def cancel_interactive_interview_stream(session_id: str) -> dict:
    """兼容旧接口的取消函数（简化实现）"""
    return {"ok": True, "cancelled": True}


router = APIRouter()


def _safe_event_id(raw_value: str | None, prefix: str) -> str:
    raw = str(raw_value or "").strip() or f"{prefix}-{uuid4().hex}"
    safe = _SAFE_EVENT_ID_PATTERN.sub("-", raw).strip(".-_")
    return safe[:96] or f"{prefix}-{uuid4().hex}"


def _track_jetstream_task(task: asyncio.Task[None]) -> None:
    _BACKGROUND_JETSTREAM_TASKS.add(task)

    def _discard(done: asyncio.Task[None]) -> None:
        _BACKGROUND_JETSTREAM_TASKS.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except (RuntimeError, ValueError, OSError, ConnectionError, TimeoutError) as exc:
            logger.warning("interview jetstream task failed: %s", exc)

    task.add_done_callback(_discard)


async def _publish_interview_chunk(*, session_id: str, chunk: dict[str, Any], seq: int) -> bool:
    envelope = create_runtime_event(
        workspace_key="llm",
        run_id=session_id,
        channel=f"llm-interview:{session_id}",
        kind="llm.interview.chunk",
        payload={
            "type": str(chunk.get("type") or "message"),
            "data": dict(chunk.get("data") or {}),
            "seq": int(seq),
        },
        meta={"source": "llm_interview_jetstream"},
    )
    return await publish_to_jetstream(
        subject=f"hp.runtime.llm.interview.{session_id}",
        payload=envelope.to_dict(),
    )


async def _run_interview_jetstream(
    *,
    settings: Any,
    payload: InterviewAskPayload,
    session_id: str,
) -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=50)
    seq = 0
    await _publish_interview_chunk(
        session_id=session_id,
        chunk={
            "type": "start",
            "data": {
                "session_id": session_id,
                "role": payload.role,
                "provider_id": payload.provider_id,
                "model": payload.model,
            },
        },
        seq=seq,
    )
    seq += 1

    terminal_seen = False
    producer = _create_internal_task(
        run_interactive_interview_streaming(
            settings,
            payload.role,
            payload.provider_id,
            payload.model,
            payload.question,
            session_id=session_id,
            context=payload.context,
            expects_thinking=payload.expects_thinking,
            criteria=payload.criteria,
            api_key=payload.api_key,
            extra_headers=payload.headers,
            env_overrides=payload.env_overrides,
            output_queue=queue,
        )
    )
    try:
        while True:
            if producer.done() and queue.empty():
                break
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            await _publish_interview_chunk(session_id=session_id, chunk=chunk, seq=seq)
            seq += 1
            if str(chunk.get("type") or "") in {"complete", "error"}:
                terminal_seen = True
                break
        await producer
    except (RuntimeError, ValueError, OSError, ConnectionError, TimeoutError) as exc:
        logger.warning("interview jetstream execution failed: %s", exc)
        await _publish_interview_chunk(
            session_id=session_id,
            chunk={"type": "error", "data": {"error": str(exc) or type(exc).__name__}},
            seq=seq,
        )
        terminal_seen = True
    finally:
        if not producer.done():
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer
        if not terminal_seen:
            await _publish_interview_chunk(
                session_id=session_id,
                chunk={"type": "error", "data": {"error": "stream completed without a terminal event"}},
                seq=seq,
            )


@router.post(
    "/llm/interview/ask", dependencies=[Depends(require_auth)], response_model=InterviewAskResponse
)  # DEPRECATED
async def llm_interview_ask(request: Request, payload: InterviewAskPayload) -> dict[str, Any]:
    state = get_state(request)
    return await run_interactive_interview_question(
        state.settings,
        payload.role,
        payload.provider_id,
        payload.model,
        payload.question,
        session_id=payload.session_id,
        context=payload.context,
        expects_thinking=payload.expects_thinking,
        criteria=payload.criteria,
        api_key=payload.api_key,
        extra_headers=payload.headers,
        env_overrides=payload.env_overrides,
        debug=payload.debug,
    )


@router.post("/v2/llm/interview/ask", dependencies=[Depends(require_auth)], response_model=InterviewAskResponse)
async def v2_llm_interview_ask(request: Request, payload: InterviewAskPayload) -> dict[str, Any]:
    """Generate an interview answer for a given role and question."""
    state = get_state(request)
    return await run_interactive_interview_question(
        state.settings,
        payload.role,
        payload.provider_id,
        payload.model,
        payload.question,
        session_id=payload.session_id,
        context=payload.context,
        expects_thinking=payload.expects_thinking,
        criteria=payload.criteria,
        api_key=payload.api_key,
        extra_headers=payload.headers,
        env_overrides=payload.env_overrides,
        debug=payload.debug,
    )


@router.post(
    "/llm/interview/save", dependencies=[Depends(require_auth)], response_model=InterviewSaveResponse
)  # DEPRECATED
def llm_interview_save(request: Request, payload: InterviewSavePayload) -> dict[str, Any]:
    state = get_state(request)
    return save_interactive_interview_report(
        state.settings,
        payload.role,
        payload.provider_id,
        payload.model,
        payload.report,
        session_id=payload.session_id,
    )


@router.post("/v2/llm/interview/save", dependencies=[Depends(require_auth)], response_model=InterviewSaveResponse)
def v2_llm_interview_save(request: Request, payload: InterviewSavePayload) -> dict[str, Any]:
    """Save an interview report."""
    state = get_state(request)
    return save_interactive_interview_report(
        state.settings,
        payload.role,
        payload.provider_id,
        payload.model,
        payload.report,
        session_id=payload.session_id,
    )


@router.post(
    "/llm/interview/cancel", dependencies=[Depends(require_auth)], response_model=InterviewCancelResponse
)  # DEPRECATED
def llm_interview_cancel(payload: InterviewCancelPayload) -> dict[str, Any]:
    # Best-effort cancellation (primarily for Codex CLI streaming subprocess).
    return cancel_interactive_interview_stream(payload.session_id)


@router.post("/v2/llm/interview/cancel", dependencies=[Depends(require_auth)], response_model=InterviewCancelResponse)
def v2_llm_interview_cancel(payload: InterviewCancelPayload) -> dict[str, Any]:
    """Best-effort cancellation of an interview stream."""
    # Best-effort cancellation (primarily for Codex CLI streaming subprocess).
    return cancel_interactive_interview_stream(payload.session_id)


@router.post("/v2/llm/interview/jetstream", dependencies=[Depends(require_auth)])
async def v2_llm_interview_jetstream(request: Request, payload: InterviewAskPayload) -> dict[str, Any]:
    """Start an interview run and publish chunks through runtime Nat-JetStream."""
    state = get_state(request)
    session_id = _safe_event_id(payload.session_id, "interactive")
    task = asyncio.create_task(
        _run_interview_jetstream(
            settings=state.settings,
            payload=payload,
            session_id=session_id,
        )
    )
    _track_jetstream_task(task)
    return {
        "ok": True,
        "session_id": session_id,
        "status": "started",
        "channel": f"llm-interview:{session_id}",
        "subject": f"hp.runtime.llm.interview.{session_id}",
        "transport": "nat-jetstream",
    }


@router.post("/llm/interview/stream", dependencies=[Depends(require_auth)])  # DEPRECATED
async def llm_interview_stream(request: Request, payload: InterviewAskPayload):
    """Removed SSE endpoint; use the Nat-JetStream interview endpoint."""
    del request, payload
    legacy_sse_removed("/v2/llm/interview/jetstream")


@router.post("/v2/llm/interview/stream", dependencies=[Depends(require_auth)])
async def v2_llm_interview_stream(request: Request, payload: InterviewAskPayload):
    """Removed SSE endpoint; use the Nat-JetStream interview endpoint."""
    del request, payload
    legacy_sse_removed("/v2/llm/interview/jetstream")
