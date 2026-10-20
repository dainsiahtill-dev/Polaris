"""Planning classification defers physical checks and never grants execution."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy import public as policy


def _query(workspace: Path, argv: tuple[str, ...]) -> policy.EvaluateVerifierCommandPolicyQueryV1:
    return policy.EvaluateVerifierCommandPolicyQueryV1(
        workspace=str(workspace),
        project_id="project",
        run_id="run",
        task_id="task",
        completion_contract_hash="a" * 64,
        verifier_obligation_id="verify",
        command_authority_hash="b" * 64,
        modality="test",
        argv=argv,
        cwd=".",
        input_obligation_ids=("input.script",),
    )


def _registered(workspace: Path, monkeypatch: pytest.MonkeyPatch, *, pinned: bool = True) -> str:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    digest = hashlib.sha256(b"#!/bin/sh\nprintf 'approved\n'\n").hexdigest()
    row = {"id": "registered-check", "path": "scripts/check.sh", "modality": "test", "enabled": True, "required": False}
    if pinned:
        row["content_sha256"] = digest
    policy.update_verifier_policy(
        policy.UpdateVerifierPolicyCommandV1(
            workspace=str(workspace),
            custom_script_enabled=True,
            custom_scripts=(row,),
        )
    )
    return digest


def test_registered_pinned_missing_script_is_classified_but_never_authorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(tmp_path, monkeypatch)
    argv = ("scripts/check.sh",)
    assert not (tmp_path / "scripts/check.sh").exists()
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv, cwd=".") == (
        "custom_script:registered-check",
        "",
    )
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, argv))
    assert not decision.authorized
    assert decision.error_code == "custom_verifier_missing"
    assert not (tmp_path / "scripts/check.sh").exists()


def test_registered_profile_declaration_does_not_bypass_actual_content_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(tmp_path, monkeypatch)
    target = tmp_path / "scripts/check.sh"
    target.parent.mkdir()
    target.write_text("#!/bin/sh\nprintf 'changed\n'\n", encoding="utf-8")
    argv = ("scripts/check.sh",)
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "custom_script:registered-check",
        "",
    )
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, argv))
    assert not decision.authorized and decision.error_code == "custom_verifier_content_drift"


@pytest.mark.parametrize(
    "state", ["unpinned", "environment_disabled", "policy_disabled", "unregistered", "wrong_modality"]
)
def test_unavailable_custom_declaration_cannot_be_assumed_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _registered(tmp_path, monkeypatch, pinned=state != "unpinned")
    if state == "environment_disabled":
        monkeypatch.delenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED")
    if state == "policy_disabled":
        policy.update_verifier_policy(
            policy.UpdateVerifierPolicyCommandV1(workspace=str(tmp_path), custom_script_enabled=False)
        )
    argv = ("scripts/other.sh",) if state == "unregistered" else ("scripts/check.sh",)
    modality = "lint" if state == "wrong_modality" else "test"
    profile_id, error = policy.classify_verifier_command_profile(workspace=str(tmp_path), modality=modality, argv=argv)
    assert not profile_id
    assert error == ("custom_verifier_unpinned" if state == "unpinned" else "untrusted_verifier_command")


@pytest.mark.parametrize("workspace", ["", "."])
def test_inline_code_remains_untrusted_without_execution_permission(workspace: str) -> None:
    assert policy.classify_verifier_command_profile(
        workspace=workspace, modality="entrypoint", argv=("node", "-e", "require('fs').accessSync('index.html')")
    ) == ("", "untrusted_verifier_command")


def test_absent_workspace_binding_is_uncertain_for_registered_candidates() -> None:
    assert policy.classify_verifier_command_profile(workspace="", modality="test", argv=("scripts/check.sh",)) == (
        "",
        "verifier_workspace_binding_missing",
    )
    assert policy.classify_verifier_command_profile(workspace="", modality="test", argv=("pytest", "-q")) == (
        "python.pytest",
        "",
    )


def test_registered_current_pinned_script_retains_runtime_profile_admissibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(tmp_path, monkeypatch)
    target = tmp_path / "scripts/check.sh"
    target.parent.mkdir()
    target.write_bytes(b"#!/bin/sh\nprintf 'approved\n'\n")
    target.chmod(0o755)
    argv = ("scripts/check.sh",)
    declaration = policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv)
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, argv))
    assert declaration == ("custom_script:registered-check", "")
    assert decision.authorized
    assert decision.profile_id == declaration[0]
    assert decision.normalized_argv == argv


@pytest.mark.parametrize("operand", ["--eval=process.exit(0);void x.js", "--eval=process.exit(0).mjs"])
def test_node_flag_ending_in_source_extension_is_never_a_file_entrypoint(tmp_path: Path, operand: str) -> None:
    argv = ("node", operand)
    for workspace in ("", str(tmp_path)):
        assert policy.classify_verifier_command_profile(workspace=workspace, modality="entrypoint", argv=argv) == (
            "",
            "untrusted_verifier_command",
        )
    decision = policy.evaluate_verifier_command_policy(replace(_query(tmp_path, argv), modality="entrypoint"))
    assert not decision.authorized
    assert decision.error_code == "untrusted_verifier_command"
    assert not decision.policy_decision_hash


def test_registered_flag_path_cannot_lend_file_pin_to_interpreter_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    operand = "--eval=process.exit(0);void x.js"
    body = "console.log('registered file');\n"
    (tmp_path / operand).write_text(body, encoding="utf-8")
    policy.update_verifier_policy(
        policy.UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            custom_script_enabled=True,
            custom_scripts=(
                {
                    "id": "flag-file",
                    "path": operand,
                    "modality": "test",
                    "enabled": True,
                    "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                },
            ),
        )
    )
    argv = ("node", operand)
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "",
        "untrusted_verifier_command",
    )
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, argv))
    assert not decision.authorized
    assert decision.error_code == "untrusted_verifier_command"


def test_explicit_relative_hyphen_filename_keeps_file_entrypoint_and_registered_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    body = "console.log('hyphen file');\n"
    (tmp_path / "-file.js").write_text(body, encoding="utf-8")
    argv = ("node", "./-file.js")
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="entrypoint", argv=argv) == (
        "node.file_entrypoint",
        "",
    )
    assert policy.evaluate_verifier_command_policy(replace(_query(tmp_path, argv), modality="entrypoint")).authorized
    policy.update_verifier_policy(
        policy.UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            custom_script_enabled=True,
            custom_scripts=(
                {
                    "id": "hyphen-file",
                    "path": "-file.js",
                    "modality": "test",
                    "enabled": True,
                    "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                },
            ),
        )
    )
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "custom_script:hyphen-file",
        "",
    )
    assert policy.evaluate_verifier_command_policy(_query(tmp_path, argv)).authorized


def test_nonproving_package_test_keeps_denial_and_current_confined_source_witness(tmp_path: Path) -> None:
    body = '{"scripts":{"test":"echo verification is not configured"}}\n'
    (tmp_path / "package.json").write_text(body, encoding="utf-8")
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, ("npm", "test")))
    assert decision.authorized is False
    assert decision.error_code == "non_proving_package_script"
    assert decision.policy_decision_hash == ""
    assert dict(decision.artifact_failure_metadata) == {
        "schema_version": "verifier.artifact_admission_failure.v1",
        "failure_domain": "artifact_profile",
        "cause": "package_test_script_not_proving",
        "source_path": "package.json",
        "source_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "source_snapshot_kind": "confined_readable_regular_utf8",
    }
    assert type(decision.artifact_failure_metadata) is tuple


@pytest.mark.parametrize(
    "script",
    [
        "echo no runner && exit 0",
        "echo 'unterminated",
        "node --eval=process.exit(0)",
        "node --test --watch",
        "node --unknown-loader tests/main.js",
    ],
)
def test_unsafe_unknown_or_malformed_package_denial_never_emits_artifact_feedback(tmp_path: Path, script: str) -> None:
    import json

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": script}}), encoding="utf-8")
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, ("npm", "test")))
    assert decision.authorized is False and decision.artifact_failure_metadata == ()


def test_package_failure_metadata_cannot_be_mutable_or_retagged_as_authorized(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"echo no runner"}}', encoding="utf-8")
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, ("npm", "test")))
    assert decision.artifact_failure_metadata
    with pytest.raises((TypeError, ValueError)):
        replace(decision, authorized=True)
    with pytest.raises((TypeError, ValueError)):
        replace(decision, artifact_failure_metadata=list(decision.artifact_failure_metadata))
    with pytest.raises((TypeError, ValueError)):
        replace(decision, artifact_failure_metadata=(*decision.artifact_failure_metadata, ("cause", "other")))
    with pytest.raises((TypeError, ValueError)):
        replace(
            decision, artifact_failure_metadata=(*decision.artifact_failure_metadata, ("execution_authority", "true"))
        )


def test_linked_manifest_never_gets_actionable_artifact_witness(tmp_path: Path) -> None:
    external = tmp_path.parent / (tmp_path.name + "-external-package.json")
    external.write_text('{"scripts":{"test":"echo no runner"}}', encoding="utf-8")
    (tmp_path / "package.json").symlink_to(external)
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, ("npm", "test")))
    assert decision.authorized is False and decision.artifact_failure_metadata == ()
