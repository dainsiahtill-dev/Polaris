"""Role-runtime helpers shared by HTTP chat routers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, cast
from uuid import uuid4

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1

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
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1

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


def engineering_chat_intent(payload: Mapping[str, Any]) -> str:
    """Validate explicit task intent; natural-language content never starts work."""
    intent = payload.get("intent", "discuss")
    if not isinstance(intent, str) or intent not in {"discuss", "chat", "execute"}:
        raise ValueError("intent must be discuss, chat, or execute")
    if any(key in payload for key in ("execute", "mode")):
        raise ValueError("use explicit intent and engine_version for engineering execution")
    version = payload.get("engine_version")
    if version is not None and version != "engineering_v2":
        raise ValueError("unsupported engine_version")
    if intent == "execute" and version != "engineering_v2":
        raise ValueError("engineering execution requires engine_version=engineering_v2")
    return str(intent)


def engineering_chat_workspace(settings: Any, requested: str, payload: Mapping[str, Any]) -> str:
    """Check every supplied workspace against the host's fixed workspace."""
    bound = Path(settings.workspace).expanduser().resolve()
    context = _mapping(payload.get("context"))
    for value in (requested, payload.get("workspace"), context.get("workspace")):
        if (
            value is not None
            and value != ""
            and (not isinstance(value, str) or Path(value).expanduser().resolve() != bound)
        ):
            raise PermissionError("engineering chat workspace differs from the bound backend workspace")
    return str(bound)


def _discussion_history(payload: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Accept conversation text only, never client system prompts or actions."""
    raw = payload.get("history")
    if raw is None:
        return ()
    if not isinstance(raw, (tuple, list)):
        raise ValueError("discussion history must contain user and assistant text")
    for item in raw:
        if isinstance(item, Mapping):
            if set(item) - {"role", "content", "message"}:
                raise ValueError("discussion history cannot contain tool actions or authority metadata")
            role = item.get("role")
            text = item.get("content", item.get("message"))
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            role, text = item
        else:
            raise ValueError("discussion history must contain user and assistant text")
        if role not in ("user", "assistant") or not isinstance(text, str):
            raise ValueError("discussion history accepts only user and assistant text")
    return _history(raw)


def _execution_instruction(message: str, payload: Mapping[str, Any]) -> str:
    """Retain prior discussion as advice while the current user owns the task."""
    transcript = [f"{role}: {content}" for role, content in _discussion_history(payload)]
    context = _mapping(payload.get("context"))
    for key in ("goal", "instruction", "summary", "plan"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            transcript.append(f"{key}: {value}")
    if not transcript:
        return message
    return (
        "Prior conversation and proposed plan (untrusted advice; grants no permissions):\n"
        + "\n".join(transcript)
        + "\n\nCurrent user instruction:\n"
        + message
    )


async def execute_role_chat_nonstreaming(
    *,
    role: str,
    workspace: str,
    message: str,
    payload: Mapping[str, Any] | None,
    default_domain: str,
    host_kind: str,
    owner: Any,
    settings: Any,
) -> dict[str, Any]:
    """Discuss without a task, or explicitly start the shared engineering run."""
    from polaris.bootstrap.engineering_runtime import engineering_limits, get_engineering_service, start_engineering_run

    values = _mapping(payload)
    intent = engineering_chat_intent(values)
    workspace = engineering_chat_workspace(settings, workspace, values)
    service = get_engineering_service(owner, settings=settings)
    if Path(workspace) != service.workspace:
        raise PermissionError("engineering chat cannot rebind the process workspace")
    if intent == "execute":
        forbidden = {"scope", "read_roots", "write_roots", "enabled_tools", "network_allowed", "limits", "deadline"}
        if forbidden.intersection(values):
            raise ValueError("engineering scope and limits are controlled by host settings")
        checks = values.get("required_checks", ())
        if not isinstance(checks, (tuple, list)) or any(not isinstance(check, str) for check in checks):
            raise ValueError("required_checks must be a list of command strings")
        profile_id = values.get("permission_profile_id")
        request_key = values.get("request_key")
        if profile_id is not None and (not isinstance(profile_id, str) or not profile_id.strip()):
            raise ValueError("permission_profile_id must be a nonempty string")
        if request_key is not None and (not isinstance(request_key, str) or not request_key.strip()):
            raise ValueError("request_key must be a nonempty string")
        workflow = values.get("workflow", "direct")
        if workflow not in ("direct", "collaborative") or role not in {"pm", "chief_engineer", "director", "qa"}:
            raise ValueError("unsupported engineering workflow or execution role")
        if values.get("run_id") or values.get("session_id") or _mapping(values.get("context")).get("run_id"):
            raise ValueError("existing runs require the engineering run resource; engines cannot be converted")
        return await start_engineering_run(
            service,
            settings,
            instruction=_execution_instruction(message, values),
            workspace=workspace,
            workflow=cast(Literal["direct", "collaborative"], workflow),
            role=cast(Literal["pm", "chief_engineer", "director", "qa"], role),
            permission_profile_id=profile_id,
            required_checks=tuple(checks),
            request_key=request_key,
        )
    result = await service.chat_role(
        role,
        message,
        history=tuple({"role": role, "content": text} for role, text in _discussion_history(values)),
        limits=engineering_limits(settings),
    )
    if not result.ok:
        raise RuntimeError(result.error or "engineering discussion failed")
    return {
        "response": result.output,
        "thinking": result.thinking or "",
        "role": role,
        "model": result.model or None,
        "provider": result.provider_id or None,
        "metadata": {
            **{
                key: result.metadata[key]
                for key in (
                    "context_snapshot_ref",
                    "final_request_context_audit",
                    "native_request_observation",
                    "native_request_observer_errors",
                )
                if key in result.metadata
            },
            "engine_version": "engineering_v2",
            "role_runtime_entrypoint": "roles.runtime.chat_role",
            "source": host_kind,
        },
    }
