"""Role-runtime helpers shared by HTTP chat routers."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService

_RUNTIME_ENTRYPOINT_NONSTREAM = "roles.runtime.execute_role_session"
_RUNTIME_ENTRYPOINT_STREAM = "roles.runtime.stream_chat_turn"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _history(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None or isinstance(value, str | bytes):
        return ()
    try:
        iterator = iter(value)
    except TypeError:
        return ()

    normalized: list[tuple[str, str]] = []
    for item in iterator:
        role = ""
        content = ""
        if isinstance(item, Mapping):
            role = _text(item.get("role"))
            content = _text(item.get("content") or item.get("message"))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role = _text(item[0])
            content = _text(item[1])
        if role and content:
            normalized.append((role, content))
    return tuple(normalized)


def _build_session_command(
    *,
    role: str,
    workspace: str,
    message: str,
    payload: Mapping[str, Any] | None,
    default_domain: str,
    host_kind: str,
    stream: bool,
    context: Mapping[str, Any] | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    history: tuple[tuple[str, str], ...] | list[tuple[str, str]] | None = None,
) -> ExecuteRoleSessionCommandV1:
    payload_dict = _mapping(payload)
    context_payload = _mapping(context if context is not None else payload_dict.get("context"))
    domain = _text(payload_dict.get("domain") or context_payload.get("domain") or default_domain)
    resolved_session_id = _text(session_id or payload_dict.get("session_id") or context_payload.get("session_id"))
    resolved_run_id = _text(run_id or payload_dict.get("run_id") or context_payload.get("run_id"))
    resolved_task_id = _text(task_id or payload_dict.get("task_id") or context_payload.get("task_id"))
    if not resolved_session_id:
        resolved_session_id = f"{host_kind}-{role}-{uuid4().hex}"

    entrypoint = _RUNTIME_ENTRYPOINT_STREAM if stream else _RUNTIME_ENTRYPOINT_NONSTREAM
    return ExecuteRoleSessionCommandV1(
        role=role,
        session_id=resolved_session_id,
        workspace=_text(workspace) or ".",
        user_message=message,
        run_id=resolved_run_id or None,
        task_id=resolved_task_id or None,
        domain=domain or default_domain,
        history=_history(history),
        context={
            **context_payload,
            "source": host_kind,
        },
        metadata={
            "role_runtime_required": True,
            "cognitive_runtime_required": True,
            "context_os_expected": True,
            "source": host_kind,
            "role_runtime_entrypoint": entrypoint,
            "runtime_fallback_used": False,
            "fallback_policy": "fail_closed",
        },
        stream=stream,
        host_kind=host_kind,
    )


def _runtime_result_text(result: Any, *names: str) -> str:
    for name in names:
        value = getattr(result, name, None)
        text = str(value or "")
        if text:
            return text
    return ""


def _stream_complete_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    result = event.get("result")
    return {
        "content": _text(event.get("content")) or _runtime_result_text(result, "content", "output"),
        "thinking": _text(event.get("thinking")) or _runtime_result_text(result, "thinking"),
        "profile_version": getattr(result, "profile_version", None),
        "tool_policy_id": getattr(result, "tool_policy_id", None),
        "metadata": {
            "role_runtime_entrypoint": _RUNTIME_ENTRYPOINT_STREAM,
            "context_os_expected": True,
            "runtime_fallback_used": False,
            "fallback_policy": "fail_closed",
        },
    }


def _queue_event_from_runtime_event(event: Mapping[str, Any]) -> dict[str, Any]:
    event_type = _text(event.get("type"))
    if event_type in {"thinking_chunk", "content_chunk"}:
        return {"type": event_type, "data": {"content": str(event.get("content") or "")}}
    if event_type == "tool_call":
        return {
            "type": "tool_call",
            "data": {"tool": event.get("tool"), "args": event.get("args")},
        }
    if event_type == "tool_result":
        result = event.get("result")
        return {"type": "tool_result", "data": result if isinstance(result, dict) else {"result": result}}
    if event_type == "fingerprint":
        return {
            "type": "fingerprint",
            "data": {
                "fingerprint": _text(event.get("profile_hash") or event.get("fingerprint")),
                "profile_id": event.get("profile_id"),
                "profile_hash": event.get("profile_hash"),
                "bundle_id": event.get("bundle_id"),
                "bundle_version": event.get("bundle_version"),
                "run_id": event.get("run_id"),
                "turn_index": event.get("turn_index"),
            },
        }
    if event_type == "complete":
        return {"type": "complete", "data": _stream_complete_payload(event)}
    if event_type == "error":
        return {"type": "error", "data": {"error": str(event.get("error") or "role_runtime_stream_failed")}}
    return {"type": event_type or "message", "data": dict(event)}


async def execute_role_chat_nonstreaming(
    *,
    role: str,
    workspace: str,
    message: str,
    payload: Mapping[str, Any] | None,
    default_domain: str,
    host_kind: str,
) -> dict[str, Any]:
    """Execute a non-streaming role chat turn through roles.runtime."""
    command = _build_session_command(
        role=role,
        workspace=workspace,
        message=message,
        payload=payload,
        default_domain=default_domain,
        host_kind=host_kind,
        stream=False,
    )
    result = await RoleRuntimeService().execute_role_session(command)
    if not bool(getattr(result, "ok", False)):
        message_text = _text(getattr(result, "error_message", "")) or "role_runtime_generation_failed"
        raise RuntimeError(message_text)
    metadata = _mapping(getattr(result, "metadata", {}))
    return {
        "response": str(getattr(result, "output", "") or ""),
        "thinking": str(getattr(result, "thinking", "") or ""),
        "role": role,
        "model": _text(metadata.get("model") or metadata.get("llm_model")) or None,
        "provider": _text(metadata.get("provider") or metadata.get("provider_type")) or None,
        "metadata": {
            **metadata,
            "role_runtime_entrypoint": _RUNTIME_ENTRYPOINT_NONSTREAM,
            "runtime_fallback_used": False,
            "fallback_policy": "fail_closed",
            "context_os_expected": True,
        },
    }
