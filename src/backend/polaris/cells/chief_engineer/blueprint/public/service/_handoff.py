"""CE handoff decision and Director handoff validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import stable_hash

from ...internal.blueprint_persistence import BlueprintPersistence
from ...internal.handoff import build_handoff_decision
from ..contracts import (
    CeHandoffDecisionBindingsV1,
    CeHandoffDecisionV1,
    ChiefEngineerBlueprintErrorV1,
    HandoffDecisionV1,
    ProjectCompletionContractV1,
)
from ._helpers import (
    _blueprint_path,
    _utc_now,
)


def evaluate_handoff_decision(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
) -> HandoffDecisionV1:
    """Decide whether a blueprint may be handed to the Director.

    The enforcement primitive that closes the quality-gate loop. A handoff
    is blocked when the deterministic quality gate has blockers OR when the
    workspace Risk Register has open critical/blocker risks for the task.

    Args:
        workspace: Root workspace path.
        blueprint: Blueprint payload (must carry the construction contract
            fields target_files / acceptance_criteria / ...).
        blueprint_id: Owning blueprint id (falls back to ``blueprint``).
        task_id: Owning PM task id (falls back to ``blueprint``).

    Returns:
        A :class:`HandoffDecisionV1`. Fail-closed: a malformed blueprint
        evaluates to ``allowed=False``.
    """
    return build_handoff_decision(
        workspace,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )


def evaluate_handoff_decision_for_blueprint(workspace: str, blueprint_id: str) -> HandoffDecisionV1 | None:
    """Load a persisted blueprint and decide whether it may be handed off.

    Returns ``None`` (fail-closed: caller treats as "not ready") when the
    blueprint is missing or unreadable.
    """
    payload = BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    if not isinstance(payload, dict):
        return None
    return evaluate_handoff_decision(workspace, blueprint=payload, blueprint_id=blueprint_id)


_CE_HANDOFF_POLICY_VERSION = "chief_engineer.handoff.v1"

_MISSING_HASH_PREFIX = "missing:"


def _binding_hash_or_missing(payload: dict[str, Any], *keys: str, fallback: Any = None) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    if fallback:
        return stable_hash(fallback)
    return f"{_MISSING_HASH_PREFIX}{keys[0]}"


def _execution_profile_hash_from_blueprint(blueprint: dict[str, Any]) -> str:
    explicit = str(
        blueprint.get("execution_profile_hash")
        or blueprint.get("task_execution_profile_hash")
        or blueprint.get("director_execution_profile_hash")
        or ""
    ).strip()
    if explicit:
        return explicit
    for key in ("execution_profile", "task_execution_profile", "director_execution_profile"):
        candidate = blueprint.get(key)
        if isinstance(candidate, dict) and candidate:
            return stable_hash(candidate)
    return "missing:execution_profile_hash"


def build_ce_handoff_decision(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
    base_decision: HandoffDecisionV1 | None = None,
) -> CeHandoffDecisionV1:
    """Build the strict `ce_handoff_decision.v1` object.

    This complements the base `HandoffDecisionV1` without changing existing
    callers. The strict decision fails closed when required hash bindings are
    missing, making it suitable for the future execution envelope.
    """

    base_handoff_decision = base_decision or evaluate_handoff_decision(
        workspace,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )
    resolved_blueprint_id = str(
        blueprint_id or base_handoff_decision.blueprint_id or blueprint.get("blueprint_id") or ""
    ).strip()
    resolved_task_id = str(task_id or base_handoff_decision.task_id or blueprint.get("task_id") or "").strip()
    bindings = CeHandoffDecisionBindingsV1(
        pm_contract_ref=str(blueprint.get("pm_contract_ref") or blueprint.get("pm_contract_path") or "").strip(),
        pm_contract_hash=_binding_hash_or_missing(
            blueprint,
            "pm_contract_hash",
            "contract_hash",
            fallback=blueprint.get("pm_contract"),
        ),
        blueprint_ref=str(blueprint.get("blueprint_ref") or _blueprint_path(resolved_blueprint_id)).strip(),
        blueprint_hash=_binding_hash_or_missing(
            blueprint,
            "blueprint_hash",
            fallback=blueprint,
        ),
        execution_profile_ref=str(
            blueprint.get("execution_profile_ref")
            or blueprint.get("task_execution_profile_ref")
            or blueprint.get("director_execution_profile_ref")
            or ""
        ).strip(),
        execution_profile_hash=_execution_profile_hash_from_blueprint(blueprint),
    )
    binding_values = bindings.to_dict()
    missing_bindings = [
        key
        for key in ("pm_contract_hash", "blueprint_hash", "execution_profile_hash")
        if str(binding_values.get(key) or "").startswith(_MISSING_HASH_PREFIX)
    ]
    blockers = [*base_handoff_decision.blockers]
    blockers.extend(f"missing required handoff binding: {key}" for key in missing_bindings)
    allowed = bool(base_handoff_decision.allowed and not missing_bindings)
    risk_assessment: dict[str, Any] = {
        "blocking_risks": (
            list(base_handoff_decision.blockers) if base_handoff_decision.open_blocker_risk_count else []
        ),
        "non_blocking_warnings": [],
    }
    evidence_refs = [
        str(ref)
        for ref in (binding_values.get("pm_contract_ref"), binding_values.get("blueprint_ref"))
        if str(ref or "").strip()
    ]
    payload_without_hash: dict[str, Any] = {
        "schema_version": "polaris.ce_handoff_decision.v1",
        "task_id": resolved_task_id,
        "blueprint_id": resolved_blueprint_id,
        "allowed": allowed,
        "reason": base_handoff_decision.reason,
        "blockers": blockers,
        "warnings": [],
        "risk_assessment": risk_assessment,
        "evaluated_at": base_handoff_decision.evaluated_at or _utc_now(),
        "evaluator": "chief_engineer.blueprint.handoff",
        "policy_version": _CE_HANDOFF_POLICY_VERSION,
        "bindings": binding_values,
        "evidence_refs": evidence_refs,
    }
    decision_hash = stable_hash(payload_without_hash)
    return CeHandoffDecisionV1(
        decision_id=f"ce-handoff-{decision_hash[:24]}",
        task_id=resolved_task_id,
        blueprint_id=resolved_blueprint_id,
        allowed=allowed,
        reason=str(base_handoff_decision.reason or ""),
        blockers=tuple(blockers),
        warnings=(),
        risk_assessment=risk_assessment,
        evaluated_at=str(payload_without_hash["evaluated_at"]),
        evaluator=str(payload_without_hash["evaluator"]),
        policy_version=_CE_HANDOFF_POLICY_VERSION,
        bindings=bindings,
        evidence_refs=tuple(evidence_refs),
        decision_hash=decision_hash,
    )


def evaluate_ce_handoff_decision_for_blueprint(
    workspace: str,
    blueprint_id: str,
) -> CeHandoffDecisionV1 | None:
    """Load a persisted blueprint and build strict `ce_handoff_decision.v1`."""

    payload = BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    if not isinstance(payload, dict):
        return None
    return build_ce_handoff_decision(workspace, blueprint=payload, blueprint_id=blueprint_id)


def _merged_payload_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    merged = dict(payload)
    merged.update(metadata)
    return merged


def _blueprint_id_from_payload(payload: dict[str, Any]) -> str:
    merged = _merged_payload_metadata(payload)
    for key in ("blueprint_id", "chief_engineer_blueprint_id", "chief_engineer_handoff_id"):
        token = str(merged.get(key) or "").strip()
        if token:
            return Path(token).stem if token.endswith(".json") else token
    for key in ("blueprint_path", "runtime_blueprint_path"):
        token = str(merged.get(key) or "").strip()
        if token:
            return Path(token).stem
    return ""


def _task_id_from_payload(payload: dict[str, Any]) -> str:
    merged = _merged_payload_metadata(payload)
    for key in ("task_id", "pm_task_id", "source_task_id", "external_task_id", "id"):
        token = str(merged.get(key) or "").strip()
        if token:
            return token
    return ""


def _handoff_validation_result(
    *,
    allowed: bool,
    reason: str,
    task_id: str = "",
    blueprint_id: str = "",
    blueprint_task_id: str = "",
    base_handoff_decision: HandoffDecisionV1 | None = None,
    strict_decision: CeHandoffDecisionV1 | None = None,
    task_completion_projection: Mapping[str, Any] | None = None,
    job_token: Mapping[str, Any] | None = None,
    require_strict: bool = False,
) -> dict[str, Any]:
    base_payload = base_handoff_decision.to_dict() if base_handoff_decision is not None else {}
    strict_payload = strict_decision.to_dict() if strict_decision is not None else {}
    return {
        "schema_version": "chief_engineer.director_handoff_validation.v1",
        "allowed": allowed,
        "reason": str(reason or "").strip(),
        "task_id": task_id,
        "blueprint_id": blueprint_id,
        "blueprint_task_id": blueprint_task_id,
        "base_allowed": bool(base_handoff_decision.allowed) if base_handoff_decision is not None else False,
        "strict_allowed": bool(strict_decision.allowed) if strict_decision is not None else False,
        "require_strict": require_strict,
        "decision_payload": base_payload,
        "strict_decision_payload": strict_payload,
        "task_completion_projection": (
            dict(task_completion_projection) if isinstance(task_completion_projection, Mapping) else {}
        ),
        # The validated CE capability must travel with the task-local
        # completion projection.  Director tool receipts commit this token to
        # the factory Run Ledger before ProjectArtifactReceipt authority can
        # be resolved; omitting it creates physical files under a token-less
        # Director run and leaves the project completion ledger empty.
        "job_token": dict(job_token) if isinstance(job_token, Mapping) else {},
        "capability_token": dict(job_token) if isinstance(job_token, Mapping) else {},
    }


def _project_task_completion_contract(
    blueprint: Mapping[str, Any],
    *,
    task_id: str,
) -> dict[str, Any]:
    """Return one bounded Director repair contract derived from CE authority.

    Director does not need the entire project completion contract on every
    retry.  It needs only obligations owned by the claimed task plus artifact
    obligations referenced by those local verifiers.  The immutable project
    contract hash remains the root authority, while ``projection_hash`` binds
    the exact task-local slice injected into Director context.
    """

    raw_contract = blueprint.get("project_completion_contract")
    if not isinstance(raw_contract, Mapping):
        raise ValueError("project completion contract is missing from Chief Engineer blueprint")
    contract = ProjectCompletionContractV1.from_dict(raw_contract)
    if task_id not in contract.covered_task_ids:
        raise ValueError("project completion contract does not cover Director task")

    contract_hash = str(blueprint.get("project_completion_contract_hash") or "").strip()
    contract_ref = str(blueprint.get("project_completion_contract_ref") or "").strip()
    if contract_hash != contract.contract_hash or not contract_ref:
        raise ValueError("Chief Engineer blueprint completion contract binding is invalid")

    owned_artifacts = tuple(item for item in contract.obligations.artifacts if item.owner_task_id == task_id)
    owned_entrypoints = tuple(item for item in contract.obligations.entrypoints if item.owner_task_id == task_id)
    owned_verification = tuple(item for item in contract.obligations.verification if item.owner_task_id == task_id)
    covered_ids = {obligation_id for verifier in owned_verification for obligation_id in verifier.covers_obligation_ids}
    owned_artifact_ids = {item.obligation_id for item in owned_artifacts}
    dependency_artifacts = tuple(
        item
        for item in contract.obligations.artifacts
        if item.obligation_id in covered_ids and item.obligation_id not in owned_artifact_ids
    )
    command_authority = tuple(item for item in contract.verification_command_authority if item.task_id == task_id)
    authority_by_hash = {item.authority_hash: item for item in command_authority}
    verification_execution_authority: list[dict[str, Any]] = []
    for verifier in owned_verification:
        if verifier.applicability == "not_applicable":
            continue
        authority = authority_by_hash.get(str(verifier.command_authority_hash or ""))
        if authority is None:
            raise ValueError(f"task-local verifier {verifier.obligation_id!r} lacks exact command authority")
        verification_execution_authority.append(
            {
                "obligation_id": verifier.obligation_id,
                "owner_task_id": verifier.owner_task_id,
                "modality": verifier.modality,
                "command": verifier.command,
                "argv": list(authority.argv),
                "cwd": authority.cwd,
                "command_authority_hash": authority.authority_hash,
                "covers_obligation_ids": list(verifier.covers_obligation_ids),
            }
        )

    seed = {
        "schema_version": "polaris.task_completion_projection.v1",
        "project_contract_id": contract.contract_id,
        "project_contract_ref": contract_ref,
        "project_contract_hash": contract.contract_hash,
        "project_id": contract.project_id,
        "run_id": contract.run_id,
        "task_id": task_id,
        "project_kind": contract.project_kind,
        "completion_predicate_version": contract.completion_predicate_version,
        "verifier_policy_hash": contract.verifier_policy_hash,
        "verifier_policy_snapshot_hash": contract.verifier_policy_snapshot_hash,
        "owned_artifacts": [item.to_dict() for item in owned_artifacts],
        "dependency_artifacts": [item.to_dict() for item in dependency_artifacts],
        "owned_entrypoints": [item.to_dict() for item in owned_entrypoints],
        "owned_verification": [item.to_dict() for item in owned_verification],
        "verification_command_authority": [item.to_dict() for item in command_authority],
        "verification_execution_authority": verification_execution_authority,
    }
    return {**seed, "projection_hash": stable_hash(seed)}


def _project_validated_job_token(
    blueprint: Mapping[str, Any],
    *,
    task_completion_projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the exact CE JobToken after binding it to blueprint authority."""

    raw_job_token = blueprint.get("job_token")
    raw_capability_token = blueprint.get("capability_token")
    if not isinstance(raw_job_token, Mapping) or not isinstance(raw_capability_token, Mapping):
        raise ValueError("Chief Engineer blueprint JobToken authority is missing")
    job_token = dict(raw_job_token)
    if job_token != dict(raw_capability_token):
        raise ValueError("Chief Engineer blueprint capability token differs from JobToken")

    audit = job_token.get("capability_audit")
    if not isinstance(audit, Mapping) or audit.get("ok") is not True or list(audit.get("issues") or []):
        raise ValueError("Chief Engineer blueprint JobToken capability audit is not clean")

    required = {
        "token_id": str(job_token.get("token_id") or "").strip(),
        "run_id": str(job_token.get("run_id") or "").strip(),
        "factory_run_id": str(job_token.get("factory_run_id") or "").strip(),
        "project_id": str(job_token.get("project_id") or "").strip(),
        "contract_hash": str(job_token.get("contract_hash") or "").strip(),
        "blueprint_hash": str(job_token.get("blueprint_hash") or "").strip(),
    }
    if any(not value for value in required.values()):
        raise ValueError("Chief Engineer blueprint JobToken identity is incomplete")
    if required["run_id"] != str(task_completion_projection.get("run_id") or "").strip():
        raise ValueError("Chief Engineer blueprint JobToken run differs from completion projection")
    if required["factory_run_id"] != required["run_id"]:
        raise ValueError("Chief Engineer blueprint JobToken factory run identity is inconsistent")
    if required["project_id"] != str(task_completion_projection.get("project_id") or "").strip():
        raise ValueError("Chief Engineer blueprint JobToken project differs from completion projection")
    if required["contract_hash"] != str(blueprint.get("contract_hash") or "").strip():
        raise ValueError("Chief Engineer blueprint JobToken contract binding is invalid")
    if required["blueprint_hash"] != str(blueprint.get("blueprint_hash") or "").strip():
        raise ValueError("Chief Engineer blueprint JobToken blueprint binding is invalid")

    owned_paths = {
        str(item.get("path") or "").strip()
        for item in task_completion_projection.get("owned_artifacts") or []
        if isinstance(item, Mapping) and str(item.get("path") or "").strip()
    }
    allowed_write_paths = {
        str(item or "").strip() for item in job_token.get("allowed_write_paths") or [] if str(item or "").strip()
    }
    if not owned_paths.issubset(allowed_write_paths):
        raise ValueError("Chief Engineer blueprint JobToken does not cover task-owned artifacts")
    return job_token


