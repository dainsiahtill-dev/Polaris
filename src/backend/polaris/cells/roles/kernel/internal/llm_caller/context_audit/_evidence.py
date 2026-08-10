from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.kernelone.audit.context_os_prompt import compact_context_os_audit
from polaris.kernelone.events.final_request_evidence import (
    build_final_request_coverage_sources,
    build_final_request_evidence_slots,
    build_final_request_tool_slots,
    final_request_evidence_ref_for_requirement,
    final_request_evidence_refs_for_coverage_flags,
    final_request_included_evidence_refs,
    final_request_structured_evidence_from_metadata_summary,
    missing_required_refs_from_evidence_coverage,
    missing_required_tools_from_evidence_coverage,
    role_final_request_policy,
)

from ..response_types import PreparedLLMRequest
from ._constants import (
    _INTERFACE_DISCREPANCY_CONTEXT_KEYS,
    _OPTIONAL_CONTEXT_QUALITY_FLAGS,
    _REF_BASED_SUPERSEDED_FINDING_CODES,
)
from ._payloads import (
    _request_sampling_audit,
)
from ._primitives import (
    _bool_value,
    _mapping,
    _non_empty_attr,
    _stable_digest,
    _string_list,
    _unique_strings,
)
from ._request_core import (
    _execution_envelope,
    _execution_strategy,
    _final_request_receipt_refs,
    _final_request_redaction_safety,
    _request_context,
    _task_type_value,
)
from ._tools import (
    _allowed_tool_names,
    _available_tool_names,
    _provider_protocol_schema_coverage,
    _required_tool_names,
    _required_tools_exempt_reason,
    _tool_schema_registry_coverage,
)


class FinalRequestEvidenceCoverageError(RuntimeError):
    """Raised when a strict final provider request evidence policy fails."""

    def __init__(self, violation: dict[str, Any]) -> None:
        self.violation = dict(violation)
        super().__init__(str(self.violation.get("message") or "Final provider request evidence coverage failed"))


def _envelope_hash_for_ref(envelope: dict[str, Any], section: str) -> str:
    payload = _mapping(envelope.get(section))
    raw_hash = payload.get("hash")
    return str(raw_hash or "").strip()


def _workflow_chain(
    *,
    ai_request: Any,
    request_metadata_summary: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, str]:
    context_payload = _request_context(ai_request)
    return {
        "pm_contract_hash": str(
            context_payload.get("pm_contract_hash")
            or context_payload.get("contract_hash")
            or _envelope_hash_for_ref(envelope, "pm_contract")
            or ""
        ),
        "ce_blueprint_hash": str(
            context_payload.get("ce_blueprint_hash")
            or context_payload.get("blueprint_hash")
            or _envelope_hash_for_ref(envelope, "ce_blueprint")
            or ""
        ),
        "handoff_decision_hash": str(
            context_payload.get("handoff_decision_hash")
            or context_payload.get("ce_handoff_decision_hash")
            or _envelope_hash_for_ref(envelope, "handoff_decision")
            or ""
        ),
        "execution_profile_hash": str(request_metadata_summary.get("execution_profile_hash") or ""),
        "execution_envelope_hash": str(request_metadata_summary.get("execution_envelope_hash") or ""),
    }


def _mapped_evidence_requirements(raw_requirements: Any) -> list[str]:
    refs: list[str] = []
    for item in _string_list(raw_requirements):
        refs.append(final_request_evidence_ref_for_requirement(item))
    return _unique_strings(refs)


