"""Runtime-first role dialogue helper for workflow adapters.

Adapters are production orchestration entrypoints. They must enter the role
runtime so Context OS, strategy receipts, and cognitive runtime shadow artifacts
are exercised on the same path used by interactive role sessions.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

_RUNTIME_ENTRYPOINT = "roles.runtime.execute_role_session"


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _stable_token(*parts: Any, length: int = 16) -> str:
    material = "\n".join(str(part or "") for part in parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[: max(8, int(length))]


def _context_metadata(context: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _to_dict(context.get("metadata"))
    for key in ("run_id", "factory_run_id", "task_id", "session_id"):
        value = _string(context.get(key))
        if value and key not in metadata:
            metadata[key] = value
    return metadata


def _resolve_context_id(
    context: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *keys: str,
) -> str:
    for key in keys:
        value = _string(context.get(key))
        if value:
            return value
        value = _string(metadata.get(key))
        if value:
            return value
    return ""


def _coerce_history(value: Any) -> tuple[tuple[str, str], ...]:
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
            role = _string(item.get("role"))
            content = _string(item.get("content") or item.get("message"))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role = _string(item[0])
            content = _string(item[1])
        if role and content:
            normalized.append((role, content))
    return tuple(normalized)


def _create_role_runtime_service() -> Any:
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    return RoleRuntimeService()


def _create_role_session_command(**kwargs: Any) -> Any:
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1

    return ExecuteRoleSessionCommandV1(**kwargs)


def _runtime_result_to_dialogue_payload(result: Any) -> dict[str, Any]:
    output = str(getattr(result, "output", "") or "")
    metadata = _to_dict(getattr(result, "metadata", {}))
    usage = _to_dict(getattr(result, "usage", {}))
    error_message = _string(getattr(result, "error_message", ""))
    error_code = _string(getattr(result, "error_code", ""))
    ok = bool(getattr(result, "ok", False))

    metadata.update(
        {
            "role_runtime_entrypoint": _RUNTIME_ENTRYPOINT,
            "context_os_expected": True,
            "runtime_fallback_used": False,
            "fallback_policy": "fail_closed",
        }
    )
    return {
        "success": ok,
        "response": output,
        "content": output,
        "thinking": getattr(result, "thinking", None),
        "role": _string(getattr(result, "role", "")),
        "metadata": metadata,
        "execution_stats": usage,
        "tool_calls": list(getattr(result, "tool_calls", ()) or ()),
        "artifacts": list(getattr(result, "artifacts", ()) or ()),
        "error": error_message or error_code,
        "raw_response": result,
    }


async def invoke_role_runtime_first(
    *,
    workspace: str,
    role: str,
    message: str,
    context: dict[str, Any] | None = None,
    domain: str | None = None,
    validate_output: bool = False,
    max_retries: int = 1,
    prompt_appendix: str | None = None,
) -> dict[str, Any]:
    """Invoke a role through `roles.runtime` and fail closed on boundary errors.

    Workflow adapters are production orchestration entrypoints. If the runtime
    public boundary cannot be constructed, the caller must see that failure so
    Context OS and Cognitive Runtime cannot be silently bypassed.
    """

    role_token = _string(role)
    workspace_token = _string(workspace)
    message_text = str(message or "")
    if not role_token:
        raise ValueError("role must be a non-empty string")
    if not workspace_token:
        raise ValueError("workspace must be a non-empty string")
    if not message_text.strip():
        raise ValueError("message must be a non-empty string")

    context_payload = _to_dict(context)
    metadata = _context_metadata(context_payload)
    metadata.update(
        {
            "source": "roles.adapters.runtime_dialogue",
            "role_runtime_required": True,
            "cognitive_runtime_required": True,
            "context_os_expected": True,
            "validate_output": bool(validate_output),
            "max_retries": max(0, int(max_retries)),
        }
    )
    if prompt_appendix:
        metadata["prompt_appendix"] = str(prompt_appendix)

    run_id = _resolve_context_id(context_payload, metadata, "run_id", "factory_run_id")
    task_id = _resolve_context_id(context_payload, metadata, "task_id")
    session_id = _resolve_context_id(context_payload, metadata, "session_id", "runtime_session_id")
    if not session_id:
        session_basis = run_id or task_id or _stable_token(workspace_token, role_token, message_text)
        session_id = f"{role_token}-adapter-{session_basis}"

    history = _coerce_history(context_payload.get("history"))
    runtime = _create_role_runtime_service()
    command = _create_role_session_command(
        role=role_token,
        session_id=session_id,
        workspace=workspace_token,
        user_message=message_text,
        run_id=run_id or None,
        task_id=task_id or None,
        domain=domain,
        history=history,
        context=context_payload,
        metadata=metadata,
        stream=False,
        host_kind=f"{role_token}_adapter",
    )

    result = await runtime.execute_role_session(command)
    return _runtime_result_to_dialogue_payload(result)
