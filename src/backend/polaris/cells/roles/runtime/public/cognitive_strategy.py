"""Cognitive-runtime and strategy-override helpers for `roles.runtime` cell.

Lossless split: this module holds the pure helpers that read cognitive-runtime
guidance/approval flags, build strategy overrides, and copy/merge override and
provider-policy payloads. The bodies were moved verbatim from
``public/service.py`` and are re-exported there to preserve the public surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.profile.public.service import RoleTurnRequest
from polaris.kernelone.context.runtime_feature_flags import resolve_context_os_enabled


def _metadata_flag_enabled(*payloads: Mapping[str, Any] | None, key: str) -> bool:
    for payload in payloads:
        if not isinstance(payload, Mapping) or key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        token = str(value or "").strip().lower()
        if token in {"1", "true", "yes", "on", "required"}:
            return True
        if token in {"0", "false", "no", "off", "optional", "disabled"}:
            return False
    return False


def _enforce_required_context_os(request: RoleTurnRequest) -> RoleTurnRequest:
    context_override = dict(request.context_override or {})
    metadata = dict(request.metadata or {})
    expected = _metadata_flag_enabled(
        context_override,
        metadata,
        key="context_os_expected",
    )
    if not expected:
        return request

    enabled = resolve_context_os_enabled(
        incoming_context=context_override,
        session_context_config=metadata,
        default=True,
    )
    if not enabled:
        raise RuntimeError("context_os_expected_but_disabled")

    metadata["context_os_preflight"] = {
        "expected": True,
        "enabled": True,
    }
    request.metadata = metadata
    return request


def _cognitive_runtime_result_patch(
    *,
    evidence: Mapping[str, Any] | None,
    request_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_payload = (
        dict(evidence)
        if isinstance(evidence, Mapping)
        else {"available": False, "error_code": "invalid_cognitive_runtime_evidence"}
    )
    patch: dict[str, Any] = {"cognitive_runtime_evidence": evidence_payload}
    preflight = request_metadata.get("cognitive_runtime_preflight")
    if isinstance(preflight, Mapping):
        patch["cognitive_runtime_preflight"] = dict(preflight)
    context_os_preflight = request_metadata.get("context_os_preflight")
    if isinstance(context_os_preflight, Mapping):
        patch["context_os_preflight"] = dict(context_os_preflight)
    return patch


def _copy_llm_provider_policy_into_context(
    *,
    context_override: dict[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose role-runtime provider policy metadata to the LLM executor path."""
    result = dict(context_override)
    for key in (
        "allowed_provider_types",
        "allow_provider_types",
        "blocked_provider_types",
        "provider_type_policy",
    ):
        value = metadata.get(key)
        if value is not None:
            result[key] = value
    policy = metadata.get("llm_provider_policy")
    if isinstance(policy, Mapping):
        result["llm_provider_policy"] = dict(policy)
    return result


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _copy_cognitive_guidance(cognitive_context: Mapping[str, Any]) -> dict[str, Any]:
    analysis = cognitive_context.get("cognitive_analysis")
    analysis_payload = dict(analysis) if isinstance(analysis, Mapping) else {}
    actions = analysis_payload.get("actions_taken")
    action_values = tuple(str(item) for item in actions[:8]) if isinstance(actions, (list, tuple)) else ()
    blocked_tools = _copy_string_tuple(cognitive_context.get("blocked_tools"), limit=24)
    return {
        "intent_type": str(cognitive_context.get("intent_type") or "unknown"),
        "confidence": _safe_float(cognitive_context.get("confidence")),
        "uncertainty_score": _safe_float(cognitive_context.get("uncertainty_score")),
        "execution_path": str(cognitive_context.get("execution_path") or "unknown"),
        "clarity_level": str(analysis_payload.get("clarity_level") or "unknown"),
        "verification_needed": bool(analysis_payload.get("verification_needed")),
        "actions_taken": action_values,
        "blocked_tools": blocked_tools,
    }


def _has_forced_transaction_tool_choice(context: Mapping[str, Any]) -> bool:
    forced_choice = context.get("_transaction_kernel_forced_tool_choice")
    if isinstance(forced_choice, Mapping):
        return bool(forced_choice)
    if isinstance(forced_choice, str):
        normalized_choice = forced_choice.strip().lower()
        if normalized_choice and normalized_choice not in {"auto", "none"}:
            return True
    elif forced_choice is not None:
        return True

    forced_definitions = context.get("_transaction_kernel_forced_tool_definitions")
    return isinstance(forced_definitions, (list, tuple)) and bool(forced_definitions)


