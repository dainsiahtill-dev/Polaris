"""Pure Chief Engineer evidence extraction and schema-repair decision helpers.

Extracted from ``OrchestrationStageExecutor`` as part of the incremental
god-class decomposition. Every function here is pure (no ``self``) and
operates on CE provider results, LLM events, and portfolio payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ChiefEngineerBehaviorExampleV1,
    ChiefEngineerBehaviorInvariantV1,
)


def ce_extract_llm_evidence(ce_result: Any, *, task_id: str, run_id: str) -> dict[str, Any]:
    def _walk_values(root: Any, keys: set[str]) -> Any:
        stack: list[Any] = [root]
        seen_ids: set[int] = set()
        while stack:
            item = stack.pop()
            item_id = id(item)
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            if isinstance(item, dict):
                for key, value in item.items():
                    normalized_key = str(key or "").strip().lower()
                    if normalized_key in keys and str(value or "").strip():
                        return value
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
        return None

    metadata = dict(getattr(ce_result, "metadata", {}) or {})
    usage = dict(getattr(ce_result, "usage", {}) or {})
    roots: list[Any] = [metadata, usage, ce_result]
    provider = ""
    model = ""
    cache_hit = False
    for root in roots:
        if not provider:
            provider = str(_walk_values(root, {"provider_id", "provider", "providerid"}) or "").strip()
        if not model:
            model = str(_walk_values(root, {"model", "model_id", "modelid"}) or "").strip()
        cache_value = _walk_values(root, {"cache_hit", "cached", "cachehit"})
        if cache_value is not None:
            cache_hit = bool(cache_value)
    if not provider:
        provider = "unknown"
    if not model:
        model = "unknown"

    evidence: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "cache_hit": cache_hit,
        "role": "chief_engineer",
        "task_id": task_id,
        "run_id": run_id,
    }
    if provider == "unknown" or model == "unknown":
        missing_parts: list[str] = []
        if provider == "unknown":
            missing_parts.append("provider_id/provider")
        if model == "unknown":
            missing_parts.append("model/model_id")
        evidence["provider_model_unknown"] = True
        evidence["provider_model_unknown_reason"] = (
            "Runtime result did not contain "
            + " and ".join(missing_parts)
            + "; check RoleExecutionKernel and RoleRuntimeService metadata propagation"
        )
    final_context_audit = _walk_values(roots, {"final_request_context_audit", "finalrequestcontextaudit"})
    if isinstance(final_context_audit, dict):
        evidence["final_request_context_audit"] = dict(final_context_audit)
    context_os_audit = _walk_values(roots, {"context_os_audit", "contextosaudit"})
    if isinstance(context_os_audit, dict):
        evidence["context_os_audit"] = dict(context_os_audit)
    context_snapshot_ref = str(_walk_values(roots, {"context_snapshot_ref", "contextsnapshotref"}) or "").strip()
    if context_snapshot_ref:
        from polaris.kernelone.events.final_request_evidence import normalize_context_snapshot_ref

        normalized_context_snapshot_ref = normalize_context_snapshot_ref(context_snapshot_ref)
        if normalized_context_snapshot_ref:
            evidence["context_snapshot_ref"] = normalized_context_snapshot_ref
    kernel_repair_reasons = _walk_values(roots, {"kernel_repair_reasons", "kernelrepairreasons"})
    if isinstance(kernel_repair_reasons, list):
        evidence["kernel_repair_reasons"] = [str(item) for item in kernel_repair_reasons]
    return evidence


def ce_prompt_profile_identity(ce_result: Any) -> dict[str, str]:
    """Recover the exact prompt-profile identity used by the primary CE call.

    A schema-repair objective necessarily contains words such as ``repair`` and
    often omits the product-facing wording that identified the original
    artifact.  Re-inferring profiles from that objective can therefore turn an
    ``implement/cli`` request into ``bugfix/library``.  The final provider
    request audit is the authority for what the primary call actually used, so
    bounded repair must inherit that identity instead of guessing again.
    """

    evidence = ce_extract_llm_evidence(ce_result, task_id="", run_id="")
    final_audit = evidence.get("final_request_context_audit")
    if not isinstance(final_audit, Mapping):
        return {}
    selection = final_audit.get("prompt_profile_selection")
    if not isinstance(selection, Mapping):
        return {}

    identity: dict[str, str] = {}
    for source_key, context_key in (
        ("inferred_language", "language"),
        ("inferred_task_type", "task_type"),
        ("inferred_stage", "prompt_stage"),
        ("inferred_artifact", "artifact"),
    ):
        value = str(selection.get(source_key) or "").strip()
        if value:
            identity[context_key] = value
    return identity


def ce_review_schema_failure_is_recoverable(ce_result: Any, *, raw_output: str) -> bool:
    if not raw_output.strip():
        return False
    if "<SESSION_PATCH" in raw_output or "</SESSION_PATCH>" in raw_output:
        return False
    failure_text = " ".join(
        str(value or "")
        for value in (
            getattr(ce_result, "error_code", None),
            getattr(ce_result, "error_message", None),
        )
    ).lower()
    return any(
        token in failure_text
        for token in (
            "验证失败",
            "validation_failed",
            "no json object matched chief_engineer blueprint keys",
            "json解析错误",
        )
    )


def ce_portfolio_result_allows_schema_repair(ce_result: Any) -> bool:
    """Whether one failed CE portfolio result may consume the single repair.

    This is deliberately narrower than a generic retryability predicate.
    Provider/routing/deadline failures remain fatal here.  Only an invalid
    portfolio payload, or a provider result that contains no visible
    portfolio payload at all, may use the already-governed schema repair.
    """

    error_code = str(getattr(ce_result, "error_code", None) or "").strip().lower()
    if error_code == "output_validation_failed":
        return True
    failure_text = " ".join(
        str(value or "")
        for value in (
            getattr(ce_result, "error_category", None),
            getattr(ce_result, "error_code", None),
            getattr(ce_result, "error_message", None),
            getattr(ce_result, "status", None),
        )
    ).lower()
    provider_result_protocol_failures = (
        "structured_output_payload_schema_mismatch",
        "structured_output_tool_arguments_invalid_json",
        "structured_output_tool_arguments_must_be_object",
        "structured_output_tool_must_be_called_exactly_once",
    )
    return (
        any(
            token in failure_text
            for token in (
                "thinking-only response",
                "thinking only response",
                "thinking_only_response",
                "empty response",
                "no visible output",
                "no visible output or tool calls",
            )
        )
        or any(token in failure_text for token in provider_result_protocol_failures)
        or ("model returned" in failure_text and "awaiting user clarification" in failure_text)
    )


def ce_schema_repair_failure_class(ce_result: Any) -> str:
    error_code = str(getattr(ce_result, "error_code", None) or "").strip().lower()
    error_message = str(getattr(ce_result, "error_message", None) or "").strip().lower()
    if error_code == "output_validation_failed" or any(
        token in error_message
        for token in (
            "structured_output_payload_schema_mismatch",
            "structured_output_tool_arguments_invalid_json",
            "structured_output_tool_arguments_must_be_object",
            "structured_output_tool_must_be_called_exactly_once",
        )
    ):
        return "output_validation_failed"
    return "thinking_only_response"


def chief_engineer_authoritative_pm_projection_candidate() -> dict[str, Any]:
    """Return the minimal CE advisory payload for deterministic PM projection.

    The candidate contains no project semantics of its own.  Exact tasks,
    targets, entrypoints, verifier commands, and ownership are projected later
    by the Chief Engineer owner from the already validated PM authority.  It
    is used only after the primary and one bounded repair both produced an
    unusable result-protocol payload; transport, auth, deadline, and evidence
    failures remain fail-closed before this helper is considered.
    """

    return {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {"invariants": []},
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [],
                "entrypoints": [],
                "verification": [],
            }
        },
        "risk_flags": [],
    }


def attach_ce_llm_evidence(signal: dict[str, Any], evidence: dict[str, Any]) -> None:
    for key in (
        "final_request_context_audit",
        "context_os_audit",
        "context_snapshot_ref",
        "kernel_repair_reasons",
        "provider_model_unknown",
        "provider_model_unknown_reason",
    ):
        if key in evidence:
            signal[key] = evidence[key]


def ce_missing_final_request_evidence(evidence: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not isinstance(evidence.get("final_request_context_audit"), dict):
        missing.append("final_request_context_audit")
    if not str(evidence.get("context_snapshot_ref") or "").strip():
        missing.append("context_snapshot_ref")
    return missing


def architecture_decision_payloads(values: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_values = values if isinstance(values, (list, tuple)) else []
    for item in source_values:
        if isinstance(item, dict):
            rows.append(dict(item))
            continue
        to_dict = getattr(item, "to_dict", None)
        if not callable(to_dict):
            continue
        try:
            payload = to_dict()
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def llm_event_error_text(event: dict[str, Any]) -> str:
    raw_value = event.get("raw")
    raw: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
    data_value = raw.get("data")
    data: dict[str, Any] = data_value if isinstance(data_value, dict) else {}
    metadata_value = raw.get("metadata")
    metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
    data_metadata_value = data.get("metadata")
    data_metadata: dict[str, Any] = data_metadata_value if isinstance(data_metadata_value, dict) else {}
    parts: list[str] = []
    for source in (event, raw, data, metadata, data_metadata):
        for key in (
            "event",
            "event_type",
            "error_category",
            "error_code",
            "error_message",
            "message",
            "status",
            "retry_decision",
        ):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts)


def chief_engineer_portfolio_output_errors(
    payload: Mapping[str, Any],
    *,
    task_ids: tuple[str, ...],
) -> list[str]:
    """Validate the nested project-level CE output contract."""

    errors: list[str] = []
    required_keys = {"construction_plan", "project_completion_contract", "risk_flags"}
    missing_keys = sorted(required_keys - set(payload))
    if missing_keys:
        errors.append("missing top-level keys: " + ", ".join(missing_keys))
    construction_plan = payload.get("construction_plan")
    if not isinstance(construction_plan, Mapping):
        errors.append("construction_plan must be an object")
        return errors
    task_plans = construction_plan.get("task_plans")
    if task_plans is not None and not isinstance(task_plans, Mapping):
        errors.append("construction_plan.task_plans must be an object")
    elif isinstance(task_plans, Mapping):
        declared_task_ids = {str(task_id).strip() for task_id in task_plans}
        unknown_task_ids = sorted(declared_task_ids - set(task_ids))
        if unknown_task_ids:
            errors.append("task_plans contains unknown task ids: " + ", ".join(unknown_task_ids))
        for task_id, task_plan in task_plans.items():
            if not isinstance(task_plan, Mapping):
                errors.append(f"task_plans[{task_id!r}] must be an object")
            elif not isinstance(task_plan.get("behavior_invariant_refs"), list):
                errors.append(f"task_plans[{task_id!r}].behavior_invariant_refs must be an array")
    interface_contract = construction_plan.get("project_interface_contract")
    if not isinstance(interface_contract, Mapping):
        errors.append("construction_plan.project_interface_contract must be an object")
    else:
        providers = interface_contract.get(
            "provider_declarations",
            interface_contract.get("providers", []),
        )
        consumers = interface_contract.get(
            "consumer_declarations",
            interface_contract.get("consumers", []),
        )
        if not isinstance(providers, list):
            errors.append("project_interface_contract.provider_declarations must be an array")
        if not isinstance(consumers, list):
            errors.append("project_interface_contract.consumer_declarations must be an array")
    behavior_contract = construction_plan.get("shared_behavior_contract")
    if not isinstance(behavior_contract, Mapping):
        errors.append("construction_plan.shared_behavior_contract must be an object")
    else:
        invariants = behavior_contract.get("invariants")
        if not isinstance(invariants, list):
            errors.append("shared_behavior_contract.invariants must be an array")
        else:
            for index, invariant in enumerate(invariants):
                if not isinstance(invariant, Mapping):
                    errors.append(f"shared_behavior_contract.invariants[{index}] must be an object")
                    continue
                raw_examples = invariant.get("verification_examples")
                if not isinstance(raw_examples, list):
                    errors.append(
                        f"shared_behavior_contract.invariants[{index}].verification_examples must be an array"
                    )
                    continue
                try:
                    examples = tuple(
                        ChiefEngineerBehaviorExampleV1(
                            given=str(example.get("given") or ""),
                            when=str(example.get("when") or ""),
                            then=str(example.get("then") or ""),
                        )
                        for example in raw_examples
                        if isinstance(example, Mapping)
                    )
                    ChiefEngineerBehaviorInvariantV1(
                        invariant_id=str(invariant.get("invariant_id") or ""),
                        statement=str(invariant.get("statement") or ""),
                        owner_task_id=str(invariant.get("owner_task_id") or ""),
                        consumer_task_ids=tuple(
                            str(item or "").strip()
                            for item in invariant.get("consumer_task_ids") or ()
                            if str(item or "").strip()
                        ),
                        covered_obligation_ids=tuple(
                            str(item or "").strip()
                            for item in invariant.get("covered_obligation_ids") or ()
                            if str(item or "").strip()
                        ),
                        verification_examples=examples,
                    )
                except (TypeError, ValueError) as exc:
                    errors.append(f"shared_behavior_contract.invariants[{index}] invalid: {exc}")
    if "scope_for_apply" in payload and not isinstance(payload.get("scope_for_apply"), list):
        errors.append("scope_for_apply must be an array")
    if not isinstance(payload.get("risk_flags"), list):
        errors.append("risk_flags must be an array")
    completion_contract = payload.get("project_completion_contract")
    if not isinstance(completion_contract, Mapping):
        errors.append("project_completion_contract must be an object")
    else:
        obligations = completion_contract.get("obligations")
        if not isinstance(obligations, Mapping):
            errors.append("project_completion_contract.obligations must be an object")
        else:
            for field in ("artifacts", "entrypoints", "verification"):
                if not isinstance(obligations.get(field), list):
                    errors.append(f"project_completion_contract.obligations.{field} must be an array")
    return errors