def _required_evidence_refs(
    *,
    ai_request: Any,
    role_id: str,
    coverage: dict[str, bool],
    request_metadata_summary: dict[str, Any],
    execution_strategy: dict[str, Any],
    envelope: dict[str, Any],
) -> list[str]:
    envelope_audit_policy = _mapping(envelope.get("audit_policy"))
    refs = _mapped_evidence_requirements(execution_strategy.get("evidence_requirements"))
    refs.extend(_mapped_evidence_requirements(envelope_audit_policy.get("required_evidence")))
    if not refs:
        normalized_role = role_id.strip().lower()
        try:
            refs.extend(
                _mapped_evidence_requirements(
                    role_final_request_policy(normalized_role).required_present_slots,
                )
            )
        except ValueError:
            refs.extend(
                final_request_evidence_refs_for_coverage_flags(
                    coverage,
                    excluded_flags=_OPTIONAL_CONTEXT_QUALITY_FLAGS,
                )
            )
    if request_metadata_summary.get("has_execution_profile"):
        refs.extend(_mapped_evidence_requirements(("execution_profile",)))
    if request_metadata_summary.get("has_execution_strategy"):
        refs.extend(_mapped_evidence_requirements(("execution_strategy", "execution_envelope")))
    if request_metadata_summary.get("has_execution_envelope"):
        refs.extend(_mapped_evidence_requirements(("execution_envelope",)))
    context_payload = _request_context(ai_request)
    refs.extend(_mapped_evidence_requirements(context_payload.get("required_evidence")))
    if any(key in context_payload for key in _INTERFACE_DISCREPANCY_CONTEXT_KEYS):
        refs.extend(_mapped_evidence_requirements(("interface_discrepancy_context",)))
    return _unique_strings(refs)


def _final_request_evidence_enforcement_source(ai_request: Any) -> str:
    context_payload = _request_context(ai_request)
    option_payload = getattr(ai_request, "options", None)
    option_payload = option_payload if isinstance(option_payload, dict) else {}
    execution_strategy = _execution_strategy(ai_request)
    envelope = _execution_envelope(ai_request)
    envelope_audit_policy = _mapping(envelope.get("audit_policy"))

    for key in (
        "final_request_evidence_required",
        "enforce_final_request_evidence_coverage",
        "required_evidence_enforcement",
    ):
        if _bool_value(context_payload.get(key)) or _bool_value(option_payload.get(key)):
            return f"request.{key}"
        if _bool_value(execution_strategy.get(key)):
            return f"execution_strategy.{key}"
        if _bool_value(envelope_audit_policy.get(key)):
            return f"execution_envelope.audit_policy.{key}"

    if _bool_value(envelope_audit_policy.get("final_provider_request_required")):
        return "execution_envelope.audit_policy.final_provider_request_required"

    return ""


