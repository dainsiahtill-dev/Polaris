"""Deterministic evaluator for CE-owned project-completion obligations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from polaris.cells.factory.verification_guard.public.contracts import (
    _PROJECT_COMPLETION_DIAGNOSTICS_AUTHORITY_TOKEN,
    CompletionEvidenceStateV1,
    CompletionNextActionV1,
    CompletionRetryClassV1,
    ProjectArtifactObligationObservationV1,
    ProjectCompletionDiagnosticsV1,
    ProjectCompletionDiagnosticV1,
    ProjectCompletionEvidenceV1,
    ProjectCompletionOwnerEvidenceBundleV1,
    ProjectEntrypointObligationObservationV1,
    ProjectRepairCoverageV1,
    ProjectVerificationObligationObservationV1,
)

_DIRECTOR_MODULE_ID = "director.runtime"
_VERIFIER_MODULE_ID = "control_plane.run_ledger"


@dataclass(frozen=True)
class _ResidualSeed:
    obligation_id: str
    owner_task_id: str
    kind: str
    affected_target: str
    evidence_state: CompletionEvidenceStateV1
    archetype: str
    evidence: ProjectCompletionEvidenceV1 | None
    coverage: ProjectRepairCoverageV1 | None


def _diagnostic_id(*, contract_hash: str, obligation_id: str, archetype: str) -> str:
    seed = f"{contract_hash}\x00{obligation_id}\x00{archetype}".encode("utf-8")  # noqa: UP012
    return f"project-diagnostic-{hashlib.sha256(seed).hexdigest()[:24]}"


def _required(item: object) -> bool:
    return getattr(item, "applicability", None) == "required"


def _affected_target(item: object) -> str:
    if isinstance(item, ProjectArtifactObligationObservationV1):
        return item.path
    if isinstance(item, ProjectEntrypointObligationObservationV1):
        return item.runtime_path or item.source_path or item.command or item.obligation_id
    if isinstance(item, ProjectVerificationObligationObservationV1):
        return item.command or item.modality
    raise TypeError("unsupported project completion obligation")


def _kind(item: object) -> str:
    if isinstance(item, ProjectArtifactObligationObservationV1):
        return "artifact"
    if isinstance(item, ProjectEntrypointObligationObservationV1):
        return "entrypoint"
    if isinstance(item, ProjectVerificationObligationObservationV1):
        return "verification"
    raise TypeError("unsupported project completion obligation")


def _residual_seed(
    *,
    item: (
        ProjectArtifactObligationObservationV1
        | ProjectEntrypointObligationObservationV1
        | ProjectVerificationObligationObservationV1
    ),
    contract_hash: str,
    evidence: ProjectCompletionEvidenceV1 | None,
    coverage: ProjectRepairCoverageV1 | None,
) -> _ResidualSeed | None:
    kind = _kind(item)

    def build_seed(
        *,
        evidence_state: CompletionEvidenceStateV1,
        archetype: str,
    ) -> _ResidualSeed:
        return _ResidualSeed(
            obligation_id=item.obligation_id,
            owner_task_id=str(item.owner_task_id or ""),
            kind=kind,
            affected_target=_affected_target(item),
            evidence_state=evidence_state,
            archetype=archetype,
            evidence=evidence,
            coverage=coverage,
        )

    if evidence is None:
        return build_seed(
            evidence_state="missing",
            archetype=f"missing_required_{kind}",
        )
    if evidence.completion_contract_hash != contract_hash:
        return build_seed(
            evidence_state="missing",
            archetype="owner_evidence_contract_mismatch",
        )
    if isinstance(item, ProjectVerificationObligationObservationV1):
        if evidence.verifier_receipt_ref is None or evidence.verifier_exit_code is None:
            return build_seed(
                evidence_state="missing",
                archetype="missing_verifier_receipt",
            )
        # Entrypoint readiness probes are long-running by design.  The
        # ExecutionBroker first proves readiness, then terminates the process
        # under platform control; that produces an expected negative exit code
        # while the authoritative receipt status remains ``passed``.  Preserve
        # the non-zero guard for every ordinary verifier, but do not overwrite
        # a readiness-proven entrypoint receipt with a contradictory failure.
        unexpected_nonzero_exit = evidence.verifier_exit_code != 0 and item.modality != "entrypoint"
        if evidence.status == "failed" or unexpected_nonzero_exit:
            return build_seed(
                evidence_state="failed",
                archetype="failed_required_verification",
            )
        return None
    if evidence.status == "failed":
        return build_seed(
            evidence_state="failed",
            archetype=f"failed_required_{kind}",
        )
    return None


def _retry_policy(
    seed: _ResidualSeed,
    *,
    dependency_ids: tuple[str, ...],
) -> tuple[CompletionRetryClassV1, CompletionNextActionV1]:
    if dependency_ids:
        return "dependency_blocked", "wait_for_dependencies"
    if seed.archetype in {"owner_evidence_contract_mismatch", "missing_verifier_receipt"}:
        return "control_plane_reconcile", "refresh_owner_evidence"
    if seed.evidence_state == "missing" and seed.kind == "verification":
        return "verification_evidence", "run_required_verifier"
    if (
        seed.evidence_state == "failed"
        and seed.coverage is not None
        and seed.coverage.completion_contract_hash
        == (seed.evidence.completion_contract_hash if seed.evidence is not None else "")
        and seed.coverage.status == "executable_runtime"
    ):
        return "deterministic_repair", "run_deterministic_repair"
    return "owner_rework", "publish_owner_rework"


def evaluate_project_completion_bundle(
    bundle: ProjectCompletionOwnerEvidenceBundleV1,
) -> ProjectCompletionDiagnosticsV1:
    """Evaluate all required obligations from one same-Cell-sealed bundle."""

    if type(bundle) is not ProjectCompletionOwnerEvidenceBundleV1:
        raise TypeError("bundle must be an exact ProjectCompletionOwnerEvidenceBundleV1")
    contract = bundle.contract
    evidence_by_id = {item.obligation_id: item for item in bundle.evidence}
    coverage_by_id = {item.obligation_id: item for item in bundle.repair_coverage}
    required_items = tuple(
        item
        for group in (
            contract.obligations.artifacts,
            contract.obligations.entrypoints,
            contract.obligations.verification,
        )
        for item in group
        if _required(item)
    )

    residuals: list[_ResidualSeed] = []
    passed_ids: list[str] = []
    for item in required_items:
        seed = _residual_seed(
            item=item,
            contract_hash=contract.contract_hash,
            evidence=evidence_by_id.get(item.obligation_id),
            coverage=coverage_by_id.get(item.obligation_id),
        )
        if seed is None:
            passed_ids.append(item.obligation_id)
        else:
            residuals.append(seed)

    diagnostic_id_by_obligation = {
        seed.obligation_id: _diagnostic_id(
            contract_hash=contract.contract_hash,
            obligation_id=seed.obligation_id,
            archetype=seed.archetype,
        )
        for seed in residuals
    }
    artifact_by_path = {item.path: item.obligation_id for item in contract.obligations.artifacts}
    required_verifiers_by_covered_id: dict[str, list[str]] = {}
    for verifier in contract.obligations.verification:
        if not _required(verifier):
            continue
        for covered_id in verifier.covers_obligation_ids:
            required_verifiers_by_covered_id.setdefault(covered_id, []).append(verifier.obligation_id)

    diagnostics: list[ProjectCompletionDiagnosticV1] = []
    for seed in residuals:
        obligation = next(item for item in required_items if item.obligation_id == seed.obligation_id)
        dependency_obligation_ids: tuple[str, ...] = ()
        required_verifier_ids: tuple[str, ...]
        if isinstance(obligation, ProjectEntrypointObligationObservationV1):
            dependency_obligation_ids = tuple(
                artifact_by_path[path]
                for path in (obligation.source_path, obligation.runtime_path)
                if path is not None and artifact_by_path.get(path) in diagnostic_id_by_obligation
            )
            required_verifier_ids = tuple(required_verifiers_by_covered_id.get(obligation.obligation_id, ()))
        elif isinstance(obligation, ProjectVerificationObligationObservationV1):
            dependency_obligation_ids = tuple(
                obligation_id
                for obligation_id in obligation.covers_obligation_ids
                if obligation_id in diagnostic_id_by_obligation
            )
            required_verifier_ids = (obligation.obligation_id,)
        else:
            required_verifier_ids = tuple(required_verifiers_by_covered_id.get(obligation.obligation_id, ()))
        dependency_ids = tuple(
            diagnostic_id_by_obligation[obligation_id] for obligation_id in dependency_obligation_ids
        )
        retry_class, allowed_next_action = _retry_policy(
            seed,
            dependency_ids=dependency_ids,
        )
        current_coverage = (
            seed.coverage
            if seed.coverage is not None and seed.coverage.completion_contract_hash == contract.contract_hash
            else None
        )
        diagnostics.append(
            ProjectCompletionDiagnosticV1(
                diagnostic_id=diagnostic_id_by_obligation[seed.obligation_id],
                archetype=seed.archetype,
                evidence_state=seed.evidence_state,
                primary_module_id=(
                    seed.evidence.owner_module_id
                    if seed.evidence is not None
                    else (_VERIFIER_MODULE_ID if seed.kind == "verification" else _DIRECTOR_MODULE_ID)
                ),
                obligation_id=seed.obligation_id,
                owner_task_id=seed.owner_task_id,
                affected_target=seed.affected_target,
                owner_evidence_refs=(
                    (
                        *seed.evidence.owner_evidence_refs,
                        *((seed.evidence.verifier_receipt_ref,) if seed.evidence.verifier_receipt_ref else ()),
                    )
                    if seed.evidence is not None
                    else ()
                ),
                retry_class=retry_class,
                allowed_next_action=allowed_next_action,
                dependency_ids=dependency_ids,
                repair_coverage=(current_coverage.status if current_coverage else "unknown"),
                repair_source_tool=(current_coverage.source_tool if current_coverage else None),
                repair_coverage_evidence_ref=(current_coverage.evidence_ref if current_coverage else None),
                repair_coverage_evidence_hash=(current_coverage.evidence_hash if current_coverage else None),
                required_verifier_ids=required_verifier_ids,
            )
        )

    missing_ids = tuple(seed.obligation_id for seed in residuals if seed.evidence_state == "missing")
    failed_ids = tuple(seed.obligation_id for seed in residuals if seed.evidence_state == "failed")
    non_blocking_ids = tuple(
        item.obligation_id
        for group in (
            contract.obligations.artifacts,
            contract.obligations.entrypoints,
            contract.obligations.verification,
        )
        for item in group
        if not _required(item)
    )
    return ProjectCompletionDiagnosticsV1(
        workspace=bundle.workspace,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        owner_bundle_hash=bundle.bundle_hash,
        diagnostics=tuple(diagnostics),
        passed_obligation_ids=tuple(passed_ids),
        missing_obligation_ids=missing_ids,
        failed_obligation_ids=failed_ids,
        non_blocking_obligation_ids=non_blocking_ids,
        _authority_token=_PROJECT_COMPLETION_DIAGNOSTICS_AUTHORITY_TOKEN,
    )


__all__ = ["evaluate_project_completion_bundle"]
