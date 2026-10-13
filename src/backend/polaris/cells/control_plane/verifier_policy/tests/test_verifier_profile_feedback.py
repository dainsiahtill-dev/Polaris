"""Planning explanation consumes current policy grammar without granting it."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy import public as policy
from polaris.cells.control_plane.verifier_policy.public.contracts import VerifierCommandModalityV1
from polaris.cells.control_plane.verifier_policy.tests.test_verifier_command_profile_declaration import (
    _query,
    _registered,
)


def test_rejected_runner_argument_has_owner_grammar_feedback_and_stays_denied(tmp_path: Path) -> None:
    argv = ["node", "--test", "--import", "ts-node/register", "tests/rules.test.ts"]
    before = deepcopy(argv)
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality="test", argv=argv)
    assert feedback["error_code"] == "untrusted_verifier_command"
    assert feedback["rejected_argument"] == "--import"
    assert feedback["argument_index"] == 2
    assert feedback["candidate_profile_id"] == "node.test_runner"
    assert "--test-reporter=tap" in feedback["accepted_shape"]
    assert feedback["guidance_only"] is True
    assert feedback["execution_authority"] is False
    assert argv == before
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "",
        "untrusted_verifier_command",
    )
    assert not policy.evaluate_verifier_command_policy(_query(tmp_path, tuple(argv))).authorized


@pytest.mark.parametrize(
    "argv",
    [
        ("node", "--test", "tests/rules.test.mjs"),
        ("node", "--test", "--test-reporter=tap", "tests/*.test.js"),
        ("node", "--test", "--test-reporter", "tap", "tests/rules.test.ts"),
    ],
)
def test_same_runner_grammar_accepts_declared_shape_without_repair_or_permission(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality="test", argv=argv)
    assert feedback["profile_id"] == "node.test_runner"
    assert feedback["error_code"] == ""
    assert "rejected_argument" not in feedback
    assert feedback["execution_authority"] is False
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "node.test_runner",
        "",
    )


def test_registered_pinned_custom_declaration_is_not_reclassified_by_builtin_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(tmp_path, monkeypatch)
    argv = ("scripts/check.sh",)
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality="test", argv=argv)
    assert feedback["profile_id"] == "custom_script:registered-check"
    assert feedback["error_code"] == ""
    assert "rejected_argument" not in feedback
    assert feedback["execution_authority"] is False
    assert not (tmp_path / "scripts/check.sh").exists()
    assert policy.evaluate_verifier_command_policy(_query(tmp_path, argv)).error_code == "custom_verifier_missing"


@pytest.mark.parametrize(
    "modality,argv,expected_profile,expected_argv",
    [
        ("environment_prep", ("install",), "node.package_install", ["npm", "install"]),
        ("test", ("npm", "run", "test:check"), "node.script_test", ["npm", "run", "test"]),
        ("entrypoint", ("npx", "http-server", "dist"), "node.script_entrypoint", ["npm", "run", "serve"]),
    ],
)
def test_unknown_recipe_gets_classifiable_full_argv_guidance_without_authority(
    tmp_path: Path,
    modality: VerifierCommandModalityV1,
    argv: tuple[str, ...],
    expected_profile: str,
    expected_argv: list[str],
) -> None:
    before = deepcopy(argv)
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality=modality, argv=argv)
    assert feedback["profile_id"] == ""
    assert feedback["error_code"] == "untrusted_verifier_command"
    candidates = feedback["candidate_profiles"]
    candidate = next(item for item in candidates if item["candidate_profile_id"] == expected_profile)
    assert candidate["expected_modality"] == modality
    assert candidate["accepted_shape"]
    assert expected_argv in candidate["argv_examples"]
    assert feedback["guidance_only"] is True
    assert feedback["execution_authority"] is False
    assert argv == before
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality=modality, argv=argv) == (
        "",
        "untrusted_verifier_command",
    )
    assert not policy.evaluate_verifier_command_policy(replace(_query(tmp_path, argv), modality=modality)).authorized
    assert not (tmp_path / ".polaris/verifier_policy.json").exists()


@pytest.mark.parametrize("modality", ["environment_prep", "build", "test", "lint", "entrypoint"])
def test_every_advertised_example_reclassifies_as_its_profile(tmp_path: Path, modality: str) -> None:
    feedback = policy.get_verifier_command_profile_feedback(
        workspace=str(tmp_path), modality=modality, argv=("unknown-command",)
    )
    assert feedback["candidate_profiles"]
    for candidate in feedback["candidate_profiles"]:
        assert candidate["expected_modality"] == modality
        assert candidate["argv_examples"]
        for argv in candidate["argv_examples"]:
            assert isinstance(argv, list) and argv and argv[0]
            assert policy.classify_verifier_command_profile(
                workspace=str(tmp_path), modality=candidate["expected_modality"], argv=argv
            ) == (candidate["candidate_profile_id"], "")


@pytest.mark.parametrize(
    "modality,argv",
    [
        ("test", ("sh", "-c", "npm test")),
        ("test", ("npm", "run", "arbitrary-script")),
        ("build", ("npx", "--yes", "-p", "typescript", "tsc")),
        ("entrypoint", ("node", "../foreign/cli.js")),
        ("test", ("node", "--test", "/foreign/test.js")),
        ("lint", ("ruff", "check", "--fix", ".")),
    ],
)
def test_guidance_does_not_accept_shell_foreign_unknown_or_mutating_proposals(
    tmp_path: Path, modality: VerifierCommandModalityV1, argv: tuple[str, ...]
) -> None:
    before = policy.classify_verifier_command_profile(workspace=str(tmp_path), modality=modality, argv=argv)
    assert before[0] == ""
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality=modality, argv=argv)
    assert (feedback["profile_id"], feedback["error_code"]) == before
    assert feedback["candidate_profiles"]
    assert feedback["execution_authority"] is False
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality=modality, argv=argv) == before
    assert not policy.evaluate_verifier_command_policy(replace(_query(tmp_path, argv), modality=modality)).authorized


@pytest.mark.parametrize(
    "modality,argv,expected_modality,expected_profile",
    [
        ("test", ("npm", "install"), "environment_prep", "node.package_install"),
        ("test", ("npx", "tsc", "--noEmit"), "lint", "node.typescript_check"),
        ("build", ("python", "-m", "pytest"), "test", "python.pytest"),
    ],
)
def test_mismatch_feedback_identifies_current_argv_modality_without_accepting_requested_modality(
    tmp_path: Path, modality: str, argv: tuple[str, ...], expected_modality: str, expected_profile: str
) -> None:
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality=modality, argv=argv)
    assert feedback["error_code"] == "verifier_modality_mismatch"
    assert feedback["candidate_profile_id"] == expected_profile
    assert feedback["expected_modality"] == expected_modality
    assert list(argv) in feedback["argv_examples"]
    assert feedback["execution_authority"] is False
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality=modality, argv=argv) == (
        "",
        "verifier_modality_mismatch",
    )
