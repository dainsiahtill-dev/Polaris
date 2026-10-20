"""Admission feedback carries no command capability or physical evidence seal."""

from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest
from polaris.cells.factory.verification_guard import public as guard


def _observation(workspace: Path):
    return guard.ProjectVerificationAdmissionObservationV1(
        workspace=str(workspace),
        project_id="project",
        run_id="run",
        completion_contract_hash="a" * 64,
        obligation_id="verify.test",
        owner_task_id="task",
        modality="test",
        argv=("npm", "test"),
        cwd=".",
        command_authority_hash="b" * 64,
        policy_error_code="non_proving_package_script",
        failure_domain="artifact_profile",
        cause="package_test_script_not_proving",
        source_path="package.json",
        source_sha256="c" * 64,
        source_snapshot_kind="confined_readable_regular_utf8",
    )


def test_error_compatibility_and_immutable_non_authority_observation(tmp_path: Path) -> None:
    old = guard.ProjectCompletionOwnerObservationV1Error("rejected", "original message")
    assert old.error_code == "rejected" and str(old) == "original message"
    assert old.admission_failure is None
    observation = _observation(tmp_path)
    error = guard.ProjectCompletionOwnerObservationV1Error(
        "rejected", "original message", admission_failure=observation
    )
    assert error.admission_failure is observation
    assert observation.authorized is observation.execution_started is observation.execution_authority is False
    assert observation.authoritative_receipt is False and observation.receipt_ref is None
    with pytest.raises(FrozenInstanceError):
        observation.source_sha256 = "d" * 64
    with pytest.raises((TypeError, ValueError)):
        guard.ProjectCompletionOwnerObservationV1Error("rejected", "message", admission_failure=asdict(observation))
    with pytest.raises((TypeError, ValueError)):
        guard.ProjectVerificationAdmissionObservationV1(**asdict(observation), execution_authority=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_path", "../package.json"),
        ("source_path", "/package.json"),
        ("source_path", "src/./package.json"),
        ("source_path", "src\\package.json"),
        ("source_sha256", "not-a-hash"),
        ("cwd", "../outside"),
        ("argv", ["npm", "test"]),
        ("policy_error_code", "unknown_failure"),
        ("failure_domain", "security"),
    ],
)
def test_admission_observation_rejects_unbound_or_mutable_values(tmp_path: Path, field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(_observation(tmp_path), **{field: value})


def test_admission_carrier_rejects_string_subclasses_with_overridable_identity(tmp_path: Path) -> None:
    class UntrustedString(str):
        pass

    with pytest.raises((TypeError, ValueError)):
        replace(_observation(tmp_path), source_sha256=UntrustedString("c" * 64))