def final_request_evidence_coverage_violation(
    *,
    ai_request: Any,
    audit: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a strict evidence coverage violation, if this request must fail closed."""

    source = _final_request_evidence_enforcement_source(ai_request)
    if not source:
        return None
    evidence_coverage = audit.get("final_request_evidence_coverage")
    if not isinstance(evidence_coverage, dict) or evidence_coverage.get("pass") is True:
        return None
    missing_refs = missing_required_refs_from_evidence_coverage(evidence_coverage)
    missing_tools = missing_required_tools_from_evidence_coverage(evidence_coverage)
    if not missing_refs and not missing_tools and evidence_coverage.get("role_identity_ok", True):
        return None
    message_parts = ["Final provider request evidence coverage failed"]
    if missing_refs:
        message_parts.append("missing_required_refs=" + ",".join(missing_refs))
    if missing_tools:
        message_parts.append("missing_required_tools=" + ",".join(missing_tools))
    if evidence_coverage.get("role_identity_ok") is False:
        message_parts.append("role_identity_mismatch")
    return {
        "schema_version": "polaris.final_request_evidence_enforcement.v1",
        "source": source,
        "role_id": str(evidence_coverage.get("role_id") or ""),
        "expected_role_id": str(evidence_coverage.get("expected_role_id") or ""),
        "role_identity_ok": bool(evidence_coverage.get("role_identity_ok", True)),
        "missing_required_refs": missing_refs,
        "missing_required_tools": missing_tools,
        "request_hash": str(evidence_coverage.get("request_hash") or audit.get("request_hash") or ""),
        "message": "; ".join(message_parts),
    }


def enforce_final_request_evidence_coverage(
    *,
    ai_request: Any,
    audit: dict[str, Any],
) -> None:
    """Fail closed when strict final-request evidence coverage is incomplete."""

    violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit)
    if violation is not None:
        raise FinalRequestEvidenceCoverageError(violation)


def _ledger_evidence(ai_request: Any, *, receipt_refs: list[str] | None = None) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    ledger = _mapping(context_payload.get("run_ledger")) or _mapping(context_payload.get("run_ledger_projection"))
    ledger_policy = _mapping(ledger.get("evidence_policy"))
    merged_receipt_refs: list[str] = []
    merged_receipt_refs.extend(_string_list(context_payload.get("receipt_refs")))
    merged_receipt_refs.extend(_string_list(ledger.get("receipt_refs")))
    merged_receipt_refs.extend(receipt_refs or [])
    return {
        "run_ledger_ref": str(
            context_payload.get("run_ledger_ref")
            or context_payload.get("run_ledger_projection_ref")
            or ledger.get("ref")
            or ""
        ),
        "failed_required_modalities": _string_list(
            context_payload.get("failed_required_modalities")
            or ledger.get("failed_required_modalities")
            or ledger_policy.get("failed_required_modalities")
        ),
        "missing_required_modalities": _string_list(
            context_payload.get("missing_required_modalities")
            or ledger.get("missing_required_modalities")
            or ledger_policy.get("missing_required_modalities")
        ),
        "receipt_refs": _unique_strings(merged_receipt_refs),
    }


def _final_request_hash(
    *,
    ai_request: Any,
    prepared: PreparedLLMRequest,
    messages: list[dict[str, Any]],
    tool_schema_payload: Any,
    response_format_payload: Any,
) -> str:
    return _stable_digest(
        {
            "role": _non_empty_attr(ai_request, name="role"),
            "task_type": _task_type_value(ai_request),
            "messages_hash": _stable_digest(messages),
            "tool_schema_hash": _stable_digest(tool_schema_payload),
            "response_format_hash": _stable_digest(response_format_payload),
            "sampling": _request_sampling_audit(ai_request, prepared),
        }
    )


def _final_request_evidence_coverage(
    *,
    ai_request: Any,
    prepared: PreparedLLMRequest,
    profile: Any,
    messages: list[dict[str, Any]],
    coverage: dict[str, bool],
    request_metadata_summary: dict[str, Any],
    tool_schema_payload: Any,
    tool_choice_payload: Any,
    response_format_payload: Any,
) -> dict[str, Any]:
    envelope = _execution_envelope(ai_request)
    execution_strategy = _execution_strategy(ai_request)
    role_id = _non_empty_attr(ai_request, name="role") or _non_empty_attr(profile, name="role_id") or "unknown"
    expected_role_id = _non_empty_attr(profile, name="role_id") or role_id
    role_identity_ok = role_id.strip().lower() == expected_role_id.strip().lower()
    receipt_refs = _final_request_receipt_refs(
        ai_request=ai_request,
        prepared=prepared,
        messages=messages,
    )
    included_refs = final_request_included_evidence_refs(
        coverage=coverage,
        request_metadata_summary=request_metadata_summary,
        receipt_refs=receipt_refs,
    )
    required_refs = _required_evidence_refs(
        ai_request=ai_request,
        role_id=role_id,
        coverage=coverage,
        request_metadata_summary=request_metadata_summary,
        execution_strategy=execution_strategy,
        envelope=envelope,
    )
    missing_required_refs = [ref for ref in required_refs if ref not in included_refs]
    available_tools = _available_tool_names(tool_schema_payload)
    required_tools = _required_tool_names(ai_request)
    allowed_tools = _allowed_tool_names(ai_request)
    required_tools_exempt: list[str] = []
    required_tools_exempt_reason = ""
    if required_tools:
        required_tools_exempt_reason = _required_tools_exempt_reason(ai_request, prepared)
        if required_tools_exempt_reason:
            # The call exposes no callable tools BY DESIGN: keep the stale claim
            # as audit evidence, but do not require tools this call cannot call.
            required_tools_exempt = required_tools
            required_tools = []
    missing_required_tools = [tool for tool in required_tools if tool not in available_tools]
    removed_allowed_tools = [tool for tool in allowed_tools if available_tools and tool not in available_tools]
    workflow_chain = _workflow_chain(
        ai_request=ai_request,
        request_metadata_summary=request_metadata_summary,
        envelope=envelope,
    )
    coverage_source_refs = _unique_strings([*required_refs, *included_refs])
    coverage_sources = build_final_request_coverage_sources(
        refs=coverage_source_refs,
        included_refs=included_refs,
        workflow_chain=workflow_chain,
        request_metadata_summary=request_metadata_summary,
    )
    structured_output_transport = getattr(prepared, "structured_output_transport", None)
    provider_protocol_coverage = _provider_protocol_schema_coverage(
        tool_schema_payload,
        tool_choice_payload=tool_choice_payload,
        plan=structured_output_transport,
    )
    exempt_tool_schemas = (
        (structured_output_transport.tool_definition,)
        if provider_protocol_coverage["active"] is True
        and provider_protocol_coverage["valid"] is True
        and structured_output_transport is not None
        else ()
    )
    total_required = len(required_refs) + len(required_tools)
    total_missing = len(missing_required_refs) + len(missing_required_tools)
    coverage_ratio = 1.0 if total_required == 0 else max(0.0, (total_required - total_missing) / total_required)
    return {
        "schema_version": "polaris.final_request_evidence_coverage.v1",
        "request_hash": _final_request_hash(
            ai_request=ai_request,
            prepared=prepared,
            messages=messages,
            tool_schema_payload=tool_schema_payload,
            response_format_payload=response_format_payload,
        ),
        "context_snapshot_ref": str(_request_context(ai_request).get("context_snapshot_ref") or ""),
        "role_id": role_id,
        "expected_role_id": expected_role_id,
        "role_identity_ok": role_identity_ok,
        "required_refs": required_refs,
        "included_refs": included_refs,
        "missing_required_refs": missing_required_refs,
        "coverage_sources": coverage_sources,
        "evidence_slots": build_final_request_evidence_slots(
            coverage_sources=coverage_sources,
            required_refs=required_refs,
            included_refs=included_refs,
            missing_required_refs=missing_required_refs,
        ),
        "required_tools": required_tools,
        "allowed_tools": allowed_tools,
        "available_tools": available_tools,
        "missing_required_tools": missing_required_tools,
        "tool_evidence_slots": build_final_request_tool_slots(
            required_tools=required_tools,
            available_tools=available_tools,
            missing_required_tools=missing_required_tools,
        ),
        "removed_allowed_tools": removed_allowed_tools,
        "tool_surface": {
            "required_tools": required_tools,
            "allowed_tools": allowed_tools,
            "offered_tools": available_tools,
            "missing_required_tools": missing_required_tools,
            "removed_allowed_tools": removed_allowed_tools,
            "required_tools_exempt": required_tools_exempt,
            "required_tools_exempt_reason": required_tools_exempt_reason,
            "required_tool_source": "explicit_required_tool_fields_only",
            "allowed_tool_source": "allowed_available_policy_contract_fields",
            "canonicalized": True,
        },
        "unexpected_tool_pruning": [
            {
                "tool": tool,
                "reason": "required_tool_missing_from_final_provider_request",
                "source": "final_request_evidence_coverage",
            }
            for tool in missing_required_tools
        ],
        "tool_schema_registry_coverage": _tool_schema_registry_coverage(
            tool_schema_payload,
            missing_required_tools=missing_required_tools,
            exempt_tool_schemas=exempt_tool_schemas,
        ),
        "provider_protocol_schema_coverage": provider_protocol_coverage,
        "structured_evidence": final_request_structured_evidence_from_metadata_summary(request_metadata_summary),
        "workflow_chain": workflow_chain,
        "ledger_evidence": _ledger_evidence(ai_request, receipt_refs=receipt_refs),
        "redaction_safety": _final_request_redaction_safety(messages),
        "coverage_ratio": round(coverage_ratio, 4),
        "pass": bool(role_identity_ok and not missing_required_refs and not missing_required_tools),
    }


def _add_evidence_coverage_findings(quality: dict[str, Any], evidence_coverage: dict[str, Any]) -> dict[str, Any]:
    findings = list(quality.get("findings") or [])
    evidence_pass = bool(evidence_coverage.get("pass"))
    if evidence_pass:
        findings = [
            item
            for item in findings
            if not (isinstance(item, dict) and item.get("code") in _REF_BASED_SUPERSEDED_FINDING_CODES)
        ]
    missing_refs = missing_required_refs_from_evidence_coverage(evidence_coverage)
    if missing_refs:
        findings.append(
            {
                "code": "missing_required_final_request_evidence",
                "severity": "warning",
                "missing_required_refs": list(missing_refs),
                "request_hash": evidence_coverage.get("request_hash", ""),
            }
        )
    missing_tools = missing_required_tools_from_evidence_coverage(evidence_coverage)
    if missing_tools:
        findings.append(
            {
                "code": "missing_required_final_request_tools",
                "severity": "error",
                "missing_required_tools": list(missing_tools),
                "request_hash": evidence_coverage.get("request_hash", ""),
            }
        )
    if evidence_coverage.get("role_identity_ok") is False:
        findings.append(
            {
                "code": "final_request_role_identity_mismatch",
                "severity": "error",
                "role_id": evidence_coverage.get("role_id", ""),
                "expected_role_id": evidence_coverage.get("expected_role_id", ""),
                "request_hash": evidence_coverage.get("request_hash", ""),
            }
        )
    return {
        **quality,
        "missing_coverage": [] if evidence_pass else list(quality.get("missing_coverage") or []),
        "context_needs_review": bool(findings),
        "findings": findings,
        "final_request_evidence_coverage_pass": evidence_pass,
        "missing_required_refs": list(missing_refs),
        "missing_required_tools": list(missing_tools),
    }


def _add_context_os_audit_findings(
    quality: dict[str, Any],
    context_os_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Make prompt-isolation failure first-class in final request quality."""

    projected = dict(quality)
    findings = list(projected.get("findings") or [])
    if context_os_audit.get("expected") is True and context_os_audit.get("ok") is not True:
        control_plane = context_os_audit.get("control_plane")
        control_payload = control_plane if isinstance(control_plane, Mapping) else {}
        requirements = context_os_audit.get("requirements")
        failed_requirements = (
            [
                str(name)
                for name, value in requirements.items()
                if isinstance(name, str) and name.strip() and value is False
            ]
            if isinstance(requirements, Mapping)
            else []
        )
        if not any(
            isinstance(item, Mapping) and item.get("code") == "context_os_prompt_audit_failed" for item in findings
        ):
            findings.append(
                {
                    "code": "context_os_prompt_audit_failed",
                    "severity": "error",
                    "control_plane_isolated": bool(control_payload.get("isolated")),
                    "metadata_key_hits": [
                        str(item) for item in (control_payload.get("metadata_key_hits") or ()) if str(item).strip()
                    ],
                    "content_hits": [
                        str(item) for item in (control_payload.get("content_hits") or ()) if str(item).strip()
                    ],
                    # R152: surface which ContextOS prompt requirement failed
                    # (e.g. current_user_final) — empty hits alone misled audits
                    # when isolation was true but final_role was system.
                    "failed_requirements": failed_requirements,
                    "final_role": str(context_os_audit.get("final_role") or ""),
                }
            )
    projected["findings"] = findings
    projected["context_needs_review"] = bool(findings)
    return projected


def _prepared_context_os_audit(
    *,
    prepared: PreparedLLMRequest,
    ai_request: Any,
) -> dict[str, Any]:
    raw_audit = getattr(prepared, "context_os_audit", None)
    if not isinstance(raw_audit, Mapping):
        request_context = getattr(ai_request, "context", None)
        if isinstance(request_context, Mapping):
            raw_audit = request_context.get("context_os_audit")
    return compact_context_os_audit(raw_audit) if isinstance(raw_audit, Mapping) else {}
