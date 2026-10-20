"""Node prefix diagnostics and classification share the bounded proof grammar."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy import public as policy
from polaris.cells.control_plane.verifier_policy.tests.test_verifier_command_profile_declaration import _query


@pytest.mark.parametrize(
    "prefix",
    [
        ["--loader", "ts-node/esm"],
        ["--require", "ts-node/register"],
        ["--unknown-option"],
    ],
)
def test_preposed_node_options_remain_denied_with_exact_argument_feedback(tmp_path: Path, prefix: list[str]) -> None:
    argv = ("node", *prefix, "--test", "tests/rules.test.ts")
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality="test", argv=argv)
    assert feedback["rejected_argument"] == prefix[0]
    assert feedback["argument_index"] == 1
    assert feedback["candidate_profile_id"] == "node.test_runner"
    assert feedback["accepted_shape"].startswith("node --test ")
    assert feedback["execution_authority"] is False
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "",
        "untrusted_verifier_command",
    )
    assert not policy.evaluate_verifier_command_policy(_query(tmp_path, argv)).authorized


@pytest.mark.parametrize(
    "argv",
    [("node", "--test", "tests/rules.test.mjs"), ("node", "--test", "--test-reporter=tap", "tests/rules.test.mjs")],
)
def test_original_native_test_forms_keep_classification(tmp_path: Path, argv: tuple[str, ...]) -> None:
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "node.test_runner",
        "",
    )


@pytest.mark.parametrize(
    "prefix",
    [["--import", "tsx"], ["--import=tsx"], ["--test-reporter=tap"], ["--test-reporter", "tap"]],
)
def test_supported_prefix_feedback_never_rewrites_or_grants_execution(tmp_path: Path, prefix: list[str]) -> None:
    argv = ("node", *prefix, "--test", "tests/Rules.test.ts")
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality="test", argv=argv)
    assert feedback["profile_id"] == "node.test_runner"
    assert feedback["error_code"] == ""
    assert feedback["execution_authority"] is False
    assert "rejected_argument" not in feedback
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "node.test_runner",
        "",
    )


@pytest.mark.parametrize(
    "argv,index,reason",
    [
        (("node", "--import", "TSX", "--test", "tests/input.ts"), 1, "unsupported_preload"),
        (("node", "--test", "--import"), 2, "missing_argument_value"),
        (("node", "--import=tsx", "--test"), 1, "explicit_test_path_required"),
        (("node", "--test", "--import=tsx", "--import=tsx", "tests/input.ts"), 3, "duplicate_argument"),
        (("node", "--test", "tests/input.ts", "--import=tsx"), 3, "option_after_test_path"),
    ],
)
def test_feedback_reports_exact_shared_parser_rejection(
    tmp_path: Path, argv: tuple[str, ...], index: int, reason: str
) -> None:
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality="test", argv=argv)
    assert feedback["argument_index"] == index
    assert feedback["rejected_argument"] == argv[index]
    assert feedback["reason_code"] == reason
    assert feedback["candidate_profile_id"] == "node.test_runner"
    assert not policy.evaluate_verifier_command_policy(_query(tmp_path, argv)).authorized


def test_application_test_argument_does_not_turn_entrypoint_into_runner_feedback(tmp_path: Path) -> None:
    argv = ("node", "src/cli.js", "--test")
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality="entrypoint", argv=argv)
    assert feedback["profile_id"] == "node.file_entrypoint"
    assert feedback["error_code"] == ""
    assert "candidate_profile_id" not in feedback
    assert "rejected_argument" not in feedback
