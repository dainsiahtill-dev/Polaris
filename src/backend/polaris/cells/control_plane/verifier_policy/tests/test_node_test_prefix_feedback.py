"""Node test prefix diagnostics explain proposals without admitting them."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy import public as policy
from polaris.cells.control_plane.verifier_policy.tests.test_verifier_command_profile_declaration import _query


@pytest.mark.parametrize(
    "prefix",
    [
        ["--import", "tsx"],
        ["--import=tsx"],
        ["--loader", "ts-node/esm"],
        ["--require", "ts-node/register"],
        ["--test-reporter=tap"],
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


def test_application_test_argument_does_not_turn_entrypoint_into_runner_feedback(tmp_path: Path) -> None:
    argv = ("node", "src/cli.js", "--test")
    feedback = policy.get_verifier_command_profile_feedback(workspace=str(tmp_path), modality="entrypoint", argv=argv)
    assert feedback["profile_id"] == "node.file_entrypoint"
    assert feedback["error_code"] == ""
    assert "candidate_profile_id" not in feedback
    assert "rejected_argument" not in feedback
