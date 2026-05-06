"""Interview routes for the LLM router."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from polaris.cells.llm.evaluation.public.service import (
    generate_interview_answer,
    generate_interview_answer_streaming,
)
from polaris.delivery.http.routers._shared import StructuredHTTPException, get_state, require_auth
from polaris.delivery.http.schemas import (
    InterviewAskResponse,
    InterviewCancelResponse,
    InterviewSaveResponse,
)

from .llm_models import InterviewAskPayload, InterviewCancelPayload, InterviewSavePayload
from .sse_utils import create_sse_response, sse_event_generator


# 适配器函数，保持与旧接口的兼容性
async def run_interactive_interview_question(settings, role, provider_id, model, question, **kwargs):
    """兼容旧接口的面试问答函数"""
    workspace = settings.workspace

    result = await generate_interview_answer(
        workspace=workspace,
        settings=settings,
        role=role,
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
    """兼容旧接口的保存报告函数（简化实现）"""
    return {"ok": True, "saved": True}


async def run_interactive_interview_streaming(
    settings, role, provider_id, model, question, output_queue, **kwargs
) -> None:
    """兼容旧接口的流式面试函数"""
    workspace = settings.workspace
    await generate_interview_answer_streaming(
        workspace=workspace,
        settings=settings,
        role=role,
        question=question,
        output_queue=output_queue,
        context=kwargs.get("context"),
        criteria=kwargs.get("criteria"),
    )


def cancel_interactive_interview_stream(session_id: str) -> dict:
    """兼容旧接口的取消函数（简化实现）"""
    return {"ok": True, "cancelled": True}


router = APIRouter()
logger = logging.getLogger(__name__)


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
    # Best-effort cancellation (primarily for Codex CLI streaming subprocess).
    return cancel_interactive_interview_stream(payload.session_id)


@router.post("/llm/interview/stream", dependencies=[Depends(require_auth)])  # DEPRECATED
async def llm_interview_stream(request: Request, payload: InterviewAskPayload):
    """Stream interview responses using Server-Sent Events (SSE)

    This endpoint provides real-time output from the LLM as it executes,
    allowing the client to see progress before the final result is ready.
    """
    state = get_state(request)
    run_id = payload.session_id or f"interactive-{uuid4().hex}"

    async def _run_interview(queue: asyncio.Queue) -> None:
        await run_interactive_interview_streaming(
            state.settings,
            payload.role,
            payload.provider_id,
            payload.model,
            payload.question,
            session_id=run_id,
            context=payload.context,
            expects_thinking=payload.expects_thinking,
            criteria=payload.criteria,
            api_key=payload.api_key,
            extra_headers=payload.headers,
            env_overrides=payload.env_overrides,
            output_queue=queue,
        )

    async def _cleanup() -> None:
        await asyncio.to_thread(cancel_interactive_interview_stream, run_id)

    return create_sse_response(sse_event_generator(_run_interview, cleanup_fn=_cleanup))


@router.post("/v2/llm/interview/stream", dependencies=[Depends(require_auth)])
async def v2_llm_interview_stream(request: Request, payload: InterviewAskPayload):
    """Stream interview responses using Server-Sent Events (SSE)

    This endpoint provides real-time output from the LLM as it executes,
    allowing the client to see progress before the final result is ready.
    """
    state = get_state(request)
    run_id = payload.session_id or f"interactive-{uuid4().hex}"

    async def _run_interview(queue: asyncio.Queue) -> None:
        await run_interactive_interview_streaming(
            state.settings,
            payload.role,
            payload.provider_id,
            payload.model,
            payload.question,
            session_id=run_id,
            context=payload.context,
            expects_thinking=payload.expects_thinking,
            criteria=payload.criteria,
            api_key=payload.api_key,
            extra_headers=payload.headers,
            env_overrides=payload.env_overrides,
            output_queue=queue,
        )

    async def _cleanup() -> None:
        await asyncio.to_thread(cancel_interactive_interview_stream, run_id)

    return create_sse_response(sse_event_generator(_run_interview, cleanup_fn=_cleanup))
