"""Stable public service exports for `context.engine`.

Architecture (P1-CTX-003 convergence):
    Cells should not directly import from kernelone.context.internal modules.
    This module uses TYPE_CHECKING guards for type annotations and lazy imports
    for runtime usage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.cells.context.engine.internal.precision_mode import (
    merge_policy,
    resolve_cost_class,
    route_by_cost_model,
)
from polaris.kernelone.context.runtime_feature_flags import resolve_context_os_enabled
from polaris.kernelone.memory.integration import (
    get_persona_text,
    init_anthropomorphic_modules,
)
from polaris.kernelone.memory.schema import PromptContext

from .contracts import (
    BuildRoleContextCommandV1,
    ContextEngineError,
    ContextResolvedEventV1,
    ResolveRoleContextQueryV1,
    RoleContextResultV1,
)

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

    assembler = PromptChunkAssembler(model_window=128_000, safety_margin=0.85)
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
    "ResolveRoleContextQueryV1",
    "RoleContextResultV1",
    "build_context_window",
    "get_anthropomorphic_context_v2",
]
