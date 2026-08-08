"""Same-Cell authority binding for project-completion owner observations."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from polaris.cells.factory.verification_guard.public.contracts import (
    _PROJECT_COMPLETION_CONTRACT_OBSERVATION_TOKEN,
    _PROJECT_COMPLETION_OWNER_BUNDLE_TOKEN,
    ProjectCompletionContractObservationV1,
    ProjectCompletionObligationsObservationV1,
    ProjectCompletionOwnerEvidenceBundleV1,
    ProjectCompletionOwnerObservationPortV1,
    ProjectCompletionOwnerObservationV1,
    ProjectCompletionOwnerObservationV1Error,
    ProjectKindAuthorityObservationV1,
    ProjectKindObservationV1,
    ProjectVerificationCommandAuthorityObservationV1,
    QueryProjectCompletionDiagnosticsV1,
    _canonical_hash,
)

_project_completion_owner_observation_port: ProjectCompletionOwnerObservationPortV1 | None = None
_owner_port_lock = Lock()


def build_project_completion_contract_observation(
    *,
    contract_id: str,
    contract_hash: str,
    project_id: str,
    run_id: str,
    project_kind: ProjectKindObservationV1,
    project_kind_authority: ProjectKindAuthorityObservationV1,
    pm_contract_hash: str,
    covered_task_ids: tuple[str, ...],
    obligations: ProjectCompletionObligationsObservationV1,
    completion_predicate_version: str,
    verifier_policy_hash: str,
    verifier_policy_snapshot_hash: str,
    verification_command_authority: tuple[ProjectVerificationCommandAuthorityObservationV1, ...],
) -> ProjectCompletionContractObservationV1:
    """Seal one complete owner snapshot after bootstrap performs CE mapping."""

    return ProjectCompletionContractObservationV1(
        contract_id=contract_id,
        contract_hash=contract_hash,
        project_id=project_id,
        run_id=run_id,
        project_kind=project_kind,
        project_kind_authority=project_kind_authority,
        pm_contract_hash=pm_contract_hash,
        covered_task_ids=covered_task_ids,
        obligations=obligations,
        completion_predicate_version=completion_predicate_version,
        verifier_policy_hash=verifier_policy_hash,
        verifier_policy_snapshot_hash=verifier_policy_snapshot_hash,
        verification_command_authority=verification_command_authority,
        _authority_token=_PROJECT_COMPLETION_CONTRACT_OBSERVATION_TOKEN,
    )


def bind_project_completion_owner_observation_port(port: ProjectCompletionOwnerObservationPortV1) -> None:
    """Bind the bootstrap owner adapter once; conflicting rebinds fail closed."""

    if not isinstance(port, ProjectCompletionOwnerObservationPortV1):
        raise ProjectCompletionOwnerObservationV1Error(
            "invalid_project_completion_owner_port",
            "Port must implement ProjectCompletionOwnerObservationPortV1",
        )
    global _project_completion_owner_observation_port
    with _owner_port_lock:
        bound = _project_completion_owner_observation_port
        if bound is None:
            _project_completion_owner_observation_port = port
            return
        if bound is not port:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_owner_port_conflicting_rebind",
                "Project completion owner port is already bound to another adapter",
            )


def _bound_owner_port() -> ProjectCompletionOwnerObservationPortV1:
    with _owner_port_lock:
        port = _project_completion_owner_observation_port
    if port is None:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_completion_owner_port_unbound",
            "Project completion owner port is not bound by process bootstrap",
        )
    return port


def observe_project_completion_owner(
    query: QueryProjectCompletionDiagnosticsV1,
) -> ProjectCompletionOwnerEvidenceBundleV1:
    """Read exact owner facts and seal them for the residual evaluator."""

    if type(query) is not QueryProjectCompletionDiagnosticsV1:
        raise ProjectCompletionOwnerObservationV1Error(
            "invalid_project_completion_query_type",
            "Query must be an exact QueryProjectCompletionDiagnosticsV1",
        )
    workspace = str(Path(query.workspace).expanduser().resolve())
    port = _bound_owner_port()
    try:
        observation = port.observe_project_completion(
            workspace=workspace,
            project_id=query.project_id,
            run_id=query.run_id,
            completion_contract_hash=query.completion_contract_hash,
        )
    except ProjectCompletionOwnerObservationV1Error:
        raise
    except Exception as exc:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_completion_owner_query_failed",
            f"Project completion owner query failed: {exc}",
        ) from exc
    if type(observation) is not ProjectCompletionOwnerObservationV1:
        raise ProjectCompletionOwnerObservationV1Error(
            "invalid_project_completion_owner_observation_type",
            "Owner port must return an exact ProjectCompletionOwnerObservationV1",
        )
    requested_identity = (workspace, query.project_id, query.run_id, query.completion_contract_hash)
    observed_identity = (
        observation.workspace,
        observation.project_id,
        observation.run_id,
        observation.completion_contract_hash,
    )
    if observed_identity != requested_identity:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_completion_owner_identity_mismatch",
            "Owner observation identity does not match the exact query",
        )
    contract = observation.contract
    if type(contract) is not ProjectCompletionContractObservationV1 or (
        contract.project_id,
        contract.run_id,
        contract.contract_hash,
    ) != (query.project_id, query.run_id, query.completion_contract_hash):
        raise ProjectCompletionOwnerObservationV1Error(
            "project_completion_contract_identity_mismatch",
            "CE completion contract does not match the exact query",
        )

    obligations = {
        item.obligation_id: item
        for group in (
            contract.obligations.artifacts,
            contract.obligations.entrypoints,
            contract.obligations.verification,
        )
        for item in group
    }
    evidence_by_id = {item.obligation_id: item for item in observation.evidence}
    for evidence in observation.evidence:
        obligation = obligations.get(evidence.obligation_id)
        if obligation is None:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_owner_unknown_obligation",
                f"Owner evidence references unknown obligation {evidence.obligation_id!r}",
            )
        evidence_identity = (
            evidence.workspace,
            evidence.project_id,
            evidence.run_id,
            evidence.completion_contract_hash,
        )
        if evidence_identity != requested_identity or evidence.owner_task_id != obligation.owner_task_id:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_owner_evidence_identity_mismatch",
                f"Owner evidence is not bound to obligation {evidence.obligation_id!r}",
            )

    for coverage in observation.repair_coverage:
        obligation = obligations.get(coverage.obligation_id)
        coverage_evidence = evidence_by_id.get(coverage.obligation_id)
        coverage_identity = (
            coverage.workspace,
            coverage.project_id,
            coverage.run_id,
            coverage.completion_contract_hash,
        )
        if obligation is None or coverage_evidence is None:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_repair_coverage_not_owner_bound",
                "Repair coverage must bind an exact failed owner-evidence obligation",
            )
        if (
            coverage_identity != requested_identity
            or coverage.owner_task_id != obligation.owner_task_id
            or coverage_evidence.status != "failed"
        ):
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_repair_coverage_not_owner_bound",
                "Repair coverage must bind an exact failed owner-evidence obligation",
            )

    bundle_hash = _canonical_hash(
        "factory.verification_guard.owner-bundle.v1",
        {
            "workspace": workspace,
            "project_id": query.project_id,
            "run_id": query.run_id,
            "completion_contract_hash": query.completion_contract_hash,
            "evidence_hashes": sorted(item.owner_evidence_hash for item in observation.evidence),
            "repair_coverage_hashes": sorted(item.evidence_hash for item in observation.repair_coverage),
        },
    )
    return ProjectCompletionOwnerEvidenceBundleV1(
        workspace=workspace,
        project_id=query.project_id,
        run_id=query.run_id,
        completion_contract_hash=query.completion_contract_hash,
        contract=contract,
        evidence=observation.evidence,
        repair_coverage=observation.repair_coverage,
        bundle_hash=bundle_hash,
        _authority_token=_PROJECT_COMPLETION_OWNER_BUNDLE_TOKEN,
    )


__all__ = [
    "bind_project_completion_owner_observation_port",
    "build_project_completion_contract_observation",
    "observe_project_completion_owner",
]