def validate_director_handoff_from_payload(
    workspace: str,
    payload: dict[str, Any],
    *,
    require_strict: bool = False,
) -> dict[str, Any]:
    """Validate whether a payload may enter Director dispatch.

    This is the shared pre-Director policy seam for PM dispatch, task-market
    consumers, CLI loops, and future execution-envelope creation. The default
    remains transition-safe: base handoff authorization is authoritative,
    while the strict `ce_handoff_decision.v1` is still computed and exposed for
    audit. Callers can opt into `require_strict=True` once their dispatch path
    always carries immutable PM/CE/profile hash bindings.
    """

    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return _handoff_validation_result(
            allowed=False,
            reason="workspace is required for Chief Engineer handoff validation",
            require_strict=require_strict,
        )
    task_id = _task_id_from_payload(payload)
    blueprint_id = _blueprint_id_from_payload(payload)
    if not blueprint_id:
        return _handoff_validation_result(
            allowed=False,
            reason="missing Chief Engineer blueprint id",
            task_id=task_id,
            require_strict=require_strict,
        )

    blueprint = BlueprintPersistence(workspace_token, ensure_directory=False).load(blueprint_id)
    if not isinstance(blueprint, dict):
        return _handoff_validation_result(
            allowed=False,
            reason=f"Chief Engineer blueprint {blueprint_id} missing or unreadable",
            task_id=task_id,
            blueprint_id=blueprint_id,
            require_strict=require_strict,
        )

    blueprint_task_id = str(blueprint.get("task_id") or blueprint.get("pm_task_id") or "").strip()
    if task_id and blueprint_task_id and task_id != blueprint_task_id:
        return _handoff_validation_result(
            allowed=False,
            reason=f"Chief Engineer blueprint {blueprint_id} belongs to {blueprint_task_id}, not {task_id}",
            task_id=task_id,
            blueprint_id=blueprint_id,
            blueprint_task_id=blueprint_task_id,
            require_strict=require_strict,
        )

    task_completion_projection: dict[str, Any] = {}
    job_token: dict[str, Any] = {}
    if require_strict:
        try:
            task_completion_projection = _project_task_completion_contract(
                blueprint,
                task_id=task_id or blueprint_task_id,
            )
            job_token = _project_validated_job_token(
                blueprint,
                task_completion_projection=task_completion_projection,
            )
        except (TypeError, ValueError) as exc:
            return _handoff_validation_result(
                allowed=False,
                reason=f"task-local project completion projection is invalid: {exc}",
                task_id=task_id,
                blueprint_id=blueprint_id,
                blueprint_task_id=blueprint_task_id,
                require_strict=require_strict,
            )

    base_handoff_decision = evaluate_handoff_decision(
        workspace_token,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )
    strict_decision = build_ce_handoff_decision(
        workspace_token,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
        base_decision=base_handoff_decision,
    )
    if not base_handoff_decision.allowed:
        return _handoff_validation_result(
            allowed=False,
            reason=base_handoff_decision.reason,
            task_id=task_id,
            blueprint_id=blueprint_id,
            blueprint_task_id=blueprint_task_id,
            base_handoff_decision=base_handoff_decision,
            strict_decision=strict_decision,
            task_completion_projection=task_completion_projection,
            require_strict=require_strict,
        )
    if require_strict and not strict_decision.allowed:
        return _handoff_validation_result(
            allowed=False,
            reason=strict_decision.reason or "strict Chief Engineer handoff decision blocked Director dispatch",
            task_id=task_id,
            blueprint_id=blueprint_id,
            blueprint_task_id=blueprint_task_id,
            base_handoff_decision=base_handoff_decision,
            strict_decision=strict_decision,
            task_completion_projection=task_completion_projection,
            require_strict=require_strict,
        )
    return _handoff_validation_result(
        allowed=True,
        reason=base_handoff_decision.reason,
        task_id=task_id,
        blueprint_id=blueprint_id,
        blueprint_task_id=blueprint_task_id,
        base_handoff_decision=base_handoff_decision,
        strict_decision=strict_decision,
        task_completion_projection=task_completion_projection,
        job_token=job_token,
        require_strict=require_strict,
    )


def assert_handoff_ready(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
) -> HandoffDecisionV1:
    """Raise when a blueprint must not be handed to the Director.

    Fail-closed enforcement helper for callers that want a hard gate: on a
    blocked decision it raises :class:`ChiefEngineerBlueprintErrorV1` with
    code ``handoff_blocked`` and the decision in ``details``.
    """
    decision = evaluate_handoff_decision(
        workspace,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )
    if not decision.allowed:
        raise ChiefEngineerBlueprintErrorV1(
            f"handoff blocked: {decision.reason}",
            code="handoff_blocked",
            details=decision.to_dict(),
        )
    return decision
