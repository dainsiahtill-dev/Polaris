"""Execution envelope builder for Director tasking.

This module binds the existing task execution profile/strategy/contract with
PM, CE, handoff, capability, model, budget, and audit evidence. It owns no tool
execution and performs no file I/O; downstream guards can consume the resulting
hashable payload incrementally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.director.tasking.public.contracts import (
    ExecutionEnvelopeV1,
    TaskExecutionContractV1,
    TaskExecutionProfileV1,
    TaskExecutionStrategyV1,
)
from polaris.kernelone.quality.scope_authority import (
    normalize_declared_scope_path,
    path_matches_any_declared_scope_candidate,
)

_MISSING_PREFIX = "missing:"
_DEFAULT_POLICY_VERSION = "director.execution_envelope.v1"
_EXECUTION_ENVELOPE_KEYS = ("execution_envelope", "task_execution_envelope", "director_execution_envelope")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            rows.append(token)
    return rows


def _string_value(metadata: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return default


def _hash_ref(*, ref: str, hash_value: str) -> dict[str, Any]:
    return {
        "ref": ref,
        "hash": hash_value or f"{_MISSING_PREFIX}hash",
    }


def _stable_hash_or_missing(value: Any, missing_key: str) -> str:
    if isinstance(value, Mapping) and value:
        return stable_hash(dict(value))
    return f"{_MISSING_PREFIX}{missing_key}"


def _metadata_hash(metadata: Mapping[str, Any], *keys: str, fallback: Any = None, missing_key: str) -> str:
    for key in keys:
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return _stable_hash_or_missing(fallback, missing_key)


def _metadata_has_value(metadata: Mapping[str, Any], *keys: str) -> bool:
    return any(str(metadata.get(key) or "").strip() for key in keys)


def _job_token(metadata: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("job_token", "control_plane_job_token", "capability_token"):
        token = _mapping(metadata.get(key))
        if token:
            return token
    return {}


def _allowed_paths(
    *,
    metadata: Mapping[str, Any],
    profile: TaskExecutionProfileV1,
    token: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    target_files = (
        _string_list(metadata.get("target_files"))
        or _string_list(token.get("target_files"))
        or list(profile.target_files)
    )
    scope_paths = (
        _string_list(metadata.get("scope_paths")) or _string_list(token.get("scope_paths")) or list(profile.scope_paths)
    )
    context_files = _string_list(metadata.get("context_files")) or _string_list(token.get("context_files"))
    if "allowed_write_paths" in metadata:
        allowed_write_paths = _string_list(metadata["allowed_write_paths"])
    elif "allowed_write_paths" in token:
        allowed_write_paths = _string_list(token["allowed_write_paths"])
    else:
        allowed_write_paths = list(target_files)
    legacy_read_paths = _string_list(token.get("allowed_paths"))
    allowed_read_paths = (
        _string_list(metadata.get("allowed_read_paths"))
        or _string_list(token.get("allowed_read_paths"))
        or list(dict.fromkeys([*allowed_write_paths, *scope_paths, *context_files, *legacy_read_paths]))
    )
    return allowed_read_paths, allowed_write_paths


def _allowed_commands(*, metadata: Mapping[str, Any], token: Mapping[str, Any]) -> list[str]:
    return _string_list(metadata.get("allowed_commands")) or _string_list(token.get("allowed_commands"))


def collect_admitted_execution_envelopes(*sources: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Capture existing admission constraints before aliases/metadata are merged."""
    envelopes: list[Mapping[str, Any]] = []
    for source in sources:
        for key in _EXECUTION_ENVELOPE_KEYS:
            if key not in source:
                continue
            envelope = source[key]
            if not isinstance(envelope, Mapping) or (key == "execution_envelope" and "authorization" not in envelope):
                raise ValueError("director_execution_envelope_admission_invalid")
            envelopes.append(envelope)
    return tuple(envelopes)


def _admitted_scope_list(value: Any, *, paths: bool) -> list[str]:
    if not isinstance(value, (list, tuple)) or any(type(item) is not str for item in value):
        raise ValueError("director_execution_envelope_admission_invalid")
    rows: list[str] = []
    for item in value:
        token = item.strip().replace("\\", "/") if paths else item.strip()
        if paths:
            if token.startswith(("/", "~")) or ":" in token or ".." in token.split("/"):
                raise ValueError("director_execution_envelope_admission_invalid")
            token = normalize_declared_scope_path(token)
        if not token:
            raise ValueError("director_execution_envelope_admission_invalid")
        if token not in rows:
            rows.append(token)
    return rows


def _intersect_admitted_paths(current: list[str], admitted: list[str]) -> list[str]:
    if not current or not admitted:
        return []
    # Exact glob equality is provable; arbitrary glob-language containment is
    # not. Literal candidates use the shared KernelOne scope matcher. Never
    # construct a new wildcard or broaden a scope to keep an inventory target.
    return [
        candidate
        for candidate in dict.fromkeys([*current, *admitted])
        if (
            candidate in current and candidate in admitted
            if any(char in candidate for char in "*?[")
            else path_matches_any_declared_scope_candidate(candidate, current)
            and path_matches_any_declared_scope_candidate(candidate, admitted)
        )
    ]