def _apply_forced_transaction_tool_guidance(
    guidance: dict[str, Any],
    context: Mapping[str, Any],
) -> bool:
    if not _has_forced_transaction_tool_choice(context):
        return False

    guidance["forced_transaction_tool_choice_override"] = True
    guidance["original_intent_type"] = str(guidance.get("intent_type") or "unknown")
    guidance["original_execution_path"] = str(guidance.get("execution_path") or "unknown")
    guidance["original_verification_needed"] = bool(guidance.get("verification_needed"))
    guidance["intent_type"] = "code_generation"
    guidance["execution_path"] = "forced_transaction_tool_write"
    guidance["verification_needed"] = True
    return True


def _resolve_cognitive_runtime_blocker_approval(
    *,
    context: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, str] | None:
    approval_raw = metadata.get("cognitive_runtime_approval")
    if not isinstance(approval_raw, Mapping):
        approval_raw = context.get("cognitive_runtime_approval")
    if not isinstance(approval_raw, Mapping):
        return None

    mode = (
        str(
            approval_raw.get("mode")
            or metadata.get("cognitive_runtime_approval_mode")
            or context.get("cognitive_runtime_approval_mode")
            or ""
        )
        .strip()
        .lower()
    )
    if mode != "auto_accept":
        return None

    scope = str(
        approval_raw.get("scope")
        or metadata.get("cognitive_runtime_approval_scope")
        or context.get("cognitive_runtime_approval_scope")
        or ""
    ).strip()
    if not scope:
        return None

    return {
        "mode": mode,
        "source": str(approval_raw.get("source") or "unknown").strip() or "unknown",
        "scope": scope,
        "approved_by": str(approval_raw.get("approved_by") or "unknown").strip() or "unknown",
    }


def _copy_string_tuple(raw_value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(raw_value, (list, tuple, set, frozenset)):
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        values.append(token)
        if len(values) >= limit:
            break
    return tuple(values)


def _deep_merge_strategy_overrides(
    base: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = dict(base or {})
    if not isinstance(override, Mapping):
        return result
    for key, value in override.items():
        key_token = str(key or "").strip()
        if not key_token:
            continue
        existing = result.get(key_token)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            result[key_token] = _deep_merge_strategy_overrides(existing, value)
        elif isinstance(value, Mapping):
            result[key_token] = dict(value)
        elif isinstance(value, (list, tuple, set, frozenset)):
            result[key_token] = tuple(value)
        else:
            result[key_token] = value
    return result


def _copy_strategy_override(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return _deep_merge_strategy_overrides({}, value)


def _build_cognitive_strategy_override(guidance: Mapping[str, Any]) -> dict[str, Any]:
    execution_path = str(guidance.get("execution_path") or "").strip().lower()
    intent_type = str(guidance.get("intent_type") or "").strip().lower()
    uncertainty = _safe_float(guidance.get("uncertainty_score"))
    verification_needed = bool(guidance.get("verification_needed"))
    requires_deeper_context = (
        verification_needed
        or uncertainty >= 0.45
        or any(
            marker in execution_path
            for marker in (
                "full",
                "verify",
                "write",
                "plan",
                "refactor",
                "architect",
            )
        )
        or intent_type in {"code_generation", "architecture", "debugging", "root_cause"}
    )
    if not requires_deeper_context:
        return {}

    depth = 5 if uncertainty >= 0.65 else 4
    read_threshold_kb = 500 if uncertainty >= 0.65 else 350
    return {
        "exploration": {
            "map_first": True,
            "search_before_read": True,
            "max_expansion_depth": depth,
            "neighbor_expansion_aggressive": verification_needed or uncertainty >= 0.45,
        },
        "read_escalation": {
            "full_read_allowed": True,
            "full_read_threshold_kb": read_threshold_kb,
            "range_first_default": True,
            "range_first_threshold_kb": 20,
        },
        "compaction": {
            "trigger_at_budget_pct": 0.90,
            "receipt_micro_compact": True,
            "receipt_compact_threshold": 5,
        },
        "cognitive_runtime": {
            "source": "cognitive_runtime_mainline",
            "applied": True,
            "execution_path": execution_path or "unknown",
            "intent_type": intent_type or "unknown",
            "verification_needed": verification_needed,
            "uncertainty_score": round(uncertainty, 3),
        },
    }
