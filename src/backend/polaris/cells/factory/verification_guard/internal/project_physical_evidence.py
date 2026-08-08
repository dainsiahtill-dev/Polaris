"""Same-Cell authority for exact project physical-evidence intents."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from polaris.cells.factory.verification_guard.internal.project_completion_authority import (
    observe_project_completion_owner,
)
from polaris.cells.factory.verification_guard.public.contracts import (
    ProjectCompletionContractObservationV1,
    ProjectCompletionOwnerObservationV1Error,
    ProjectCompletionPhysicalArtifactInputV1,
    ProjectCompletionPhysicalEvidenceEffectV1,
    ProjectCompletionPhysicalEvidenceIntentV1,
    ProjectCompletionPhysicalEvidencePortV1,
    ProjectEntrypointObligationObservationV1,
    ProjectVerificationObligationObservationV1,
    QueryProjectCompletionDiagnosticsV1,
    RunProjectCompletionEvidenceCommandV1,
    RunProjectCompletionEvidenceResultV1,
)

_PROJECT_COMPLETION_PHYSICAL_INTENT_SEAL = object()
_VERIFIER_TIMEOUT_SECONDS = 300.0
_INPUT_ARTIFACT_ROLES = frozenset(
    {"source", "manifest", "test", "entrypoint", "config", "docs", "assets"}
)

_project_completion_physical_evidence_port: ProjectCompletionPhysicalEvidencePortV1 | None = None
_physical_port_lock = Lock()


def _is_project_completion_physical_intent_seal(value: object | None) -> bool:
    return value is _PROJECT_COMPLETION_PHYSICAL_INTENT_SEAL


def bind_project_completion_physical_evidence_port(port: ProjectCompletionPhysicalEvidencePortV1) -> None:
    """Bind the physical owner adapter exactly once during bootstrap."""

    if not isinstance(port, ProjectCompletionPhysicalEvidencePortV1):
        raise ProjectCompletionOwnerObservationV1Error(
            "invalid_project_completion_physical_evidence_port",
            "Port must implement ProjectCompletionPhysicalEvidencePortV1",
        )
    global _project_completion_physical_evidence_port
    with _physical_port_lock:
        current = _project_completion_physical_evidence_port
        if current is None:
            _project_completion_physical_evidence_port = port
            return
        if current is not port:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_physical_evidence_port_conflicting_rebind",
                "Project completion physical evidence port is already bound to another adapter",
            )


def _bound_physical_port() -> ProjectCompletionPhysicalEvidencePortV1:
    with _physical_port_lock:
        port = _project_completion_physical_evidence_port
    if port is None:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_completion_physical_evidence_port_unbound",
            "Project completion physical evidence port is not bound by process bootstrap",
        )
    return port


def _active_verifier_for_entrypoint(
    contract: ProjectCompletionContractObservationV1,
    entrypoint: ProjectEntrypointObligationObservationV1,
) -> ProjectVerificationObligationObservationV1:
    candidates = tuple(
        verifier
        for verifier in contract.obligations.verification
        if verifier.applicability != "not_applicable"
        and verifier.modality == "entrypoint"
        and entrypoint.obligation_id in verifier.covers_obligation_ids
        and verifier.owner_task_id == entrypoint.owner_task_id
    )
    if len(candidates) != 1:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_entrypoint_probe_authority_missing",
            "Entrypoint requires exactly one typed entrypoint verifier covering the obligation",
        )
    return candidates[0]


def _command_authority(
    contract: ProjectCompletionContractObservationV1, verifier: ProjectVerificationObligationObservationV1
):
    authority_hash = verifier.command_authority_hash
    candidates = tuple(
        item
        for item in contract.verification_command_authority
        if item.authority_hash == authority_hash
        and item.task_id == verifier.owner_task_id
        and item.modality == verifier.modality
        and item.command == verifier.command
    )
    if len(candidates) != 1:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_verifier_command_authority_missing",
            "Verifier must bind exactly one canonical PM command authority",
        )
    return candidates[0]


def _add_input(
    inputs: dict[str, ProjectCompletionPhysicalArtifactInputV1],
    *,
    obligation_id: str,
    path: str | None,
) -> None:
    if path is None:
        return
    candidate = ProjectCompletionPhysicalArtifactInputV1(obligation_id=obligation_id, path=path)
    current = inputs.get(obligation_id)
    if current is not None and current.path != candidate.path:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_verifier_input_artifact_ambiguous",
            f"Artifact input {obligation_id!r} resolves to multiple paths",
        )
    inputs[obligation_id] = candidate


def _command_inputs(
    contract: ProjectCompletionContractObservationV1,
    verifier: ProjectVerificationObligationObservationV1,
) -> tuple[ProjectCompletionPhysicalArtifactInputV1, ...]:
    artifacts = {item.obligation_id: item for item in contract.obligations.artifacts}
    entrypoints = {item.obligation_id: item for item in contract.obligations.entrypoints}
    inputs: dict[str, ProjectCompletionPhysicalArtifactInputV1] = {}
    for obligation_id in verifier.covers_obligation_ids:
        artifact = artifacts.get(obligation_id)
        if artifact is not None and artifact.applicability != "not_applicable":
            _add_input(inputs, obligation_id=artifact.obligation_id, path=artifact.path)
            continue
        entrypoint = entrypoints.get(obligation_id)
        if entrypoint is not None and entrypoint.applicability != "not_applicable":
            _add_input(inputs, obligation_id=f"{entrypoint.obligation_id}.source", path=entrypoint.source_path)
            _add_input(inputs, obligation_id=f"{entrypoint.obligation_id}.runtime", path=entrypoint.runtime_path)
    # Every verifier is bound to the whole contract-authoritative input closure,
    # not merely artifacts owned by the verifier's task.  Cross-task sources can
    # change the result of build/test/lint/entrypoint commands just as directly;
    # excluding them allowed a stale successful receipt to survive such drift.
    # That closure includes launchers, assets/fixtures, configuration, and docs:
    # any of them can alter a command's observed proof or the delivered project.
    for artifact in contract.obligations.artifacts:
        if (
            artifact.applicability != "not_applicable"
            and artifact.semantic_role in _INPUT_ARTIFACT_ROLES
        ):
            _add_input(inputs, obligation_id=artifact.obligation_id, path=artifact.path)
    if not inputs:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_verifier_input_artifacts_missing",
            "Verifier command requires at least one contract-owned artifact input",
        )
    return tuple(sorted(inputs.values(), key=lambda item: (item.obligation_id, item.path)))


def build_project_completion_physical_evidence_intent(
    contract: ProjectCompletionContractObservationV1,
    obligation_id: str,
    *,
    workspace: str,
) -> ProjectCompletionPhysicalEvidenceIntentV1:
    """Derive and seal one exact physical intent from the CE contract."""

    if type(contract) is not ProjectCompletionContractObservationV1:
        raise ProjectCompletionOwnerObservationV1Error(
            "invalid_project_completion_contract_observation_type",
            "Physical evidence requires an exact ProjectCompletionContractObservationV1",
        )
    canonical_workspace = str(Path(workspace).expanduser().resolve())
    artifact = next(
        (item for item in contract.obligations.artifacts if item.obligation_id == obligation_id),
        None,
    )
    if artifact is not None:
        if artifact.applicability == "not_applicable" or artifact.owner_task_id is None:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_obligation_not_executable",
                "Artifact obligation is not active or lacks an owner task",
            )
        return ProjectCompletionPhysicalEvidenceIntentV1(
            workspace=canonical_workspace,
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
            obligation_id=artifact.obligation_id,
            owner_task_id=artifact.owner_task_id,
            kind="artifact",
            artifact_path=artifact.path,
            modality=None,
            argv=(),
            cwd=None,
            command_authority_hash=None,
            input_artifacts=(),
            timeout_seconds=_VERIFIER_TIMEOUT_SECONDS,
            _authority_token=_PROJECT_COMPLETION_PHYSICAL_INTENT_SEAL,
        )
    verifier = next(
        (item for item in contract.obligations.verification if item.obligation_id == obligation_id),
        None,
    )
    target_obligation_id = obligation_id
    owner_task_id: str | None = None
    if verifier is None:
        entrypoint = next(
            (item for item in contract.obligations.entrypoints if item.obligation_id == obligation_id),
            None,
        )
        if entrypoint is None:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_obligation_unknown",
                f"Completion contract has no obligation {obligation_id!r}",
            )
        if entrypoint.applicability == "not_applicable" or entrypoint.owner_task_id is None:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_obligation_not_executable",
                "Entrypoint obligation is not active or lacks an owner task",
            )
        verifier = _active_verifier_for_entrypoint(contract, entrypoint)
        target_obligation_id = entrypoint.obligation_id
        owner_task_id = entrypoint.owner_task_id
    if verifier.applicability == "not_applicable" or verifier.owner_task_id is None:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_completion_obligation_not_executable",
            "Verifier obligation is not active or lacks an owner task",
        )
    authority = _command_authority(contract, verifier)
    if owner_task_id is None:
        owner_task_id = verifier.owner_task_id
    if authority.task_id != owner_task_id:
        raise ProjectCompletionOwnerObservationV1Error(
            "project_verifier_owner_task_mismatch",
            "Entrypoint/verifier/command authority must share one owner task",
        )
    return ProjectCompletionPhysicalEvidenceIntentV1(
        workspace=canonical_workspace,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        obligation_id=target_obligation_id,
        owner_task_id=owner_task_id,
        kind="command",
        artifact_path=None,
        modality=verifier.modality,
        argv=authority.argv,
        cwd=authority.cwd,
        command_authority_hash=authority.authority_hash,
        input_artifacts=_command_inputs(contract, verifier),
        timeout_seconds=_VERIFIER_TIMEOUT_SECONDS,
        _authority_token=_PROJECT_COMPLETION_PHYSICAL_INTENT_SEAL,
    )


def run_project_completion_evidence(
    command: RunProjectCompletionEvidenceCommandV1,
) -> RunProjectCompletionEvidenceResultV1:
    """Materialize one owner-derived obligation effect, without completion verdict."""

    if type(command) is not RunProjectCompletionEvidenceCommandV1:
        raise ProjectCompletionOwnerObservationV1Error(
            "invalid_project_completion_evidence_command_type",
            "Command must be an exact RunProjectCompletionEvidenceCommandV1",
        )
    workspace = str(Path(command.workspace).expanduser().resolve())
    bundle = observe_project_completion_owner(
        QueryProjectCompletionDiagnosticsV1(
            workspace=workspace,
            project_id=command.project_id,
            run_id=command.run_id,
            completion_contract_hash=command.completion_contract_hash,
        )
    )
    intent = build_project_completion_physical_evidence_intent(
        bundle.contract,
        command.obligation_id,
        workspace=workspace,
    )
    effect = _bound_physical_port().materialize_project_completion_evidence(intent)
    if type(effect) is not ProjectCompletionPhysicalEvidenceEffectV1:
        raise ProjectCompletionOwnerObservationV1Error(
            "invalid_project_completion_physical_effect_type",
            "Physical owner port must return an exact ProjectCompletionPhysicalEvidenceEffectV1",
        )
    return RunProjectCompletionEvidenceResultV1(
        code=effect.code,
        obligation_id=intent.obligation_id,
        spawned=effect.spawned,
        receipt_ref=effect.receipt_ref,
    )


__all__ = [
    "bind_project_completion_physical_evidence_port",
    "build_project_completion_physical_evidence_intent",
    "run_project_completion_evidence",
]