def _constrain_admitted_authorization(
    authorization: dict[str, Any], envelopes: tuple[Mapping[str, Any], ...]
) -> dict[str, Any]:
    constrained = dict(authorization)
    required: list[str] = []
    for envelope in envelopes:
        # Budget-only compatibility hints do not carry an authorization grant.
        if "authorization" not in envelope:
            continue
        admitted = envelope["authorization"]
        if not isinstance(admitted, Mapping) or "allowed_write_paths" not in admitted:
            raise ValueError("director_execution_envelope_admission_invalid")
        for key in ("allowed_write_paths", "allowed_read_paths", "allowed_commands"):
            if key not in admitted:
                continue
            paths = key != "allowed_commands"
            ceiling = _admitted_scope_list(admitted[key], paths=paths)
            current = _string_list(constrained.get(key))
            constrained[key] = (
                _intersect_admitted_paths(current, ceiling)
                if paths
                else [command for command in current if command in ceiling]
            )
        if "required_write_targets" in admitted:
            required.extend(_admitted_scope_list(admitted["required_write_targets"], paths=True))
    if required:
        writes = _string_list(constrained.get("allowed_write_paths"))
        if any(not path_matches_any_declared_scope_candidate(path, writes) for path in required):
            raise ValueError("director_execution_envelope_admission_conflict")
        constrained["required_write_targets"] = list(dict.fromkeys(required))
    return constrained


