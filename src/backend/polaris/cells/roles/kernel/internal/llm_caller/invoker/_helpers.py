"""Module-level helpers for the LLM invoker package.

Private free functions, small dataclasses, constants, and the instructor
availability probe that previously lived at the top of the monolithic
``invoker`` module.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleSemanticRequestIdentityV1,
    get_factory_role_evidence_authority_binding,
)
from polaris.kernelone.llm.engine import AIExecutor
from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name

from ..context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit_for_request,
)
from ..factory_dispatch_propagation import (
    FactorySemanticDispatchPropagationPort,
    enforce_factory_aware_final_request_evidence_coverage,
)
from ..final_provider_attempt_qualification import (
    context_snapshot_matches_frozen_attempt,
    final_request_snapshot_evidence,
)
from ..final_request_metrics import validated_final_context_evidence
from ..helpers import (
    extract_native_tool_calls,
    native_tool_call_name,
)
from ..request_preparer import LLMRequestPreparer
from ..response_types import PreparedLLMRequest
from ..stream_handler import (
    normalize_stream_chunk,  # noqa: F401
)

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

logger = logging.getLogger(__name__)

_FACTORY_SEMANTIC_DISPATCH_NOT_ENABLED = "factory_role_semantic_request_frozen_physical_dispatch_not_enabled"


class _FactorySemanticDispatchNotEnabledError(RuntimeError):
    """B3.2 hard stop: semantic freeze is not physical-call authority."""


def _enforce_factory_semantic_zero_transport(prepared: PreparedLLMRequest) -> None:
    # B3.5 production requests are exact PreparedLLMRequest instances whose
    # live sidecar performs qualification.  Legacy/malformed test doubles do
    # not carry that authority and remain fail-closed.
    if type(prepared) is PreparedLLMRequest:
        return
    prepared.__post_init__()
    if prepared.factory_semantic_request is not None:
        raise _FactorySemanticDispatchNotEnabledError(_FACTORY_SEMANTIC_DISPATCH_NOT_ENABLED)


def _physical_dispatch_port_for_request(
    prepared: PreparedLLMRequest,
    request: Any,
) -> Any | None:
    """Return exact sidecar only for unchanged frozen semantics.

    Semantic-changing fallback requests need a fresh cutoff/freeze/sidecar.
    B3.3 has no lawful shortcut, so these paths remain fail-closed.
    """

    prepared.__post_init__()
    port = prepared.factory_dispatch_port
    if port is None:
        return None
    if request is not prepared.ai_request:
        raise RuntimeError("factory_role_semantic_retry_refreeze_required")
    frozen = prepared.factory_semantic_request
    if frozen is None:
        raise RuntimeError("factory_role_semantic_request_required_for_dispatch_port")
    port.validate_frozen_identity(frozen)
    return port


async def _invoke_executor_with_factory_dispatch(
    *,
    executor: Any,
    prepared: PreparedLLMRequest,
    request: Any,
    profile: Any | None = None,
) -> Any:
    """Preserve the legacy executor call shape for ordinary requests."""

    port = _physical_dispatch_port_for_request(prepared, request)
    if port is None:
        return await executor.invoke(request)
    if type(port) is not FactorySemanticDispatchPropagationPort:
        return await executor.invoke(request, physical_dispatch_port=port)
    frozen = prepared.factory_semantic_request
    if frozen is None:
        raise RuntimeError("factory_role_semantic_request_required_for_dispatch_port")
    request_context = getattr(request, "context", None)
    existing_context_ref = (
        str(request_context.get("context_snapshot_ref") or "") if isinstance(request_context, dict) else ""
    )
    if not context_snapshot_matches_frozen_attempt(
        workspace=port.workspace,
        context_snapshot_ref=existing_context_ref,
        frozen=frozen,
    ):
        await _store_active_request_context_snapshot(
            workspace=port.workspace,
            active_request=request,
            prepared=prepared,
            profile=profile,
            run_id=frozen.identity.run_id,
            call_id=frozen.identity.call_id,
        )
    audit = build_final_request_context_audit_for_request(
        ai_request=request,
        prepared=prepared,
        profile=profile,
    )
    audit = port.bind_final_request_context_audit(audit)
    enforce_factory_aware_final_request_evidence_coverage(
        port=prepared.factory_dispatch_port,
        ai_request=request,
        audit=audit,
    )
    request_context = getattr(request, "context", None)
    context_snapshot_ref = (
        str(request_context.get("context_snapshot_ref") or "") if isinstance(request_context, dict) else ""
    )
    port.qualify(
        final_request_context_audit=audit,
        context_snapshot_ref=context_snapshot_ref,
    )
    return await executor.invoke(request, physical_dispatch_port=port)


async def _reprepare_semantic_retry(
    *,
    request_preparer: LLMRequestPreparer,
    prepared: PreparedLLMRequest,
    request: Any,
    profile: Any,
) -> tuple[PreparedLLMRequest, Any]:
    """Mint a new Factory freeze/port before snapshot/audit of changed semantics."""

    if prepared.factory_dispatch_port is not None and request is not prepared.ai_request:
        prepared = await request_preparer._reprepare_factory_semantic_retry_request(
            prepared=prepared,
            request=request,
            profile=profile,
        )
        request = prepared.ai_request
    return prepared, request


def _invoker_owned_factory_semantic_identity(
    *,
    run_id: str | None,
    turn_round: int,
    call_id: str,
) -> FactoryRoleSemanticRequestIdentityV1 | None:
    """Mint identity only for an explicitly controlled Factory child run."""

    if get_factory_role_evidence_authority_binding() is None:
        return None
    if type(run_id) is not str or not run_id.strip():
        raise RuntimeError("factory_role_controlled_run_id_required")
    if type(turn_round) is not int or turn_round < 0:
        raise RuntimeError("factory_role_turn_round_invalid")
    controlled_run_id = run_id.strip()
    return FactoryRoleSemanticRequestIdentityV1(
        run_id=controlled_run_id,
        turn_id=f"{controlled_run_id}:turn:{turn_round}",
        call_id=call_id,
        request_freeze_id=uuid.uuid4().hex,
    )


def _refreeze_factory_semantic_identity(
    identity: FactoryRoleSemanticRequestIdentityV1 | None,
) -> FactoryRoleSemanticRequestIdentityV1 | None:
    if identity is None:
        return None
    if type(identity) is not FactoryRoleSemanticRequestIdentityV1:
        raise TypeError("factory_role_semantic_identity_exact_type_required")
    FactoryRoleSemanticRequestIdentityV1.__post_init__(identity)
    return FactoryRoleSemanticRequestIdentityV1(
        run_id=identity.run_id,
        turn_id=identity.turn_id,
        call_id=identity.call_id,
        request_freeze_id=uuid.uuid4().hex,
    )


@dataclass(frozen=True)
class _RoleBindingFallbackFailure:
    profile: RoleProfile
    prepared: PreparedLLMRequest
    active_request: Any
    error: str
    model: str


def _with_context_os_audit(metadata: dict[str, Any], prepared: PreparedLLMRequest | None) -> dict[str, Any]:
    payload = dict(metadata)
    audit = getattr(prepared, "context_os_audit", None) if prepared is not None else None
    if isinstance(audit, dict):
        payload["context_os_audit"] = dict(audit)
    return payload


def _with_final_request_context_audit(
    metadata: dict[str, Any],
    *,
    prepared: PreparedLLMRequest,
    active_request: Any,
    profile: Any,
) -> dict[str, Any]:
    payload = dict(metadata)
    final_evidence = validated_final_context_evidence(
        prepared.factory_dispatch_port,
        expected_port_type=FactorySemanticDispatchPropagationPort,
    )
    audit = (
        final_evidence[1]
        if final_evidence is not None
        else build_final_request_context_audit_for_request(
            ai_request=active_request,
            prepared=prepared,
            profile=profile,
        )
    )
    raw_final_tokens = audit.get("final_request_token_estimate")
    final_tokens = int(
        raw_final_tokens
        if raw_final_tokens is not None
        else (prepared.context_result.token_estimate if prepared.context_result else 0)
    )
    payload["final_request_context_audit"] = audit
    payload["context_tokens_after"] = final_tokens
    payload["contextTokens"] = final_tokens
    return payload


def _final_request_context_tokens(metadata: dict[str, Any], fallback: int | None = None) -> int | None:
    raw = metadata.get("contextTokens")
    if raw is None:
        raw = metadata.get("context_tokens_after")
    if raw is None:
        raw = fallback
    if isinstance(raw, bool) or raw is None:
        return fallback
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return fallback


def _prepared_request_temperature(prepared: PreparedLLMRequest, fallback: float) -> float:
    """Read the effective temperature emitted by the request preparer.

    Minimal test/provider adapters may omit request options. They retain the
    caller argument instead of failing before invocation; production requests
    always use the prepared option when present.
    """

    option_payloads = (
        prepared.request_options,
        getattr(prepared.ai_request, "options", None),
    )
    for payload in option_payloads:
        if not isinstance(payload, dict):
            continue
        raw_temperature = payload.get("temperature")
        if raw_temperature is None or isinstance(raw_temperature, bool):
            continue
        try:
            return float(raw_temperature)
        except (TypeError, ValueError):
            continue
    return float(fallback)


def _required_tools_from_final_request_audit(audit: dict[str, Any]) -> list[str]:
    coverage = audit.get("final_request_evidence_coverage")
    if not isinstance(coverage, dict):
        return []
    rows: list[str] = []
    for item in coverage.get("required_tools") or []:
        token = str(item or "").strip()
        if token and token not in rows:
            rows.append(token)
    return rows


def _called_required_native_tool(native_tool_calls: list[dict[str, Any]], required_tools: list[str]) -> bool:
    required = {canonicalize_tool_name(name) for name in required_tools if str(name or "").strip()}
    if not required:
        return True
    called = {canonicalize_tool_name(name) for call in native_tool_calls if (name := native_tool_call_name(call))}
    return bool(required & called)


def _request_tool_surface_disabled(active_request: Any, prepared: PreparedLLMRequest) -> bool:
    """True when the request physically cannot call tools (zero-tool surface).

    A request without native tool schemas — or with tool_choice ``none`` /
    ``disabled`` — cannot satisfy required-tool semantics no matter how many
    retries fire. Stale ``required_tools`` inherited from the shared turn
    context (e.g. a forced-write first call) must never turn such a call into
    a required_tool_not_called retry storm.
    """

    options = getattr(active_request, "options", None)
    if not isinstance(options, dict):
        raw_prepared_options = getattr(prepared, "request_options", None)
        options = raw_prepared_options if isinstance(raw_prepared_options, dict) else {}
    tool_choice = str(options.get("tool_choice") or "").strip().lower()
    if tool_choice in {"none", "disabled"}:
        return True
    tools = options.get("tools")
    return not (isinstance(tools, list) and tools)


def _profile_lacks_forced_tool_choice(profile: Any) -> bool:
    token = " ".join(
        [
            str(getattr(profile, "provider_id", "") or ""),
            str(getattr(profile, "model", "") or ""),
            str(getattr(profile, "provider_type", "") or ""),
            str(getattr(profile, "name", "") or ""),
        ]
    ).lower()
    return "kimi" in token or "deepseek" in token or "minimax" in token


def _allowed_tool_names_from_prepared(prepared: PreparedLLMRequest) -> list[str]:
    names: list[str] = []
    for tool in cast(list[Any], prepared.native_tool_schemas or []):
        if not isinstance(tool, dict):
            continue
        function_block = tool.get("function")
        name = str(function_block.get("name") or "").strip() if isinstance(function_block, dict) else ""
        if not name:
            name = str(tool.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    if names:
        return names

    options = prepared.request_options if isinstance(prepared.request_options, dict) else {}
    raw_tools = options.get("tools")
    if not isinstance(raw_tools, list):
        return names
    for tool in raw_tools:
        if not isinstance(tool, dict):
            continue
        function_block = tool.get("function")
        name = str(function_block.get("name") or "").strip() if isinstance(function_block, dict) else ""
        if not name:
            name = str(tool.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


@dataclass(frozen=True)
class _TextToolRecovery:
    calls: tuple[dict[str, Any], ...] = ()
    parser_attempted: bool = False
    parser_available: bool = True
    error: str = ""


def _recover_text_tool_calls_from_response_text(
    *,
    response_text: str,
    raw_payload: dict[str, Any],
    prepared: PreparedLLMRequest,
    provider_hint: str,
) -> _TextToolRecovery:
    if not str(response_text or "").strip():
        return _TextToolRecovery(error="empty_response_text")
    allowed_tool_names = _allowed_tool_names_from_prepared(prepared)
    if not allowed_tool_names:
        return _TextToolRecovery(error="no_allowed_tool_names")
    try:
        from polaris.infrastructure.llm.tools.parser_adapter import LLMToolkitParserAdapter
    except (ImportError, RuntimeError, ValueError) as exc:
        return _TextToolRecovery(parser_available=False, error=f"parser_unavailable:{exc}")

    try:
        parsed = LLMToolkitParserAdapter().parse_calls(
            text=response_text,
            response_payload=raw_payload,
            provider_hint=provider_hint,
            allowed_tool_names=allowed_tool_names,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        return _TextToolRecovery(parser_attempted=True, error=f"parser_failed:{exc}")
    calls: list[dict[str, Any]] = []
    for item in parsed:
        to_openai_format = getattr(item, "to_openai_format", None)
        if not callable(to_openai_format):
            continue
        call = to_openai_format()
        if isinstance(call, dict):
            calls.append(call)
    return _TextToolRecovery(
        calls=tuple(calls),
        parser_attempted=True,
        error="" if calls else "parser_produced_no_tool_calls",
    )


def _required_tool_not_called_error(
    *,
    prepared: PreparedLLMRequest,
    active_request: Any,
    response: Any,
    profile: Any,
) -> str:
    if _request_tool_surface_disabled(active_request, prepared):
        return ""
    audit = build_final_request_context_audit_for_request(
        ai_request=active_request,
        prepared=prepared,
        profile=profile,
    )
    required_tools = _required_tools_from_final_request_audit(audit)
    if not required_tools:
        return ""
    raw_payload = response.raw if isinstance(getattr(response, "raw", None), dict) else {}
    response_text = str(getattr(response, "output", "") or "")
    response_model_name = str((getattr(response, "model", None) or raw_payload.get("model") or "") or "")
    response_provider = str(
        (getattr(response, "provider_id", None) or raw_payload.get("provider_id") or raw_payload.get("provider") or "")
        or ""
    )
    native_tool_calls, _provider = extract_native_tool_calls(
        raw_payload,
        provider_id=response_provider,
        model=response_model_name,
        response_text=response_text,
    )
    if _called_required_native_tool(native_tool_calls, required_tools):
        return ""
    recovery = _recover_text_tool_calls_from_response_text(
        response_text=response_text,
        raw_payload=raw_payload,
        prepared=prepared,
        provider_hint=_provider,
    )
    if _called_required_native_tool(list(recovery.calls), required_tools):
        return ""
    return "required_tool_not_called: required_tools=" + ",".join(required_tools)


def _with_optional_final_request_context_audit(
    metadata: dict[str, Any],
    *,
    prepared: PreparedLLMRequest | None,
    active_request: Any,
    profile: Any,
) -> dict[str, Any]:
    if prepared is None or active_request is None:
        return dict(metadata)
    return _with_final_request_context_audit(
        metadata,
        prepared=prepared,
        active_request=active_request,
        profile=profile,
    )


def _with_context_snapshot_diagnostics(metadata: dict[str, Any], request: Any) -> dict[str, Any]:
    """Attach context snapshot degradation evidence from AIRequest.context, when present."""
    payload = dict(metadata)
    ctx = getattr(request, "context", None)
    if isinstance(ctx, dict):
        degraded = ctx.get("context_snapshot_degraded")
        if isinstance(degraded, dict):
            payload["context_snapshot_degraded"] = dict(degraded)
            payload["context_snapshot_degraded_reason"] = degraded.get("reason") or degraded.get("code")
    return payload


_CONTEXT_SNAPSHOT_CONTEXT_KEYS = (
    "context_snapshot_ref",
    "contextSnapshotRef",
    "context_snapshot_degraded",
    "contextSnapshotDegraded",
    "context_snapshot_degraded_reason",
    "contextSnapshotDegradedReason",
)


def _clear_context_snapshot_context(request: Any) -> dict[str, Any] | None:
    ctx = getattr(request, "context", None)
    if not isinstance(ctx, dict):
        try:
            request.context = {}
        except (AttributeError, TypeError):
            return None
        ctx = getattr(request, "context", None)
    if not isinstance(ctx, dict):
        return None
    for key in _CONTEXT_SNAPSHOT_CONTEXT_KEYS:
        ctx.pop(key, None)
    return ctx


def _context_snapshot_degraded_payload(exc: BaseException) -> dict[str, str]:
    return {
        "code": "CONTEXT_STORE_WRITE_FAILED",
        "reason": "context_snapshot_store_failure",
        "message": str(exc)[:200],
        "exception_type": type(exc).__name__,
    }


def _snapshot_messages_for_request(*, request: Any, prepared: PreparedLLMRequest) -> list[Any]:
    ctx = getattr(request, "context", None)
    raw_messages = ctx.get("chat_messages") if isinstance(ctx, dict) else None
    if isinstance(raw_messages, list) and raw_messages:
        messages = [dict(item) for item in raw_messages if isinstance(item, dict)]
        if messages:
            return messages
    messages = [dict(item) for item in getattr(prepared, "messages", []) or [] if isinstance(item, dict)]
    if messages:
        return messages
    request_input = str(getattr(request, "input", "") or "")
    if request_input.strip():
        return [{"role": "user", "content": request_input}]
    return []


async def _store_active_request_context_snapshot(
    *,
    workspace: str | None,
    active_request: Any,
    prepared: PreparedLLMRequest,
    profile: Any,
    run_id: str,
    call_id: str,
) -> str | None:
    """Persist final provider request evidence for the concrete active request."""

    request = active_request
    ctx = _clear_context_snapshot_context(request)
    messages = _snapshot_messages_for_request(request=request, prepared=prepared)
    if not messages:
        return None
    try:
        provider_request_snapshot = build_final_provider_request_snapshot(
            ai_request=request,
            prepared=prepared,
            profile=profile,
        )
        frozen = prepared.factory_semantic_request
        if frozen is not None:
            provider_request_snapshot["factory_final_request"] = final_request_snapshot_evidence(frozen)
            snapshot_audit = provider_request_snapshot.get("final_request_context_audit")
            if not isinstance(snapshot_audit, dict):
                raise RuntimeError("factory_final_request_context_audit_missing")
            dispatch_port = prepared.factory_dispatch_port
            if type(dispatch_port) is not FactorySemanticDispatchPropagationPort:
                raise RuntimeError("factory_dispatch_port_required_for_context_audit_binding")
            bound_snapshot_audit = dispatch_port.bind_final_request_context_audit(snapshot_audit)
            provider_request_snapshot["final_request_context_audit"] = bound_snapshot_audit
            provider_request_snapshot["final_request_evidence_coverage"] = bound_snapshot_audit.get(
                "final_request_evidence_coverage",
                {},
            )
        context_store_hash = await AIExecutor._store_context_messages(
            workspace,
            messages,
            run_id,
            call_id,
            provider_request_snapshot,
        )
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        logger.warning(
            "Failed to store LLM context snapshot before call_start: workspace=%s run_id=%s call_id=%s",
            workspace,
            run_id,
            call_id,
            exc_info=True,
        )
        if isinstance(ctx, dict):
            ctx["context_snapshot_degraded"] = _context_snapshot_degraded_payload(exc)
        return None
    if context_store_hash and isinstance(ctx, dict):
        ctx["context_snapshot_ref"] = str(context_store_hash)
    return str(context_store_hash) if context_store_hash else None


async def _store_call_start_context_snapshot(
    *,
    workspace: str | None,
    prepared: PreparedLLMRequest,
    profile: Any,
    run_id: str,
    call_id: str,
) -> None:
    """Persist the final provider messages before call_start emits its ref."""

    await _store_active_request_context_snapshot(
        workspace=workspace,
        active_request=prepared.ai_request,
        prepared=prepared,
        profile=profile,
        run_id=run_id,
        call_id=call_id,
    )


def _usage_int(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float) and value.is_integer():
            return max(0, int(value))
        if isinstance(value, str) and value.strip():
            try:
                return max(0, int(float(value.strip())))
            except ValueError:
                continue
    return 0


def _normalize_provider_usage(raw_usage: Any) -> dict[str, Any] | None:
    if raw_usage is None:
        return None
    if hasattr(raw_usage, "to_dict"):
        maybe_payload = raw_usage.to_dict()
    elif isinstance(raw_usage, dict):
        maybe_payload = dict(raw_usage)
    else:
        return None
    if not isinstance(maybe_payload, dict):
        return None

    prompt_tokens = _usage_int(maybe_payload, "prompt_tokens", "promptTokens", "input_tokens", "inputTokens")
    completion_tokens = _usage_int(
        maybe_payload,
        "completion_tokens",
        "completionTokens",
        "output_tokens",
        "outputTokens",
    )
    total_tokens = _usage_int(maybe_payload, "total_tokens", "totalTokens")
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens <= 0:
        return None

    return {
        "cached_tokens": _usage_int(maybe_payload, "cached_tokens", "cachedTokens"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated": bool(maybe_payload.get("estimated", False)),
        "prompt_chars": _usage_int(maybe_payload, "prompt_chars", "promptChars"),
        "completion_chars": _usage_int(maybe_payload, "completion_chars", "completionChars"),
    }


def _get_cognitive_runtime_receipt_deps() -> tuple[Any, Any]:
    from polaris.cells.factory.cognitive_runtime.public import (
        RecordRuntimeReceiptCommandV1,
        get_cognitive_runtime_public_service,
    )

    return RecordRuntimeReceiptCommandV1, get_cognitive_runtime_public_service


# Import-time side effect preserved (exact former message):
# Instructor integration
try:
    from polaris.infrastructure.llm.instructor_client import create_structured_client  # noqa: F401

    INSTRUCTOR_AVAILABLE = True
except ImportError:
    INSTRUCTOR_AVAILABLE = False
