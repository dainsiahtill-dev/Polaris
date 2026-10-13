"""Planning explanation consumes current policy grammar without granting it."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy import public as policy
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
