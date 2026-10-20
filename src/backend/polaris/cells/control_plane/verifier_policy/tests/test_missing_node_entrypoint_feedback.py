"""Synthetic module fixtures: denied entrypoints produce feedback, never effects."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy.public import (
    EvaluateVerifierCommandPolicyQueryV1,
    evaluate_verifier_command_policy,
)


def _query(workspace: Path) -> EvaluateVerifierCommandPolicyQueryV1:
    return EvaluateVerifierCommandPolicyQueryV1(
        workspace=str(workspace),
        project_id="synthetic-entrypoint",
        run_id="run",
        task_id="TASK-1",
        completion_contract_hash="a" * 64,
        verifier_obligation_id="verify-entrypoint",
        command_authority_hash="b" * 64,
        modality="entrypoint",
        argv=("npm", "run", "start", "--", "--ticks=20"),
        cwd=".",
        input_obligation_ids=("manifest", "source", "config"),
    )


def _manifest(workspace: Path, script: str) -> bytes:
    body = (json.dumps({"scripts": {"start": script}}) + "\n").encode("utf-8")
    (workspace / "package.json").write_bytes(body)
    return body


def test_missing_safe_node_entrypoint_keeps_denial_with_current_manifest_feedback(tmp_path: Path) -> None:
    body = _manifest(tmp_path, "node dist/cli/main.js")
    decision = evaluate_verifier_command_policy(_query(tmp_path))

    assert decision.authorized is False
    assert decision.error_code == "untrusted_package_script"
    assert dict(decision.artifact_failure_metadata) == {
        "schema_version": "verifier.artifact_admission_failure.v1",
        "failure_domain": "artifact_profile",
        "cause": "package_entrypoint_target_missing",
        "source_path": "package.json",
        "source_sha256": hashlib.sha256(body).hexdigest(),
        "source_snapshot_kind": "confined_readable_regular_utf8",
        "target_path": "dist/cli/main.js",
    }
    assert not (tmp_path / "dist").exists()


@pytest.mark.parametrize(
    "script",
    [
        "node --require ./preload.cjs dist/cli/main.js",
        "node --import=./preload.mjs dist/cli/main.js",
        "node --watch dist/cli/main.js",
        "node -e 'console.log(1)'",
        "node ../outside.js",
        "node /tmp/outside.js",
        "node dist/cli/main.js && echo passed",
        "node dist/cli/main.js || true",
        "node dist/cli/main.js --watch",
    ],
)
def test_unsafe_or_unknown_entrypoint_denial_never_becomes_artifact_repair_feedback(
    tmp_path: Path, script: str
) -> None:
    _manifest(tmp_path, script)
    decision = evaluate_verifier_command_policy(_query(tmp_path))
    assert decision.authorized is False
    assert decision.artifact_failure_metadata == ()


@pytest.mark.parametrize("runner", ["/tmp/node", "./node", "tools/node"])
def test_untrusted_node_runner_paths_never_become_missing_target_feedback(tmp_path: Path, runner: str) -> None:
    _manifest(tmp_path, f"{runner} dist/cli/main.js")
    decision = evaluate_verifier_command_policy(_query(tmp_path))
    assert decision.authorized is False
    assert decision.artifact_failure_metadata == ()


@pytest.mark.parametrize("link_kind", ["target", "ancestor", "manifest"])
def test_missing_entrypoint_through_symlink_has_no_artifact_feedback(tmp_path: Path, link_kind: str) -> None:
    _manifest(tmp_path, "node dist/cli/main.js")
    outside = tmp_path / "outside"
    outside.mkdir()
    if link_kind == "target":
        (tmp_path / "dist/cli").mkdir(parents=True)
        (tmp_path / "dist/cli/main.js").symlink_to(outside / "missing.js")
    elif link_kind == "ancestor":
        (tmp_path / "dist").symlink_to(outside, target_is_directory=True)
    else:
        manifest = tmp_path / "package.json"
        body = manifest.read_text(encoding="utf-8")
        manifest.unlink()
        (outside / "package.json").write_text(body, encoding="utf-8")
        manifest.symlink_to(outside / "package.json")
    decision = evaluate_verifier_command_policy(_query(tmp_path))
    assert decision.authorized is False
    assert decision.artifact_failure_metadata == ()


def test_missing_entrypoint_does_not_become_feedback_when_manifest_snapshot_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

    _manifest(tmp_path, "node dist/cli/main.js")
    monkeypatch.setattr(WorkspaceReadSnapshot, "drift_reasons", lambda _self: ("source_snapshot_drift",))
    decision = evaluate_verifier_command_policy(_query(tmp_path))
    assert decision.authorized is False
    assert decision.artifact_failure_metadata == ()


def test_unknown_target_observation_never_claims_missing_artifact_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

    _manifest(tmp_path, "node dist/cli/main.js")
    confined = WorkspaceReadSnapshot.confined_path

    def unknown(self, path):
        if path.name == "main.js":
            raise PermissionError("Synthetic unknown target state")
        return confined(self, path)

    monkeypatch.setattr(WorkspaceReadSnapshot, "confined_path", unknown)
    decision = evaluate_verifier_command_policy(_query(tmp_path))
    assert decision.authorized is False
    assert decision.artifact_failure_metadata == ()
