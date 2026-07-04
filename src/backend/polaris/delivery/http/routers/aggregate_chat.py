"""Aggregate model-compatible chat completion router."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.control_plane.run_ledger.public import merge_failure_evidence_payload
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatCompletionsCommandV1,
    AggregateChatMessageV1,
)
from polaris.cells.roles.runtime.public.service import aggregate_chat_completions
from polaris.delivery.http.routers._shared import StructuredHTTPException, get_state, require_auth
from polaris.delivery.http.workspace import active_workspace_value

router = APIRouter()


def _normalize_messages(raw_messages: Any) -> tuple[AggregateChatMessageV1, ...]:
    if not isinstance(raw_messages, list) or not raw_messages:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_REQUEST",
            message="messages must be a non-empty list",
        )
    messages: list[AggregateChatMessageV1] = []
    for index, item in enumerate(raw_messages):
        if not isinstance(item, Mapping):
            raise StructuredHTTPException(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"messages[{index}] must be an object",
            )
        try:
            messages.append(
                AggregateChatMessageV1(
                    role=str(item.get("role") or "").strip(),
                    content=str(item.get("content") or "").strip(),
                    name=str(item.get("name")).strip() if item.get("name") is not None else None,
                )
            )
        except ValueError as exc:
            raise StructuredHTTPException(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"invalid messages[{index}]: {exc}",
            ) from exc
    return tuple(messages)


def _normalize_role_ids(raw_role_ids: Any) -> tuple[str, ...]:
    if raw_role_ids is None:
        return ()
    if isinstance(raw_role_ids, str):
        token = raw_role_ids.strip()
        return (token,) if token else ()
    if isinstance(raw_role_ids, list | tuple):
        return tuple(str(item or "").strip() for item in raw_role_ids if str(item or "").strip())
    raise StructuredHTTPException(
        status_code=400,
        code="INVALID_REQUEST",
        message="role_ids must be a string or list of strings",
    )


def _normalize_failure_signals(payload: Mapping[str, Any]) -> tuple[str, ...]:
    signals: list[str] = []
    raw_signal = payload.get("failure_signal")
    if isinstance(raw_signal, str) and raw_signal.strip():
        signals.append(raw_signal)
    raw_signals = payload.get("failure_signals")
    if isinstance(raw_signals, str) and raw_signals.strip():
        signals.append(raw_signals)
    elif isinstance(raw_signals, list | tuple):
        signals.extend(str(item or "") for item in raw_signals)
    return tuple(signal.strip() for signal in signals if signal.strip())


def _normalize_failure_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_evidence = payload.get("failure_evidence")
    return merge_failure_evidence_payload({}, raw_evidence)


@router.post("/v1/chat/completions", dependencies=[Depends(require_auth)])
async def aggregate_chat_completions_route(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Return an aggregate model chat completion."""
    state = get_state(request)
    workspace = str(payload.get("workspace") or active_workspace_value(state.settings)).strip()
    if not workspace:
        raise StructuredHTTPException(status_code=400, code="INVALID_REQUEST", message="workspace is required")

    try:
        command = AggregateChatCompletionsCommandV1(
            workspace=workspace,
            messages=_normalize_messages(payload.get("messages")),
            model=str(payload.get("model") or "polaris.aggregate_llm.v1").strip(),
            domain=str(payload.get("domain")).strip() if payload.get("domain") is not None else None,
            role_ids=_normalize_role_ids(payload.get("role_ids") or payload.get("roles")),
            failure_signals=_normalize_failure_signals(payload),
            failure_evidence=_normalize_failure_evidence(payload),
            execution_mode=str(payload.get("execution_mode") or "plan_only").strip(),
            session_id=str(payload.get("session_id")).strip() if payload.get("session_id") is not None else None,
            run_id=str(payload.get("run_id")).strip() if payload.get("run_id") is not None else None,
            include_virtual_lobes=bool(payload.get("include_virtual_lobes", True)),
            context=payload.get("context") if isinstance(payload.get("context"), Mapping) else {},
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {},
        )
    except ValueError as exc:
        raise StructuredHTTPException(status_code=400, code="INVALID_REQUEST", message=str(exc)) from exc

    try:
        result = await aggregate_chat_completions(command)
    except ValueError as exc:
        raise StructuredHTTPException(status_code=400, code="INVALID_REQUEST", message=str(exc)) from exc
    except RuntimeError as exc:
        raise StructuredHTTPException(status_code=500, code="AGGREGATE_RUNTIME_ERROR", message=str(exc)) from exc
    return asdict(result)
