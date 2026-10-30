"""Aggregate role plan builders, failure signals, and takeover directives."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    merge_failure_evidence_payload,
)
from polaris.cells.roles.runtime.public.aggregate_chat._entrypoint_checks import (
    _check_aggregate_entrypoint,
    _dedupe_tokens,
)
from polaris.cells.roles.runtime.public.aggregate_chat._specs import (
    _AGGREGATE_FAILURE_EVIDENCE_KEYS,
    _AGGREGATE_FAILURE_SIGNAL_ALIASES,
    _AGGREGATE_MODEL_ID,
    _AGGREGATE_RUNTIME_INTEGRATION_SPECS,
    _DEFAULT_AGGREGATE_ROLE_IDS,
)
from polaris.cells.roles.runtime.public.contracts import (
    AggregateCognitiveLedgerEntryV1,
    AggregateRoleLobeV1,
    AggregateRolePlanResultV1,
    AggregateRuntimeAuditResultV1,
    AggregateRuntimeIntegrationV1,
    AggregateTakeoverDirectiveV1,
    BuildAggregateRolePlanQueryV1,
)


def _select_aggregate_role_ids(
    requested_role_ids: tuple[str, ...],
    available_role_ids: set[str],
) -> tuple[str, ...]:
    if requested_role_ids:
        return _dedupe_tokens(role_id for role_id in requested_role_ids if role_id in available_role_ids)
    selected = tuple(role_id for role_id in _DEFAULT_AGGREGATE_ROLE_IDS if role_id in available_role_ids)
    if selected:
        return selected
    return tuple(sorted(available_role_ids))


def _build_aggregate_lobe(
    spec: Mapping[str, Any],
    *,
    selected_role_ids: set[str],
    available_role_ids: set[str],
    include_virtual_lobes: bool,
) -> AggregateRoleLobeV1:
    role_ids = _dedupe_tokens(spec.get("role_ids") or ())
    virtual_role_ids = _dedupe_tokens(spec.get("virtual_role_ids") or ()) if include_virtual_lobes else ()
    missing_role_ids = tuple(
        role_id for role_id in role_ids if role_id not in selected_role_ids or role_id not in available_role_ids
    )
    status = "active" if not missing_role_ids else "partial"
    return AggregateRoleLobeV1(
        lobe_id=str(spec.get("lobe_id") or ""),
        title=str(spec.get("title") or ""),
        phase=str(spec.get("phase") or ""),
        role_ids=role_ids,
        virtual_role_ids=virtual_role_ids,
        capability_refs=_dedupe_tokens(spec.get("capability_refs") or ()),
        attention_masks=_dedupe_tokens(spec.get("attention_masks") or ()),
        memory_triggers=_dedupe_tokens(spec.get("memory_triggers") or ()),
        compute_tier=str(spec.get("compute_tier") or "unspecified"),
        handoff_keys=_dedupe_tokens(spec.get("handoff_keys") or ()),
        takeover_triggers=_dedupe_tokens(spec.get("takeover_triggers") or ()),
        output_contract=str(spec.get("output_contract") or ""),
        status=status,
        missing_role_ids=missing_role_ids,
        metadata={
            "truthful_migration": (
                "virtual_role_ids are aggregate lobes or critics, not current roles.profile entries"
                if virtual_role_ids
                else "all role_ids are current roles.profile entries"
            ),
            "stateful": False,
        },
    )


def _build_cognitive_ledger(lobes: tuple[AggregateRoleLobeV1, ...]) -> tuple[AggregateCognitiveLedgerEntryV1, ...]:
    entries: list[AggregateCognitiveLedgerEntryV1] = []
    for index, lobe in enumerate(lobes):
        next_lobe = lobes[index + 1].lobe_id if index + 1 < len(lobes) else ""
        entries.append(
            AggregateCognitiveLedgerEntryV1(
                sequence=index,
                lobe_id=lobe.lobe_id,
                phase=lobe.phase,
                compute_tier=lobe.compute_tier,
                reads=(*lobe.capability_refs, *lobe.memory_triggers),
                writes=(*lobe.handoff_keys, lobe.output_contract),
                handoff_to=(next_lobe,) if next_lobe else (),
                takeover_triggers=lobe.takeover_triggers,
            )
        )
    return tuple(entries)


def _build_compute_policy(lobes: tuple[AggregateRoleLobeV1, ...]) -> dict[str, Any]:
    tier_order = _dedupe_tokens(lobe.compute_tier for lobe in lobes)
    return {
        "policy_id": "aggregate_compute_swap.v1",
        "tier_order": tier_order,
        "default_priority": "local_self_heal_first_after_compiler_feedback",
        "cloud_priority_conditions": (
            "architectural_ambiguity",
            "graph_boundary_violation",
            "high_blast_radius",
        ),
        "local_priority_conditions": (
            "compile_failure",
            "typecheck_failure",
            "failed_apply",
            "localization_uncertain",
        ),
        "rationale": (
            "Use cloud critique for high-ambiguity boundary decisions, but route "
            "compiler/test failures to local self-heal loops first because they are "
            "measurable and produce reusable Cognitive Runtime receipts."
        ),
    }


def _build_runtime_integrations(workspace: str) -> tuple[AggregateRuntimeIntegrationV1, ...]:
    integrations: list[AggregateRuntimeIntegrationV1] = []
    for spec in _AGGREGATE_RUNTIME_INTEGRATION_SPECS:
        production_entrypoints = _dedupe_tokens(spec.get("production_entrypoints") or ())
        entrypoint_checks = tuple(
            _check_aggregate_entrypoint(workspace, entrypoint) for entrypoint in production_entrypoints
        )
        missing_entrypoints = tuple(check.entrypoint for check in entrypoint_checks if not check.ok)
        integrations.append(
            AggregateRuntimeIntegrationV1(
                tech_id=str(spec.get("tech_id") or ""),
                title=str(spec.get("title") or ""),
                status=str(spec.get("status") or ""),
                priority=str(spec.get("priority") or ""),
                production_entrypoints=production_entrypoints,
                trigger_keys=_dedupe_tokens(spec.get("trigger_keys") or ()),
                evidence_keys=_dedupe_tokens(spec.get("evidence_keys") or ()),
                runtime_effects=_dedupe_tokens(spec.get("runtime_effects") or ()),
                benefit=str(spec.get("benefit") or ""),
                capability_refs=_dedupe_tokens(spec.get("capability_refs") or ()),
                entrypoint_checks=entrypoint_checks,
                entrypoints_verified=bool(entrypoint_checks) and not missing_entrypoints,
                missing_entrypoints=missing_entrypoints,
            )
        )
    return tuple(integrations)


def _build_runtime_audit_result(
    *,
    workspace: str,
    integrations: tuple[AggregateRuntimeIntegrationV1, ...],
    metadata: Mapping[str, Any] | None = None,
) -> AggregateRuntimeAuditResultV1:
    wired = tuple(item for item in integrations if item.status == "wired" and item.entrypoints_verified)
    available = tuple(item for item in integrations if item.status == "available" and item.entrypoints_verified)
    planned_bridge = tuple(item for item in integrations if item.status == "planned_bridge")
    missing_checks = tuple(
        check for integration in integrations for check in integration.entrypoint_checks if not check.ok
    )
    verified_checks = tuple(
        check for integration in integrations for check in integration.entrypoint_checks if check.ok
    )
    priority_wired = tuple(item.tech_id for item in wired if item.priority == "p0")
    warnings: list[str] = []
    if planned_bridge:
        warnings.append(f"planned_bridge:{','.join(item.tech_id for item in planned_bridge)}")
    if missing_checks:
        warnings.append(f"missing_entrypoints:{','.join(check.entrypoint for check in missing_checks)}")
    return AggregateRuntimeAuditResultV1(
        ok=bool(integrations) and len(priority_wired) >= 4 and not missing_checks,
        workspace=workspace,
        aggregate_model_id=_AGGREGATE_MODEL_ID,
        integrations=integrations,
        wired_count=len(wired),
        available_count=len(available),
        planned_bridge_count=len(planned_bridge),
        verified_entrypoint_count=len(verified_checks),
        missing_entrypoint_count=len(missing_checks),
        priority_wired=priority_wired,
        warnings=tuple(warnings),
        metadata={
            "audit_scope": "aggregate_llm_unique_technology_runtime_integrations",
            "status_semantics": {
                "wired": "current aggregate runtime path has a production entrypoint",
                "available": "implemented capability exists but aggregate path does not force it yet",
                "planned_bridge": "known architecture asset still needs an aggregate bridge",
            },
            **dict(metadata or {}),
        },
    )


def _normalize_failure_signal(value: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _AGGREGATE_FAILURE_SIGNAL_ALIASES.get(token, token)


def _extract_failure_signals(query: BuildAggregateRolePlanQueryV1) -> tuple[str, ...]:
    signals: list[str] = []
    signals.extend(query.failure_signals)
    for source in (query.context, query.metadata):
        for key in ("failure_signal", "degraded_signal", "error_signal"):
            raw_value = source.get(key) if isinstance(source, Mapping) else None
            if isinstance(raw_value, str) and raw_value.strip():
                signals.append(raw_value)
        raw_values = source.get("failure_signals") if isinstance(source, Mapping) else None
        if isinstance(raw_values, list | tuple):
            signals.extend(str(item or "") for item in raw_values)
    return _dedupe_tokens(_normalize_failure_signal(signal) for signal in signals if str(signal or "").strip())


def _extract_failure_evidence(query: BuildAggregateRolePlanQueryV1) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for source in (query.context, query.metadata):
        raw_evidence = source.get("failure_evidence") if isinstance(source, Mapping) else None
        evidence = merge_failure_evidence_payload(evidence, raw_evidence)
    evidence = merge_failure_evidence_payload(evidence, query.failure_evidence)
    return evidence


def _aggregate_plan_failure_evidence_payload(plan: AggregateRolePlanResultV1) -> dict[str, Any]:
    """Return the canonical aggregate failure-evidence projection for a plan."""

    return merge_failure_evidence_payload({}, plan.metadata.get("failure_evidence"))


def _build_takeover_evidence_status(
    *,
    takeover_directive: AggregateTakeoverDirectiveV1 | None,
    failure_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if takeover_directive is None:
        return {
            "required_keys": (),
            "present_keys": tuple(sorted(str(key) for key in failure_evidence)),
            "missing_keys": (),
            "complete": True,
        }
    required_keys = takeover_directive.evidence_keys
    present_keys = tuple(
        key for key in required_keys if key in failure_evidence and failure_evidence.get(key) is not None
    )
    missing_keys = tuple(key for key in required_keys if key not in present_keys)
    return {
        "required_keys": required_keys,
        "present_keys": present_keys,
        "missing_keys": missing_keys,
        "complete": not missing_keys,
    }


def _build_takeover_directive(
    *,
    lobes: tuple[AggregateRoleLobeV1, ...],
    cognitive_ledger: tuple[AggregateCognitiveLedgerEntryV1, ...],
    failure_signals: tuple[str, ...],
) -> AggregateTakeoverDirectiveV1 | None:
    if not failure_signals:
        return None
    ledger_by_lobe = {entry.lobe_id: entry for entry in cognitive_ledger}
    for signal in failure_signals:
        for lobe in lobes:
            if signal not in lobe.takeover_triggers and signal not in lobe.memory_triggers:
                continue
            entry = ledger_by_lobe.get(lobe.lobe_id)
            next_lobes = entry.handoff_to if entry is not None else ()
            return AggregateTakeoverDirectiveV1(
                trigger=signal,
                lobe_id=lobe.lobe_id,
                compute_tier=lobe.compute_tier,
                reason=f"{signal} activates {lobe.lobe_id} through aggregate takeover triggers",
                evidence_keys=_AGGREGATE_FAILURE_EVIDENCE_KEYS.get(signal, ("failure_signal",)),
                action_contract=lobe.output_contract,
                next_lobes=next_lobes,
            )
    return None
