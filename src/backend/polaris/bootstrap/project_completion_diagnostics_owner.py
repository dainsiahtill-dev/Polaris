"""Bootstrap composition adapter for authoritative project-completion facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any, cast

from polaris.cells.chief_engineer.blueprint.public import (
    ArtifactObligationV1,
    EntrypointObligationV1,
    ProjectCompletionContractV1,
    QueryProjectCompletionContractV1,
    VerificationObligationV1,
    query_project_completion_contract,
)
from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    RunLedgerProjectionResultV1,
    read_run_ledger_projection,
)
from polaris.cells.control_plane.verifier_policy.public import (
    EvaluateVerifierCommandPolicyQueryV1,
    VerifierCommandPolicyDecisionV1,
    evaluate_verifier_command_policy,
)
from polaris.cells.director.runtime.public import (
    DirectorRepairCoverageReportV1,
    DirectorRepairPlanProbeResultV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairPlanProbeV1,
    query_director_repair_coverage,
    query_director_repair_plan_probe,
)
from polaris.cells.factory.verification_guard.public.bootstrap import (
    bind_project_completion_owner_observation_port,
    bind_project_completion_physical_evidence_port,
    build_project_completion_contract_observation,
    build_project_completion_physical_evidence_intent,
)
from polaris.cells.factory.verification_guard.public.contracts import (
    ProjectArtifactObligationObservationV1,
    ProjectCompletionContractObservationV1,
    ProjectCompletionEvidenceV1,
    ProjectCompletionObligationsObservationV1,
    ProjectCompletionOwnerObservationV1,
    ProjectCompletionOwnerObservationV1Error,
    ProjectCompletionPhysicalEvidenceEffectV1,
    ProjectCompletionPhysicalEvidenceIntentV1,
    ProjectEntrypointObligationObservationV1,
    ProjectKindAuthorityObservationV1,
    ProjectKindObservationV1,
    ProjectRepairCoverageV1,
    ProjectVerificationCommandAuthorityObservationV1,
    ProjectVerificationObligationObservationV1,
)
from polaris.cells.runtime.execution_broker.public.bootstrap import (
    bind_project_verification_execution_authority_port,
)
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ConsumeProjectVerificationCapabilityCommandV1,
    ProjectArtifactExecutionAuthorityV1,
    ProjectArtifactReceiptV1,
    ProjectVerificationArtifactInputV1,
    ProjectVerificationCapabilityConsumptionV1,
    ProjectVerificationExecutionAuthorityV1,
    ProjectVerificationExecutionResultV1,
    ProjectVerificationReceiptV1,
    QueryProjectArtifactReceiptV1,
    QueryProjectVerificationReceiptV1,
    RecordProjectArtifactCommandV1,
    ResolveProjectArtifactAuthorityQueryV1,
    ResolveProjectVerificationAuthorityQueryV1,
    authorize_project_verification_command,
    query_project_artifact_receipt,
    query_project_verification_receipt,
    record_project_artifact,
    run_project_verification,
)
from polaris.cells.runtime.task_runtime.public import (
    ObservableTaskRowsProjectionV1,
    query_observable_task_rows,
)

_CAPABILITY_CONSUMPTION_LOCK = RLock()
_CONSUMED_PROJECT_VERIFICATION_CAPABILITIES: set[str] = set()


def _fail(code: str, message: str) -> ProjectCompletionOwnerObservationV1Error:
    return ProjectCompletionOwnerObservationV1Error(code, message)


def _canonical_hash(payload: object) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise _fail("project_completion_owner_payload_not_canonical", str(exc)) from exc
    return hashlib.sha256(raw).hexdigest()


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("invalid_project_completion_owner_projection", f"{name} must be a mapping")
    return dict(value)


def _owner_task_id(item: object) -> str:
    owner_task_id = str(getattr(item, "owner_task_id", None) or "").strip()
    if not owner_task_id:
        raise _fail("project_completion_obligation_owner_task_missing", "Active obligation lacks owner_task_id")
    return owner_task_id


def _map_completion_contract(contract: object) -> ProjectCompletionContractObservationV1:
    """Map and independently hash-check one exact CE owner contract."""

    if type(contract) is not ProjectCompletionContractV1:
        raise _fail("invalid_project_completion_contract_type", "CE returned a completion-contract lookalike")
    typed_contract = cast(ProjectCompletionContractV1, contract)
    artifacts = tuple(
        ProjectArtifactObligationObservationV1(
            obligation_id=item.obligation_id,
            path=item.path,
            semantic_role=item.semantic_role,
            applicability=item.applicability,
            owner_task_id=item.owner_task_id,
        )
        for item in typed_contract.obligations.artifacts
    )
    entrypoints = tuple(
        ProjectEntrypointObligationObservationV1(
            obligation_id=item.obligation_id,
            kind=item.kind,
            applicability=item.applicability,
            owner_task_id=item.owner_task_id,
            source_path=item.source_path,
            runtime_path=item.runtime_path,
            command=item.command,
        )
        for item in typed_contract.obligations.entrypoints
    )
    verification = tuple(
        ProjectVerificationObligationObservationV1(
            obligation_id=item.obligation_id,
            modality=item.modality,
            command=item.command,
            applicability=item.applicability,
            covers_obligation_ids=item.covers_obligation_ids,
            owner_task_id=item.owner_task_id,
            command_authority_hash=item.command_authority_hash,
        )
        for item in typed_contract.obligations.verification
    )
    command_authority = tuple(
        ProjectVerificationCommandAuthorityObservationV1(
            task_id=item.task_id,
            modality=item.modality,
            argv=item.argv,
            cwd=item.cwd,
            command=item.command,
            authority_hash=item.authority_hash,
        )
        for item in typed_contract.verification_command_authority
    )
    mapped = build_project_completion_contract_observation(
        contract_id=typed_contract.contract_id,
        contract_hash=typed_contract.contract_hash,
        project_id=typed_contract.project_id,
        run_id=typed_contract.run_id,
        project_kind=cast(ProjectKindObservationV1, typed_contract.project_kind),
        project_kind_authority=ProjectKindAuthorityObservationV1(
            project_kind=cast(ProjectKindObservationV1, typed_contract.project_kind_authority.project_kind),
            source_ref=typed_contract.project_kind_authority.source_ref,
            source_hash=typed_contract.project_kind_authority.source_hash,
            justification=typed_contract.project_kind_authority.justification,
            authority_hash=typed_contract.project_kind_authority.authority_hash,
        ),
        pm_contract_hash=typed_contract.pm_contract_hash,
        covered_task_ids=typed_contract.covered_task_ids,
        obligations=ProjectCompletionObligationsObservationV1(
            artifacts=artifacts,
            entrypoints=entrypoints,
            verification=verification,
        ),
        completion_predicate_version=typed_contract.completion_predicate_version,
        verifier_policy_hash=typed_contract.verifier_policy_hash,
        verifier_policy_snapshot_hash=typed_contract.verifier_policy_snapshot_hash,
        verification_command_authority=command_authority,
    )
    if mapped.to_seed_dict() != typed_contract.to_seed_dict() or mapped.contract_hash != typed_contract.contract_hash:
        raise _fail(
            "project_completion_contract_mapping_mismatch",
            "VerificationGuard contract observation does not exactly match the CE owner payload",
        )
    return mapped


def _row_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    metadata = row.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    fact = metadata_map.get("task_runtime_execution_fact")
    fact_map = fact if isinstance(fact, Mapping) else {}
    workflow_run_id = str(row.get("workflow_run_id") or row.get("run_id") or fact_map.get("run_id") or "").strip()
    factory_run_id = str(
        row.get("factory_run_id") or metadata_map.get("factory_run_id") or fact_map.get("factory_run_id") or ""
    ).strip()
    return workflow_run_id, factory_run_id


def _repair_coverage(
    *,
    workspace: str,
    project_id: str,
    run_id: str,
    contract_hash: str,
    obligation: ArtifactObligationV1 | EntrypointObligationV1 | VerificationObligationV1,
) -> ProjectRepairCoverageV1:
    obligation_id = obligation.obligation_id
    owner_task_id = _owner_task_id(obligation)
    target = str(
        getattr(obligation, "path", None)
        or getattr(obligation, "runtime_path", None)
        or getattr(obligation, "source_path", None)
        or getattr(obligation, "command", None)
        or obligation_id
    )
    diagnostic = f"project completion obligation {obligation_id} failed for {target}"
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    if type(coverage) is not DirectorRepairCoverageReportV1:
        raise _fail("invalid_director_repair_coverage_type", "Director coverage query returned a lookalike")
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=(diagnostic,),
            base_files={},
            source_tools=(),
            mode="shadow",
            metadata={"owner_task_id": owner_task_id, "obligation_id": obligation_id},
        )
    )
    if type(probe) is not DirectorRepairPlanProbeResultV1:
        raise _fail("invalid_director_repair_plan_probe_type", "Director plan probe returned a lookalike")
    tools = tuple(probe.plannable_source_tools)
    if len(tools) == 1:
        status = "executable_runtime"
        source_tool = tools[0]
    elif (
        coverage.known_rule_matched
        if hasattr(coverage, "known_rule_matched")
        else coverage.covered_diagnostic_count > 0
    ):
        status = "metadata_only"
        source_tool = None
    else:
        status = "uncovered"
        source_tool = None
    payload = {"coverage": coverage.to_dict(), "plan_probe": probe.to_dict()}
    evidence_hash = _canonical_hash(payload)
    return ProjectRepairCoverageV1(
        workspace=workspace,
        project_id=project_id,
        run_id=run_id,
        completion_contract_hash=contract_hash,
        obligation_id=obligation_id,
        owner_task_id=owner_task_id,
        status=status,  # type: ignore[arg-type]
        evidence_ref=f"director-runtime://repair-coverage/{evidence_hash}",
        source_tool=source_tool,
    )


class ProjectCompletionOwnerObservationAdapter:
    """Join public owner projections without reading target files or guessing ownership."""

    def resolve_project_verification_authority(
        self,
        query: ResolveProjectVerificationAuthorityQueryV1,
        /,
    ) -> ProjectVerificationExecutionAuthorityV1:
        """Re-resolve exact CE command and committed JobToken policy before spawn."""

        if type(query) is not ResolveProjectVerificationAuthorityQueryV1:
            raise _fail("invalid_project_verification_authority_query", "Authority query must be exact")
        ce_contract = query_project_completion_contract(
            QueryProjectCompletionContractV1(
                workspace=query.workspace,
                project_id=query.project_id,
                run_id=query.run_id,
                contract_hash=query.completion_contract_hash,
            )
        )
        if type(ce_contract) is not ProjectCompletionContractV1:
            raise _fail("project_verification_contract_missing", "CE completion contract is unavailable")
        contract = _map_completion_contract(ce_contract)
        intent = build_project_completion_physical_evidence_intent(
            contract,
            query.obligation_id,
            workspace=query.workspace,
        )
        if (
            intent.kind != "command"
            or intent.modality is None
            or intent.cwd is None
            or intent.command_authority_hash is None
        ):
            raise _fail(
                "project_verification_command_authority_missing",
                "Obligation lacks exact typed CE command authority",
            )
        ledger_result = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=query.workspace,
                run_id=query.run_id,
                factory_run_id=query.run_id,
                project_id=query.project_id,
                include_migration_ledgers=False,
            )
        )
        if type(ledger_result) is not RunLedgerProjectionResultV1:
            raise _fail("invalid_project_verification_run_ledger_type", "Run Ledger returned a lookalike")
        ledger = _mapping(ledger_result.projection, "run_ledger")
        if ledger.get("query_scope") != {
            "run_id": query.run_id,
            "factory_run_id": query.run_id,
            "project_id": query.project_id,
        }:
            raise _fail("project_verification_run_ledger_scope_mismatch", "Run Ledger scope is stale")
        run_projection = _mapping(ledger.get("run_projection"), "run_ledger.run_projection")
        capability = _mapping(run_projection.get("capability"), "run_ledger.run_projection.capability")
        if (
            capability.get("ok") is not True
            or capability.get("issues") not in ([], ())
            or str(capability.get("latest_contract_hash") or "") != query.completion_contract_hash
        ):
            raise _fail(
                "project_verification_capability_not_authoritative",
                "Run Ledger capability must be clean and bind the exact contract",
            )
        raw_token_ids = capability.get("job_token_ids")
        token_ids = (
            tuple(sorted({str(item).strip() for item in raw_token_ids if isinstance(item, str) and str(item).strip()}))
            if isinstance(raw_token_ids, (list, tuple))
            else ()
        )
        latest_token_id = str(capability.get("latest_token_id") or "").strip()
        if not token_ids or latest_token_id not in token_ids:
            raise _fail(
                "project_verification_job_token_uncommitted",
                "Exact contract requires a committed current JobToken",
            )
        evidence_policy = _mapping(
            run_projection.get("evidence_policy"),
            "run_ledger.run_projection.evidence_policy",
        )
        raw_enabled = evidence_policy.get("enabled_modalities")
        enabled_modalities = (
            {str(item).strip() for item in raw_enabled if isinstance(item, str) and str(item).strip()}
            if isinstance(raw_enabled, (list, tuple))
            else set()
        )
        if intent.modality not in enabled_modalities:
            raise _fail(
                "project_verification_modality_not_permitted",
                "Committed JobToken policy does not enable this verifier modality",
            )
        policy_payload = {
            "domain": "runtime.execution_broker.project_verification_policy.v1",
            "workspace": query.workspace,
            "project_id": query.project_id,
            "run_id": query.run_id,
            "completion_contract_hash": query.completion_contract_hash,
            "obligation_id": query.obligation_id,
            "owner_task_id": intent.owner_task_id,
            "modality": intent.modality,
            "argv": list(intent.argv),
            "cwd": intent.cwd,
            "command_authority_hash": intent.command_authority_hash,
            "job_token_id": latest_token_id,
            "job_token_ids": list(token_ids),
            "capability": capability,
            "evidence_policy": evidence_policy,
        }
        policy_decision = evaluate_verifier_command_policy(
            EvaluateVerifierCommandPolicyQueryV1(
                workspace=query.workspace,
                project_id=query.project_id,
                run_id=query.run_id,
                task_id=intent.owner_task_id,
                completion_contract_hash=query.completion_contract_hash,
                verifier_obligation_id=query.obligation_id,
                command_authority_hash=intent.command_authority_hash,
                modality=intent.modality,
                argv=intent.argv,
                cwd=intent.cwd,
                input_obligation_ids=tuple(item.obligation_id for item in intent.input_artifacts),
            )
        )
        if (
            type(policy_decision) is not VerifierCommandPolicyDecisionV1
            or not policy_decision.authorized
            or policy_decision.normalized_argv != intent.argv
            or policy_decision.normalized_cwd != intent.cwd
        ):
            raise _fail(
                "project_verification_command_policy_rejected",
                getattr(policy_decision, "detail", "Verifier command policy rejected the command"),
            )
        execution_policy_hash = _canonical_hash(policy_payload)
        authority_revision = _canonical_hash(
            {
                "domain": "runtime.execution_broker.project_verification_authority_revision.v1",
                "execution_policy_hash": execution_policy_hash,
                "policy_decision_hash": policy_decision.policy_decision_hash,
                "job_token_id": latest_token_id,
            }
        )
        return ProjectVerificationExecutionAuthorityV1(
            workspace=query.workspace,
            project_id=query.project_id,
            run_id=query.run_id,
            completion_contract_hash=query.completion_contract_hash,
            obligation_id=query.obligation_id,
            owner_task_id=intent.owner_task_id,
            modality=intent.modality,
            argv=intent.argv,
            cwd=intent.cwd,
            command_authority_hash=intent.command_authority_hash,
            input_artifacts=tuple(
                ProjectVerificationArtifactInputV1(obligation_id=item.obligation_id, path=item.path)
                for item in intent.input_artifacts
            ),
            timeout_seconds=intent.timeout_seconds,
            job_token_id=latest_token_id,
            job_token_set_hash=_canonical_hash(
                {
                    "domain": "control_plane.run_ledger.job_token_set.v1",
                    "token_ids": list(token_ids),
                }
            ),
            execution_policy_hash=execution_policy_hash,
            authority_revision=authority_revision,
            policy_profile_id=policy_decision.profile_id,
            policy_decision_hash=policy_decision.policy_decision_hash,
            executable_path=policy_decision.executable_path,
            executable_realpath=policy_decision.executable_realpath,
            executable_hash=policy_decision.executable_hash,
        )

    def resolve_project_artifact_authority(
        self,
        query: ResolveProjectArtifactAuthorityQueryV1,
        /,
    ) -> ProjectArtifactExecutionAuthorityV1:
        """Bind an artifact obligation to the current CE contract and JobToken."""

        if type(query) is not ResolveProjectArtifactAuthorityQueryV1:
            raise _fail("invalid_project_artifact_authority_query", "Artifact authority query must be exact")
        ce_contract = query_project_completion_contract(
            QueryProjectCompletionContractV1(
                workspace=query.workspace,
                project_id=query.project_id,
                run_id=query.run_id,
                contract_hash=query.completion_contract_hash,
            )
        )
        if type(ce_contract) is not ProjectCompletionContractV1:
            raise _fail("project_artifact_contract_missing", "CE completion contract is unavailable")
        intent = build_project_completion_physical_evidence_intent(
            _map_completion_contract(ce_contract),
            query.obligation_id,
            workspace=query.workspace,
        )
        if intent.kind != "artifact" or intent.artifact_path is None:
            raise _fail("project_artifact_authority_missing", "Obligation is not an exact CE artifact obligation")
        ledger_result = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=query.workspace,
                run_id=query.run_id,
                factory_run_id=query.run_id,
                project_id=query.project_id,
                include_migration_ledgers=False,
            )
        )
        if type(ledger_result) is not RunLedgerProjectionResultV1:
            raise _fail("invalid_project_artifact_run_ledger_type", "Run Ledger returned a lookalike")
        ledger = _mapping(ledger_result.projection, "run_ledger")
        run_projection = _mapping(ledger.get("run_projection"), "run_ledger.run_projection")
        capability = _mapping(run_projection.get("capability"), "run_ledger.run_projection.capability")
        if (
            ledger.get("query_scope")
            != {"run_id": query.run_id, "factory_run_id": query.run_id, "project_id": query.project_id}
            or capability.get("ok") is not True
            or capability.get("issues") not in ([], ())
            or str(capability.get("latest_contract_hash") or "") != query.completion_contract_hash
        ):
            raise _fail("project_artifact_capability_not_authoritative", "Run Ledger capability is stale")
        raw_ids = capability.get("job_token_ids")
        token_ids = (
            tuple(sorted({str(item).strip() for item in raw_ids if isinstance(item, str) and str(item).strip()}))
            if isinstance(raw_ids, (list, tuple))
            else ()
        )
        latest_token_id = str(capability.get("latest_token_id") or "").strip()
        if not token_ids or latest_token_id not in token_ids:
            raise _fail("project_artifact_job_token_uncommitted", "Artifact requires a committed current JobToken")
        job_token_set_hash = _canonical_hash(
            {"domain": "control_plane.run_ledger.job_token_set.v1", "token_ids": list(token_ids)}
        )
        execution_policy_hash = _canonical_hash(
            {
                "domain": "runtime.execution_broker.project_artifact_policy.v1",
                "workspace": query.workspace,
                "project_id": query.project_id,
                "run_id": query.run_id,
                "completion_contract_hash": query.completion_contract_hash,
                "obligation_id": query.obligation_id,
                "owner_task_id": intent.owner_task_id,
                "path": intent.artifact_path,
                "job_token_id": latest_token_id,
                "job_token_set_hash": job_token_set_hash,
            }
        )
        return ProjectArtifactExecutionAuthorityV1(
            workspace=query.workspace,
            project_id=query.project_id,
            run_id=query.run_id,
            completion_contract_hash=query.completion_contract_hash,
            obligation_id=query.obligation_id,
            owner_task_id=intent.owner_task_id,
            path=intent.artifact_path,
            job_token_id=latest_token_id,
            job_token_set_hash=job_token_set_hash,
            execution_policy_hash=execution_policy_hash,
            authority_revision=_canonical_hash(
                {
                    "domain": "runtime.execution_broker.project_artifact_authority_revision.v1",
                    "execution_policy_hash": execution_policy_hash,
                    "job_token_id": latest_token_id,
                }
            ),
        )

    def consume_project_verification_execution_capability(
        self,
        command: ConsumeProjectVerificationCapabilityCommandV1,
        /,
    ) -> ProjectVerificationCapabilityConsumptionV1:
        """Re-read policy and consume a one-use fenced attempt under one lock."""

        if type(command) is not ConsumeProjectVerificationCapabilityCommandV1:
            raise _fail("invalid_project_verification_capability_command", "Capability command must be exact")
        with _CAPABILITY_CONSUMPTION_LOCK:
            current = self.resolve_project_verification_authority(
                ResolveProjectVerificationAuthorityQueryV1(
                    workspace=command.workspace,
                    project_id=command.project_id,
                    run_id=command.run_id,
                    completion_contract_hash=command.completion_contract_hash,
                    obligation_id=command.obligation_id,
                )
            )
            expected = tuple(getattr(command, name) for name in ProjectVerificationExecutionAuthorityV1.__dataclass_fields__)
            observed = tuple(getattr(current, name) for name in ProjectVerificationExecutionAuthorityV1.__dataclass_fields__)
            if observed != expected:
                raise _fail(
                    "project_verification_capability_authority_changed",
                    "Current JobToken/verifier policy authority changed before capability consume",
                )
            capability_id = _canonical_hash(
                {
                    "domain": "runtime.execution_broker.project_verification_capability.v1",
                    "authority_revision": current.authority_revision,
                    "effect_key": command.effect_key,
                    "attempt_id": command.attempt_id,
                }
            )
            if capability_id in _CONSUMED_PROJECT_VERIFICATION_CAPABILITIES:
                raise _fail("project_verification_capability_already_consumed", "Attempt capability is one-use")
            _CONSUMED_PROJECT_VERIFICATION_CAPABILITIES.add(capability_id)
            return ProjectVerificationCapabilityConsumptionV1(
                capability_id=capability_id,
                effect_key=command.effect_key,
                attempt_id=command.attempt_id,
                authority_revision=current.authority_revision,
                job_token_id=current.job_token_id,
                job_token_set_hash=current.job_token_set_hash,
                execution_policy_hash=current.execution_policy_hash,
                policy_profile_id=current.policy_profile_id,
                policy_decision_hash=current.policy_decision_hash,
            )

    def materialize_project_completion_evidence(
        self,
        intent: ProjectCompletionPhysicalEvidenceIntentV1,
        /,
    ) -> ProjectCompletionPhysicalEvidenceEffectV1:
        """Translate one VerificationGuard-sealed intent to the physical owner."""

        if type(intent) is not ProjectCompletionPhysicalEvidenceIntentV1:
            raise _fail(
                "invalid_project_completion_physical_intent_type",
                "Physical evidence adapter requires an exact sealed intent",
            )
        if intent.kind == "artifact":
            if intent.artifact_path is None:  # pragma: no cover - sealed intent invariant.
                raise _fail("project_artifact_path_missing", "Artifact intent lacks path")
            receipt = record_project_artifact(
                RecordProjectArtifactCommandV1(
                    workspace=intent.workspace,
                    project_id=intent.project_id,
                    run_id=intent.run_id,
                    completion_contract_hash=intent.completion_contract_hash,
                    obligation_id=intent.obligation_id,
                    owner_task_id=intent.owner_task_id,
                    path=intent.artifact_path,
                )
            )
            if type(receipt) is not ProjectArtifactReceiptV1:
                raise _fail("invalid_project_artifact_receipt_type", "Physical owner returned a lookalike")
            return ProjectCompletionPhysicalEvidenceEffectV1(
                code="project_artifact_receipt_recorded",
                spawned=False,
                receipt_ref=receipt.receipt_ref,
            )
        if (
            intent.modality is None or intent.cwd is None or intent.command_authority_hash is None
        ):  # pragma: no cover - sealed intent invariant.
            raise _fail("project_verifier_intent_incomplete", "Command intent lacks exact authority fields")
        command = authorize_project_verification_command(
            ResolveProjectVerificationAuthorityQueryV1(
                workspace=intent.workspace,
                project_id=intent.project_id,
                run_id=intent.run_id,
                completion_contract_hash=intent.completion_contract_hash,
                obligation_id=intent.obligation_id,
            )
        )
        expected_intent = (
            intent.owner_task_id,
            intent.modality,
            intent.argv,
            intent.cwd,
            intent.command_authority_hash,
            tuple((item.obligation_id, item.path) for item in intent.input_artifacts),
            intent.timeout_seconds,
        )
        authorized_intent = (
            command.owner_task_id,
            command.modality,
            command.argv,
            command.cwd,
            command.command_authority_hash,
            tuple((item.obligation_id, item.path) for item in command.input_artifacts),
            command.timeout_seconds,
        )
        if authorized_intent != expected_intent:
            raise _fail(
                "project_verifier_authority_changed",
                "Execution owner authority no longer matches VerificationGuard intent",
            )
        result = run_project_verification(command)
        if type(result) is not ProjectVerificationExecutionResultV1:
            raise _fail("invalid_project_verification_execution_result_type", "Physical owner returned a lookalike")
        return ProjectCompletionPhysicalEvidenceEffectV1(
            code=result.code,
            spawned=result.spawned,
            receipt_ref=result.receipt.receipt_ref if result.receipt is not None else None,
        )

    @staticmethod
    def _physical_evidence(
        contract: ProjectCompletionContractObservationV1,
        *,
        workspace: str,
    ) -> tuple[ProjectCompletionEvidenceV1, ...]:
        evidence: list[ProjectCompletionEvidenceV1] = []
        obligations: tuple[
            ProjectArtifactObligationObservationV1
            | ProjectEntrypointObligationObservationV1
            | ProjectVerificationObligationObservationV1,
            ...,
        ] = (
            *contract.obligations.artifacts,
            *contract.obligations.entrypoints,
            *contract.obligations.verification,
        )
        for obligation in obligations:
            if obligation.applicability == "not_applicable":
                continue
            intent = build_project_completion_physical_evidence_intent(
                contract,
                obligation.obligation_id,
                workspace=workspace,
            )
            if intent.kind == "artifact":
                if intent.artifact_path is None:  # pragma: no cover - sealed intent invariant.
                    raise _fail("project_artifact_path_missing", "Artifact intent lacks path")
                artifact_receipt = query_project_artifact_receipt(
                    QueryProjectArtifactReceiptV1(
                        workspace=intent.workspace,
                        project_id=intent.project_id,
                        run_id=intent.run_id,
                        completion_contract_hash=intent.completion_contract_hash,
                        obligation_id=intent.obligation_id,
                        owner_task_id=intent.owner_task_id,
                        path=intent.artifact_path,
                    )
                )
                if artifact_receipt is None:
                    continue
                if (
                    type(artifact_receipt) is not ProjectArtifactReceiptV1
                    or artifact_receipt.owner_module_id != "runtime.execution_broker"
                ):
                    raise _fail("invalid_project_artifact_receipt_type", "Physical owner returned a lookalike")
                evidence.append(
                    ProjectCompletionEvidenceV1(
                        workspace=intent.workspace,
                        project_id=intent.project_id,
                        run_id=intent.run_id,
                        completion_contract_hash=intent.completion_contract_hash,
                        obligation_id=intent.obligation_id,
                        owner_task_id=intent.owner_task_id,
                        owner_module_id=artifact_receipt.owner_module_id,
                        status="passed",
                        owner_evidence_refs=(
                            artifact_receipt.receipt_ref,
                            f"artifact-sha256://{artifact_receipt.artifact_hash}",
                        ),
                        artifact_path=artifact_receipt.path,
                        artifact_hash=artifact_receipt.artifact_hash,
                    )
                )
                continue
            if intent.modality is None or intent.cwd is None or intent.command_authority_hash is None:
                raise _fail("project_verifier_intent_incomplete", "Command intent lacks exact authority fields")
            command = authorize_project_verification_command(
                ResolveProjectVerificationAuthorityQueryV1(
                    workspace=intent.workspace,
                    project_id=intent.project_id,
                    run_id=intent.run_id,
                    completion_contract_hash=intent.completion_contract_hash,
                    obligation_id=intent.obligation_id,
                )
            )
            verification_receipt = query_project_verification_receipt(
                QueryProjectVerificationReceiptV1(
                    workspace=command.workspace,
                    project_id=command.project_id,
                    run_id=command.run_id,
                    completion_contract_hash=command.completion_contract_hash,
                    obligation_id=command.obligation_id,
                    owner_task_id=command.owner_task_id,
                    modality=command.modality,
                    argv=command.argv,
                    cwd=command.cwd,
                    command_authority_hash=command.command_authority_hash,
                    input_artifacts=command.input_artifacts,
                    timeout_seconds=command.timeout_seconds,
                    job_token_id=command.job_token_id,
                    job_token_set_hash=command.job_token_set_hash,
                    execution_policy_hash=command.execution_policy_hash,
                    authority_revision=command.authority_revision,
                    policy_profile_id=command.policy_profile_id,
                    policy_decision_hash=command.policy_decision_hash,
                    executable_path=command.executable_path,
                    executable_realpath=command.executable_realpath,
                    executable_hash=command.executable_hash,
                )
            )
            if verification_receipt is None:
                continue
            if (
                type(verification_receipt) is not ProjectVerificationReceiptV1
                or verification_receipt.owner_module_id != "runtime.execution_broker"
            ):
                raise _fail("invalid_project_verification_receipt_type", "Physical owner returned a lookalike")
            evidence.append(
                ProjectCompletionEvidenceV1(
                    workspace=intent.workspace,
                    project_id=intent.project_id,
                    run_id=intent.run_id,
                    completion_contract_hash=intent.completion_contract_hash,
                    obligation_id=intent.obligation_id,
                    owner_task_id=intent.owner_task_id,
                    owner_module_id=verification_receipt.owner_module_id,
                    status="passed" if verification_receipt.succeeded else "failed",
                    owner_evidence_refs=(
                        verification_receipt.receipt_ref,
                        f"input-artifact-sha256://{verification_receipt.input_artifact_hash}",
                        f"output-sha256://{verification_receipt.output_hash}",
                    ),
                    verifier_receipt_ref=verification_receipt.receipt_ref,
                    verifier_exit_code=verification_receipt.exit_code,
                    verifier_modality=verification_receipt.modality,
                    verifier_argv=verification_receipt.argv,
                    verifier_cwd=verification_receipt.cwd,
                    verifier_command_authority_hash=verification_receipt.command_authority_hash,
                    verifier_input_artifact_hash=verification_receipt.input_artifact_hash,
                    verifier_timed_out=verification_receipt.timed_out,
                    verifier_output_hash=verification_receipt.output_hash,
                )
            )
        return tuple(evidence)

    def observe_project_completion(
        self,
        *,
        workspace: str,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
    ) -> ProjectCompletionOwnerObservationV1:
        canonical_workspace = str(Path(workspace).expanduser().resolve())
        ce_contract = query_project_completion_contract(
            QueryProjectCompletionContractV1(
                workspace=canonical_workspace,
                project_id=project_id,
                run_id=run_id,
                contract_hash=completion_contract_hash,
            )
        )
        if type(ce_contract) is not ProjectCompletionContractV1 or (
            ce_contract.project_id,
            ce_contract.run_id,
            ce_contract.contract_hash,
        ) != (project_id, run_id, completion_contract_hash):
            raise _fail(
                "project_completion_contract_identity_mismatch",
                "CE completion contract does not match the exact owner query",
            )
        contract = _map_completion_contract(ce_contract)
        task_projection = query_observable_task_rows(canonical_workspace)
        if type(task_projection) is not ObservableTaskRowsProjectionV1:
            raise _fail("invalid_project_completion_task_runtime_type", "TaskRuntime returned a lookalike")
        if (
            task_projection.workspace != canonical_workspace
            or not task_projection.authoritative
            or task_projection.degraded
        ):
            raise _fail(
                "project_completion_task_runtime_not_authoritative", "TaskRuntime must be exact and authoritative"
            )
        task_rows = task_projection.rows_for_factory_run(run_id)
        rows_by_id = {str(row.get("task_id") or row.get("id") or "").strip(): row for row in task_rows}
        if (
            len(rows_by_id) != len(task_rows)
            or not set(contract.covered_task_ids).issubset(rows_by_id)
            or "" in rows_by_id
        ):
            raise _fail("project_completion_task_runtime_owner_tasks_missing", "TaskRuntime lacks covered owner tasks")
        for task_id, row in rows_by_id.items():
            workflow_run_id, factory_run_id = _row_identity(row)
            if (
                factory_run_id != run_id
                or not workflow_run_id
                or type(row.get("fact_event_seq")) is not int
                or int(row["fact_event_seq"]) <= 0
            ):
                raise _fail(
                    "project_completion_task_runtime_owner_identity_invalid",
                    f"TaskRuntime owner identity is invalid for task {task_id!r}",
                )

        ledger_result = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=canonical_workspace,
                run_id=run_id,
                factory_run_id=run_id,
                project_id=project_id,
                include_migration_ledgers=False,
            )
        )
        if type(ledger_result) is not RunLedgerProjectionResultV1:
            raise _fail("invalid_project_completion_run_ledger_type", "Run Ledger returned a lookalike")
        ledger = _mapping(ledger_result.projection, "run_ledger")
        if ledger.get("query_scope") != {"run_id": run_id, "factory_run_id": run_id, "project_id": project_id}:
            raise _fail("project_completion_run_ledger_scope_mismatch", "Run Ledger scope does not match query")
        run_projection = _mapping(ledger.get("run_projection"), "run_ledger.run_projection")
        capability = _mapping(run_projection.get("capability"), "run_ledger.run_projection.capability")
        if str(capability.get("latest_contract_hash") or "") != completion_contract_hash:
            raise _fail("project_completion_run_ledger_contract_mismatch", "Run Ledger contract hash is stale")
        raw_job_token_ids = capability.get("job_token_ids")
        job_token_ids = (
            {str(item).strip() for item in raw_job_token_ids if str(item).strip()}
            if isinstance(raw_job_token_ids, (list, tuple))
            else set()
        )
        latest_token_id = str(capability.get("latest_token_id") or "").strip()
        if not job_token_ids or latest_token_id not in job_token_ids:
            raise _fail(
                "project_completion_run_ledger_capability_uncommitted",
                "Run Ledger capability must bind a committed JobToken set",
            )
        task_boundary = _mapping(ledger.get("task_boundary"), "run_ledger.task_boundary")
        latest = task_boundary.get("latest_by_task")
        latest_by_task = dict(latest) if isinstance(latest, Mapping) else {}
        gates_raw = run_projection.get("gates")
        if not isinstance(gates_raw, (list, tuple)):
            raise _fail("invalid_project_completion_run_ledger_gates", "Run Ledger gates must be a list")
        gates = tuple(_mapping(gate, "run_ledger.gate") for gate in gates_raw)
        for gate in gates:
            gate_token_id = str(gate.get("job_token_id") or "").strip()
            if (
                not str(gate.get("content_id") or gate.get("append_id") or "").strip()
                or gate_token_id not in job_token_ids
            ):
                raise _fail(
                    "project_completion_run_ledger_gate_uncommitted",
                    "Every verifier gate requires committed event and JobToken evidence",
                )

        obligations: tuple[
            ProjectArtifactObligationObservationV1
            | ProjectEntrypointObligationObservationV1
            | ProjectVerificationObligationObservationV1,
            ...,
        ] = (
            *contract.obligations.artifacts,
            *contract.obligations.entrypoints,
            *contract.obligations.verification,
        )
        for obligation in obligations:
            if getattr(obligation, "applicability", None) == "not_applicable":
                continue
            owner_task_id = _owner_task_id(obligation)
            if owner_task_id not in rows_by_id:
                raise _fail(
                    "project_completion_obligation_owner_task_missing",
                    f"TaskRuntime lacks owner task {owner_task_id!r} for obligation {obligation.obligation_id!r}",
                )
            row = rows_by_id[owner_task_id]
            boundary_raw = latest_by_task.get(owner_task_id)
            boundary = dict(boundary_raw) if isinstance(boundary_raw, Mapping) else None
            if boundary is not None:
                workflow_run_id, _ = _row_identity(row)
                boundary_refs = boundary.get("evidence_refs")
                if (
                    str(boundary.get("task_id") or owner_task_id).strip() != owner_task_id
                    or str(boundary.get("run_id") or "").strip() != workflow_run_id
                    or not isinstance(boundary_refs, (list, tuple))
                    or not all(isinstance(item, str) and item.strip() for item in boundary_refs)
                    or not boundary_refs
                ):
                    raise _fail(
                        "project_completion_task_boundary_identity_invalid",
                        f"TaskBoundary identity/evidence is invalid for owner task {owner_task_id!r}",
                    )

        # TaskRuntime, TaskBoundary and Run Ledger remain prerequisites only.
        # Completion evidence comes exclusively from current, exact, owner-
        # sealed runtime.execution_broker receipts.
        evidence = self._physical_evidence(contract, workspace=canonical_workspace)
        original_obligations: dict[
            str,
            ArtifactObligationV1 | EntrypointObligationV1 | VerificationObligationV1,
        ] = {}
        for artifact in ce_contract.obligations.artifacts:
            original_obligations[artifact.obligation_id] = artifact
        for entrypoint in ce_contract.obligations.entrypoints:
            original_obligations[entrypoint.obligation_id] = entrypoint
        for verification in ce_contract.obligations.verification:
            original_obligations[verification.obligation_id] = verification
        repair_coverage = tuple(
            _repair_coverage(
                workspace=canonical_workspace,
                project_id=project_id,
                run_id=run_id,
                contract_hash=completion_contract_hash,
                obligation=original_obligations[item.obligation_id],
            )
            for item in evidence
            if item.status == "failed"
        )
        return ProjectCompletionOwnerObservationV1(
            workspace=canonical_workspace,
            project_id=project_id,
            run_id=run_id,
            completion_contract_hash=completion_contract_hash,
            contract=contract,
            evidence=evidence,
            repair_coverage=repair_coverage,
        )


PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER = ProjectCompletionOwnerObservationAdapter()


def configure_project_completion_diagnostics_owner() -> None:
    bind_project_verification_execution_authority_port(PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER)
    bind_project_completion_owner_observation_port(PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER)
    bind_project_completion_physical_evidence_port(PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER)


__all__ = [
    "PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER",
    "ProjectCompletionOwnerObservationAdapter",
    "configure_project_completion_diagnostics_owner",
]