def build_execution_envelope(
    *,
    workspace: str,
    task_id: str,
    run_id: str,
    trace_id: str,
    profile: TaskExecutionProfileV1,
    strategy: TaskExecutionStrategyV1,
    contract: TaskExecutionContractV1,
    metadata: Mapping[str, Any] | None = None,
    created_at: str | None = None,
    policy_version: str = _DEFAULT_POLICY_VERSION,
    admitted_envelopes: tuple[Mapping[str, Any], ...] = (),
) -> ExecutionEnvelopeV1:
    """Build a hashable `polaris.execution_envelope.v1` payload."""

    normalized_metadata = _mapping(metadata)
    token = _job_token(normalized_metadata)
    created = created_at or _utc_now()
    pm_contract_payload = _mapping(normalized_metadata.get("pm_contract")) or _mapping(normalized_metadata.get("task"))
    ce_blueprint_payload = _mapping(normalized_metadata.get("ce_blueprint")) or {
        key: normalized_metadata.get(key)
        for key in ("blueprint_id", "blueprint_path", "blueprint_hash", "target_files", "scope_paths")
        if normalized_metadata.get(key) is not None
    }
    handoff_payload = _mapping(normalized_metadata.get("ce_handoff_decision")) or _mapping(
        normalized_metadata.get("handoff_decision")
    )
    handoff_bindings = _mapping(handoff_payload.get("bindings"))
    execution_profile_hash = _metadata_hash(
        normalized_metadata,
        "execution_profile_hash",
        "task_execution_profile_hash",
        "director_execution_profile_hash",
        fallback=profile.to_dict(),
        missing_key="execution_profile_hash",
    )
    if not _metadata_has_value(
        normalized_metadata,
        "execution_profile_hash",
        "task_execution_profile_hash",
        "director_execution_profile_hash",
    ) and handoff_bindings.get("execution_profile_hash"):
        execution_profile_hash = str(handoff_bindings["execution_profile_hash"])
    pm_contract_hash = _metadata_hash(
        normalized_metadata,
        "pm_contract_hash",
        "contract_hash",
        fallback=pm_contract_payload,
        missing_key="pm_contract_hash",
    )
    if not _metadata_has_value(normalized_metadata, "pm_contract_hash", "contract_hash") and handoff_bindings.get(
        "pm_contract_hash"
    ):
        pm_contract_hash = str(handoff_bindings["pm_contract_hash"])
    ce_blueprint_hash = _metadata_hash(
        normalized_metadata,
        "blueprint_hash",
        "ce_blueprint_hash",
        fallback=ce_blueprint_payload,
        missing_key="blueprint_hash",
    )
    if not _metadata_has_value(normalized_metadata, "blueprint_hash", "ce_blueprint_hash") and handoff_bindings.get(
        "blueprint_hash"
    ):
        ce_blueprint_hash = str(handoff_bindings["blueprint_hash"])
    handoff_hash = _metadata_hash(
        normalized_metadata,
        "handoff_decision_hash",
        "ce_handoff_decision_hash",
        fallback=handoff_payload,
        missing_key="handoff_decision_hash",
    )
    if not _metadata_has_value(
        normalized_metadata, "handoff_decision_hash", "ce_handoff_decision_hash"
    ) and handoff_payload.get("decision_hash"):
        handoff_hash = str(handoff_payload["decision_hash"])
    allowed_read_paths, allowed_write_paths = _allowed_paths(
        metadata=normalized_metadata,
        profile=profile,
        token=token,
    )
    target_files = _string_list(normalized_metadata.get("target_files")) or list(profile.target_files)
    scope_paths = _string_list(normalized_metadata.get("scope_paths")) or list(profile.scope_paths)
    authorization = {
        "allowed_read_paths": allowed_read_paths,
        "allowed_write_paths": allowed_write_paths,
        "allowed_commands": _allowed_commands(metadata=normalized_metadata, token=token),
        "target_files": target_files,
        "scope_paths": scope_paths,
        "capability_token_ref": str(token.get("token_id") or "").strip(),
        "capability_token_hash": stable_hash(token) if token else f"{_MISSING_PREFIX}capability_token",
    }
    authorization = _constrain_admitted_authorization(
        authorization,
        (*admitted_envelopes, *collect_admitted_execution_envelopes(normalized_metadata)),
    )
    model_policy = {
        "provider": _string_value(normalized_metadata, "provider", "provider_id"),
        "model": _string_value(normalized_metadata, "model", "model_id", default="missing:model"),
        "temperature": strategy.temperature,
        "top_p": normalized_metadata.get("top_p"),
        "max_tokens": strategy.output_budget_tokens,
        "response_format": _mapping(normalized_metadata.get("response_format")),
        "tool_choice": _string_value(normalized_metadata, "tool_choice", default="auto"),
        "tool_schema_hash": _string_value(normalized_metadata, "tool_schema_hash"),
    }
    budget_policy = {
        "input_budget_tokens": strategy.input_budget_tokens,
        "output_budget_tokens": strategy.output_budget_tokens,
        "tool_call_budget": int(normalized_metadata.get("tool_call_budget") or 0),
        "command_timeout_seconds": int(normalized_metadata.get("command_timeout_seconds") or 0),
        "repair_attempt_budget": int(normalized_metadata.get("repair_attempt_budget") or 0),
    }
    missing_authority_bindings = [
        key
        for key, value in (
            ("pm_contract_hash", pm_contract_hash),
            ("blueprint_hash", ce_blueprint_hash),
            ("handoff_decision_hash", handoff_hash),
            ("execution_profile_hash", execution_profile_hash),
        )
        if str(value or "").startswith(_MISSING_PREFIX)
    ]
    audit_policy = {
        "required_evidence": list(strategy.evidence_requirements),
        "context_snapshot_ref": _string_value(normalized_metadata, "context_snapshot_ref"),
        "final_provider_request_required": True,
        "receipt_required": True,
        "provenance_bundle_required": True,
        "execution_authority": {
            "ok": not missing_authority_bindings,
            "missing_bindings": missing_authority_bindings,
            "strict_handoff_required": True,
        },
    }
    validity = {
        "created_at": created,
        "expires_at": _string_value(normalized_metadata, "execution_envelope_expires_at"),
        "policy_version": policy_version,
    }
    payload_without_hash: dict[str, Any] = {
        "schema_version": "polaris.execution_envelope.v1",
        "run_id": run_id,
        "task_id": task_id,
        "workspace": workspace,
        "trace_id": trace_id,
        "pm_contract": _hash_ref(
            ref=_string_value(
                normalized_metadata, "pm_contract_ref", default=str(handoff_bindings.get("pm_contract_ref") or "")
            ),
            hash_value=pm_contract_hash,
        ),
        "ce_blueprint": _hash_ref(
            ref=_string_value(
                normalized_metadata,
                "blueprint_path",
                "ce_blueprint_ref",
                default=str(handoff_bindings.get("blueprint_ref") or ""),
            ),
            hash_value=ce_blueprint_hash,
        ),
        "handoff_decision": {
            "ref": _string_value(normalized_metadata, "handoff_decision_ref", "ce_handoff_decision_ref"),
            "hash": handoff_hash,
            "allowed": bool(handoff_payload.get("allowed")),
        },
        "execution_profile": _hash_ref(
            ref=_string_value(
                normalized_metadata,
                "execution_profile_ref",
                "task_execution_profile_ref",
                default=str(handoff_bindings.get("execution_profile_ref") or ""),
            ),
            hash_value=execution_profile_hash,
        ),
        "authorization": authorization,
        "model_policy": model_policy,
        "budget_policy": budget_policy,
        "audit_policy": audit_policy,
        "validity": validity,
        "created_at": created,
        "task_execution_contract_hash": stable_hash(contract.to_dict()),
    }
    envelope_hash = stable_hash(payload_without_hash)
    return ExecutionEnvelopeV1(
        envelope_id=f"exec-env-{envelope_hash[:24]}",
        run_id=run_id,
        task_id=task_id,
        workspace=workspace,
        trace_id=trace_id,
        pm_contract=payload_without_hash["pm_contract"],
        ce_blueprint=payload_without_hash["ce_blueprint"],
        handoff_decision=payload_without_hash["handoff_decision"],
        execution_profile=payload_without_hash["execution_profile"],
        authorization=authorization,
        model_policy=model_policy,
        budget_policy=budget_policy,
        audit_policy=audit_policy,
        validity=validity,
        envelope_hash=envelope_hash,
        created_at=created,
    )


__all__ = ["build_execution_envelope"]
