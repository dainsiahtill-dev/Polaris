"""Stable public service exports for `context.engine`.

Architecture (P1-CTX-003 convergence):
    Cells should not directly import from kernelone.context.internal modules.
    This module uses TYPE_CHECKING guards for type annotations and lazy imports
    for runtime usage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.context.engine.internal.precision_mode import (
    merge_policy,
    resolve_cost_class,
    route_by_cost_model,
)
from polaris.kernelone.context.runtime_feature_flags import resolve_context_os_enabled
from polaris.kernelone.llm.engine.context_store_retention import (
    ContextSnapshotAuditPinError,
    ContextSnapshotAuditPinRepository,
)
from polaris.kernelone.llm.engine.internal.context_hash import validate_context_hash
from polaris.kernelone.memory.integration import (
    get_persona_text,
    init_anthropomorphic_modules,
)
from polaris.kernelone.memory.schema import PromptContext

from .contracts import (
    BuildRoleContextCommandV1,
    ContextEngineError,
    ContextResolvedEventV1,
    FactoryRunContextSnapshotsResultV1,
    FinalProviderRequestAuditResultV1,
    QueryFactoryRunContextSnapshotsV1,
    QueryFinalProviderRequestAuditV1,
    ResolveRoleContextQueryV1,
    RoleContextResultV1,
)
from .snapshot_paths import context_snapshot_candidates

# TYPE_CHECKING block for type annotations only (P1-CTX-003 convergence)
if TYPE_CHECKING:
    # Used in function signatures as forward references
    from polaris.kernelone.context.engine import (
        ContextBudget,
        ContextItem,
        ContextPack,
        ContextRequest,
    )


def build_context_window(
    project_root: str,
    role: str,
    query: str,
    step: int,
    run_id: str,
    mode: str,
    *,
    events_path: str = "",
    cost_model: str | None = None,
    sources_enabled: list[str] | None = None,
    policy: dict[str, Any] | None = None,
    context_override: dict[str, Any] | None = None,
    session_id: str = "",
) -> tuple[ContextPack, dict[str, Any], ContextBudget, list[str]]:
    """Build a role-scoped context window through the canonical context engine."""
    merged_input_policy = dict(policy or {})
    cost_class = resolve_cost_class(cost_model or merged_input_policy.get("cost_class"))
    strategy = route_by_cost_model(cost_class, role)
    merged_policy = merge_policy(strategy.policy, merged_input_policy)
    merged_policy["cost_class"] = cost_class

    max_tokens = _coerce_int(merged_policy.get("max_tokens"), strategy.budget.get("max_tokens", 0))
    max_chars = _coerce_int(merged_policy.get("max_chars"), strategy.budget.get("max_chars", 0))

    # Lazy import for runtime (P1-CTX-003 convergence)
    from polaris.kernelone.context.engine import ContextBudget

    budget = ContextBudget(max_tokens=max_tokens, max_chars=max_chars, cost_class=cost_class)

    resolved_sources = list(sources_enabled or [])
    if not resolved_sources:
        configured_sources = merged_policy.get("sources_enabled")
        if isinstance(configured_sources, list):
            resolved_sources = [str(item) for item in configured_sources if str(item or "").strip()]
        else:
            resolved_sources = list(strategy.sources_enabled)

    request = _build_context_request(
        run_id=run_id,
        step=step,
        role=role,
        mode=mode,
        query=query,
        budget=budget,
        sources_enabled=resolved_sources,
        policy=merged_policy,
        events_path=events_path or "",
    )
    resolved_override = _resolve_context_override(
        context_override=context_override,
        session_id=session_id,
    )
    pack = _build_context_pack(project_root, request)
    pack = _apply_context_os_overlay(
        pack,
        role=role,
        session_id=session_id,
        turn_index=step,
        context_override=resolved_override,
        policy=merged_policy,
    )
    return pack, merged_policy, budget, resolved_sources


def get_anthropomorphic_context_v2(
    project_root: str,
    role: str,
    query: str,
    step: int,
    run_id: str,
    phase: str,
    *,
    events_path: str = "",
    sources_enabled: list[str] | None = None,
    policy: dict[str, Any] | None = None,
    context_override: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """Build the v2 prompt context bundle through the canonical context engine."""
    init_anthropomorphic_modules(project_root)
    persona_text = get_persona_text(role, project_root=project_root)
    context_policy = dict(policy or {})

    pack, _, _, _ = build_context_window(
        project_root,
        role,
        query,
        step,
        run_id,
        phase,
        events_path=events_path or "",
        sources_enabled=sources_enabled,
        policy=context_policy,
        context_override=context_override,
        session_id=session_id,
    )

    prompt_context = PromptContext(
        run_id=run_id,
        phase=phase,
        step=step,
        persona_id=f"{role}.v1",
        retrieved_mem_ids=[item.id for item in pack.items if item.kind == "memory"],
        retrieved_mem_scores=[],
        retrieved_ref_ids=[item.id for item in pack.items if item.kind == "reflection"],
        token_usage_estimate=pack.total_tokens,
    )

    return {
        "persona_instruction": persona_text,
        "anthropomorphic_context": pack.rendered_prompt,
        "prompt_context_obj": prompt_context,
        "context_pack": pack,
        "context_os_summary": _extract_context_os_summary(pack),
    }


def get_search_service() -> Any:
    """Return the graph-constrained semantic search service via the public boundary."""
    from polaris.cells.context.engine.internal.search_gateway import get_search_service as _get_search_service

    return _get_search_service()


def _context_hash_from_ref(context_snapshot_ref: str) -> str:
    token = str(context_snapshot_ref or "").strip().replace("\\", "/")
    if "/" in token:
        token = token.rstrip("/").rsplit("/", 1)[-1]
    return validate_context_hash(token)


def _project_final_physical_provider_request(
    *,
    snapshot_payload: dict[str, Any],
    provider_request: dict[str, Any],
) -> dict[str, Any] | None:
    """Project the exact provider-native body for ContextOS/UI consumers."""

    if snapshot_payload.get("schema_version") != "llm.final_physical_provider_request_context.v1":
        return None
    wire = provider_request.get("final_physical_request")
    route = provider_request.get("physical_route_authority")
    if not isinstance(wire, dict) or not isinstance(route, dict):
        return None
    body = wire.get("body")
    if not isinstance(body, dict):
        return None
    protocol = str(route.get("native_protocol") or "")
    if protocol == "anthropic_messages":
        messages_raw = body.get("messages")
        messages = list(messages_raw) if isinstance(messages_raw, list) else []
        if "system" in body:
            messages = [{"role": "system", "content": body.get("system")}, *messages]
    elif protocol == "openai_responses":
        messages_raw = body.get("input")
        messages = list(messages_raw) if isinstance(messages_raw, list) else []
    elif protocol == "openai_chat_completions":
        messages_raw = body.get("messages")
        messages = list(messages_raw) if isinstance(messages_raw, list) else []
    else:
        return None
    tools_raw = body.get("tools")
    return {
        "messages": messages,
        "tools": list(tools_raw) if isinstance(tools_raw, list) else [],
        "tool_choice": body.get("tool_choice"),
        "response_format": body.get("response_format"),
        "provider_request_schema_version": route.get("native_request_schema_version"),
        "native_protocol": protocol,
        "endpoint": wire.get("endpoint"),
        "transport_kind": wire.get("transport_kind"),
        "body": body,
        "wire": wire,
    }


def query_final_provider_request_audit(
    query: QueryFinalProviderRequestAuditV1,
) -> FinalProviderRequestAuditResultV1:
    """Read final provider request audit evidence from a stored ContextOS snapshot."""

    try:
        context_hash = _context_hash_from_ref(query.context_snapshot_ref)
    except ValueError as exc:
        return FinalProviderRequestAuditResultV1(
            ok=False,
            status="invalid_ref",
            workspace=query.workspace,
            context_snapshot_ref=query.context_snapshot_ref,
            payload={},
            error_code="invalid_context_snapshot_ref",
            error_message=str(exc),
        )

    storage_source = ""
    file_path: Path | None = None
    candidates = context_snapshot_candidates(query.workspace, context_hash)
    for candidate_source, candidate_path in candidates:
        if candidate_path.is_file():
            storage_source = candidate_source
            file_path = candidate_path
            break

    if file_path is None:
        return FinalProviderRequestAuditResultV1(
            ok=False,
            status="not_found",
            workspace=query.workspace,
            context_snapshot_ref=query.context_snapshot_ref,
            payload={
                "context_hash": context_hash,
                "searched_paths": [{"source": source, "context_path": str(path)} for source, path in candidates],
            },
            error_code="context_snapshot_not_found",
            error_message=f"Context snapshot not found for hash {context_hash}.",
        )

    try:
        with open(file_path, encoding="utf-8") as handle:
            snapshot_payload = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return FinalProviderRequestAuditResultV1(
            ok=False,
            status="unreadable",
            workspace=query.workspace,
            context_snapshot_ref=query.context_snapshot_ref,
            payload={"context_hash": context_hash, "context_path": str(file_path), "storage_source": storage_source},
            error_code="context_snapshot_unreadable",
            error_message=str(exc),
        )
    if not isinstance(snapshot_payload, dict):
        return FinalProviderRequestAuditResultV1(
            ok=False,
            status="invalid_snapshot",
            workspace=query.workspace,
            context_snapshot_ref=query.context_snapshot_ref,
            payload={"context_hash": context_hash, "context_path": str(file_path), "storage_source": storage_source},
            error_code="context_snapshot_invalid_format",
            error_message="Context snapshot payload must be an object.",
        )

    provider_request_raw = snapshot_payload.get("provider_request")
    provider_request = provider_request_raw if isinstance(provider_request_raw, dict) else {}
    if not provider_request:
        return FinalProviderRequestAuditResultV1(
            ok=False,
            status="missing_provider_request",
            workspace=query.workspace,
            context_snapshot_ref=query.context_snapshot_ref,
            payload={
                "context_hash": context_hash,
                "context_path": str(file_path),
                "storage_source": storage_source,
                "trace_id": snapshot_payload.get("trace_id"),
                "call_id": snapshot_payload.get("call_id"),
            },
            error_code="provider_request_missing",
            error_message="Context snapshot does not include provider_request audit evidence.",
        )

    physical_projection = _project_final_physical_provider_request(
        snapshot_payload=snapshot_payload,
        provider_request=provider_request,
    )
    if (
        snapshot_payload.get("schema_version") == "llm.final_physical_provider_request_context.v1"
        and physical_projection is None
    ):
        return FinalProviderRequestAuditResultV1(
            ok=False,
            status="invalid_snapshot",
            workspace=query.workspace,
            context_snapshot_ref=query.context_snapshot_ref,
            payload={
                "context_hash": context_hash,
                "context_path": str(file_path),
                "storage_source": storage_source,
            },
            error_code="final_physical_provider_request_invalid",
            error_message="Final physical provider request has an unsupported or invalid native protocol.",
        )
    messages_raw = snapshot_payload.get("messages")
    messages = messages_raw if isinstance(messages_raw, list) else []
    final_audit_raw = provider_request.get("final_request_context_audit")
    final_audit = dict(final_audit_raw) if isinstance(final_audit_raw, dict) else {}
    coverage_raw = final_audit.get("final_request_evidence_coverage")
    if isinstance(coverage_raw, dict):
        coverage = dict(coverage_raw)
        coverage["context_snapshot_ref"] = context_hash
        final_audit["final_request_evidence_coverage"] = coverage
    tools_raw = provider_request.get("tools")
    tools = tools_raw if isinstance(tools_raw, list) else []
    if physical_projection is not None:
        messages = physical_projection["messages"]
        tools = physical_projection["tools"]
    return FinalProviderRequestAuditResultV1(
        ok=True,
        status="available",
        workspace=query.workspace,
        context_snapshot_ref=query.context_snapshot_ref,
        payload={
            "schema_version": "context.final_provider_request_audit.v1",
            "context_hash": context_hash,
            "context_path": str(file_path),
            "storage_source": storage_source,
            "trace_id": snapshot_payload.get("trace_id"),
            "call_id": snapshot_payload.get("call_id"),
            "stored_at": snapshot_payload.get("stored_at"),
            "message_count": len(messages),
            "messages": messages,
            "provider_request": provider_request,
            "provider_request_schema_version": (
                physical_projection["provider_request_schema_version"]
                if physical_projection is not None
                else provider_request.get("schema_version")
            ),
            "role": provider_request.get("role"),
            "provider_id": provider_request.get("provider_id"),
            "provider_type": provider_request.get("provider_type"),
            "model": provider_request.get("model"),
            "tools": tools,
            "tool_choice": (
                physical_projection["tool_choice"]
                if physical_projection is not None
                else provider_request.get("tool_choice")
            ),
            "response_format": (
                physical_projection["response_format"]
                if physical_projection is not None
                else provider_request.get("response_format")
            ),
            "native_protocol": physical_projection["native_protocol"] if physical_projection else None,
            "physical_endpoint": physical_projection["endpoint"] if physical_projection else None,
            "physical_transport_kind": physical_projection["transport_kind"] if physical_projection else None,
            "final_physical_request_body": physical_projection["body"] if physical_projection else None,
            "final_physical_request": physical_projection["wire"] if physical_projection else None,
            "final_request_context_audit": final_audit,
        },
    )


def query_factory_run_context_snapshots(
    query: QueryFactoryRunContextSnapshotsV1,
) -> FactoryRunContextSnapshotsResultV1:
    """Read immutable final-request audit pins for one exact Factory run."""

    try:
        pins = ContextSnapshotAuditPinRepository(workspace=query.workspace).query_factory_run_pins(
            query.factory_run_id
        )
    except (ContextSnapshotAuditPinError, OSError, RuntimeError, ValueError) as exc:
        return FactoryRunContextSnapshotsResultV1(
            ok=False,
            status="unavailable",
            workspace=query.workspace,
            factory_run_id=query.factory_run_id,
            pins=(),
            error_code="factory_run_context_snapshot_query_failed",
            error_message=str(exc),
        )
    return FactoryRunContextSnapshotsResultV1(
        ok=True,
        status="available" if pins else "missing",
        workspace=query.workspace,
        factory_run_id=query.factory_run_id,
        pins=tuple(pin.to_record() for pin in pins),
    )


def _build_context_request(
    run_id: str,
    step: int,
    role: str,
    mode: str,
    query: str,
    budget: ContextBudget,
    sources_enabled: list[str],
    policy: dict[str, Any],
    events_path: str,
) -> ContextRequest:
    """Build ContextRequest with lazy import (P1-CTX-003 convergence)."""
    from polaris.kernelone.context.engine import ContextRequest

    return ContextRequest(
        run_id=run_id,
        step=step,
        role=role,
        mode=mode,
        query=query,
        budget=budget,
        sources_enabled=sources_enabled,
        policy=policy,
        events_path=events_path,
    )


def _apply_context_os_overlay(
    pack: ContextPack,
    *,
    role: str,
    session_id: str,
    turn_index: int,
    context_override: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
) -> ContextPack:
    if not resolve_context_os_enabled(
        incoming_context=context_override if isinstance(context_override, dict) else None,
        session_context_config=policy if isinstance(policy, dict) else None,
        default=True,
    ):
        return pack
    overlay = _build_context_os_overlay(
        role=role,
        session_id=session_id,
        turn_index=turn_index,
        context_override=context_override,
        policy=policy,
    )
    if overlay is None:
        return pack

    block, item, summary = overlay
    rendered_prompt = f"{block}\n\n{pack.rendered_prompt}".strip() if pack.rendered_prompt else block
    rendered_messages = [{"role": "user", "content": rendered_prompt}]
    compression_log = list(pack.compression_log)
    compression_log.append(
        {
            "action": "context_os_overlay",
            "summary": summary,
        }
    )
    items = [item, *list(pack.items)]
    return pack.model_copy(
        update={
            "items": items,
            "compression_log": compression_log,
            "rendered_prompt": rendered_prompt,
            "rendered_messages": rendered_messages,
            "total_tokens": _estimate_tokens(rendered_prompt),
            "total_chars": len(rendered_prompt),
        }
    )


def _build_context_os_overlay(
    *,
    role: str,
    session_id: str,
    turn_index: int,
    context_override: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
) -> tuple[str, ContextItem, dict[str, Any]] | None:
    # Lazy imports (P1-CTX-003 convergence)
    from polaris.kernelone.context.chunks import PromptChunkAssembler
    from polaris.kernelone.context.context_os import summarize_context_os_payload
    from polaris.kernelone.context.engine import ContextItem

    override = dict(context_override or {})
    continuity = override.get("session_continuity")
    state_first = override.get("state_first_context_os")
    continuity_payload = dict(continuity) if isinstance(continuity, dict) else {}
    state_first_payload = dict(state_first) if isinstance(state_first, dict) else {}
    if not continuity_payload and not state_first_payload:
        return None

    summary_text = str(continuity_payload.get("summary") or "").strip()
    source_messages = _coerce_int(continuity_payload.get("source_message_count"), 0)

    assembler = PromptChunkAssembler(
        model_window=_resolve_context_os_overlay_model_window(policy),
        safety_margin=0.85,
    )
    chunk = assembler.add_continuity(
        summary_text,
        source_messages=max(0, source_messages),
        context_os=state_first_payload or None,
        source="context.engine",
        role_id=role,
        session_id=session_id,
        turn_index=turn_index,
    )
    rendered = str(chunk.content or "").strip()
    if not rendered:
        return None

    summary = summarize_context_os_payload(state_first_payload or None)
    refs = {
        "adapter_id": str(summary.get("adapter_id") or "").strip(),
        "pressure_level": str(summary.get("pressure_level") or "").strip(),
        "session_id": str(session_id or "").strip(),
    }
    item = ContextItem(
        kind="continuity",
        provider="context_os_overlay",
        content_or_pointer=rendered,
        refs=refs,
        size_est=_estimate_tokens(rendered),
        priority=200,
        reason="State-First Context OS continuity overlay",
    )
    return rendered, item, summary


def _resolve_context_os_overlay_model_window(policy: dict[str, Any] | None) -> int:
    payload = dict(policy or {})
    candidate_keys = (
        "resolved_context_window",
        "model_window_tokens",
        "model_context_window",
        "context_window_tokens",
        "max_context_tokens",
    )
    for key in candidate_keys:
        value = _coerce_int(payload.get(key), 0)
        if value > 0:
            return value
    context_window = payload.get("context_window")
    if isinstance(context_window, dict):
        for key in candidate_keys:
            value = _coerce_int(context_window.get(key), 0)
            if value > 0:
                return value
    return 1


def _extract_context_os_summary(pack: ContextPack) -> dict[str, Any]:
    for entry in reversed(list(pack.compression_log or [])):
        if entry.get("action") == "context_os_overlay" and isinstance(entry.get("summary"), dict):
            return dict(entry["summary"])
    return {}


def _build_context_pack(project_root: str, request: ContextRequest) -> ContextPack:
    """Build context pack with lazy import (P1-CTX-003 convergence)."""
    from polaris.kernelone.context.engine import ContextEngine

    return ContextEngine(project_root).build_context(request)


def _estimate_tokens(text: str) -> int:
    """Estimate tokens with lazy import (P1-CTX-006 convergence)."""
    from polaris.kernelone.context.engine import _estimate_tokens as estimate

    return estimate(text)


def _resolve_context_override(
    *,
    context_override: dict[str, Any] | None,
    session_id: str,
) -> dict[str, Any] | None:
    resolved = dict(context_override or {})
    if not session_id:
        return resolved or None
    try:
        from polaris.cells.roles.session.public import RoleSessionService

        with RoleSessionService() as session_service:
            session_context = session_service.get_context_config_dict(session_id)
        if isinstance(session_context, dict):
            for key, value in session_context.items():
                if key not in resolved:
                    resolved[key] = value
    except (RuntimeError, ValueError, TypeError, ImportError, AttributeError):
        # context.engine must remain robust when roles.session is unavailable.
        pass
    return resolved or None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


__all__ = [
    "BuildRoleContextCommandV1",
    "ContextEngineError",
    "ContextResolvedEventV1",
    "FactoryRunContextSnapshotsResultV1",
    "FinalProviderRequestAuditResultV1",
    "QueryFactoryRunContextSnapshotsV1",
    "QueryFinalProviderRequestAuditV1",
    "ResolveRoleContextQueryV1",
    "RoleContextResultV1",
    "build_context_window",
    "get_anthropomorphic_context_v2",
    "get_search_service",
    "query_factory_run_context_snapshots",
    "query_final_provider_request_audit",
]
