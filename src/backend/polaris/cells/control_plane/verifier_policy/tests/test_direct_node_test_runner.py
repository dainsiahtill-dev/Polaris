"""Direct Node tests consume the same policy and physical proof owner."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy.public import (
    EvaluateVerifierCommandPolicyQueryV1,
    classify_verifier_command_profile,
    evaluate_verifier_command_policy,
    evaluate_verifier_proof,
)


def _query(workspace: Path, argv: tuple[str, ...]) -> EvaluateVerifierCommandPolicyQueryV1:
    return EvaluateVerifierCommandPolicyQueryV1(
        workspace=str(workspace),
        project_id="project-1",
        run_id="run-1",
        task_id="task-1",
        completion_contract_hash="a" * 64,
        verifier_obligation_id="verify-1",
        command_authority_hash="b" * 64,
        modality="test",
        argv=argv,
        cwd=".",
        input_obligation_ids=("artifact-1",),
    )


@pytest.mark.parametrize("case,expected", [("passing", True), ("failing", False), ("empty", False)])
def test_actual_direct_node_runner_preserves_physical_test_verdict(tmp_path: Path, case: str, expected: bool) -> None:
    assert shutil.which("node"), "real Node is required for this execution regression"
    argv: tuple[str, ...]
    if case != "empty":
        test_path = tmp_path / "tests" / "example.test.mjs"
        test_path.parent.mkdir()
        value = "2" if case == "passing" else "3"
        test_path.write_text(
            "import test from 'node:test'; import assert from 'node:assert/strict';\n"
            f"test('actual assertion', () => assert.equal(2, {value}));\n",
            encoding="utf-8",
        )
        argv = ("node", "--test", "tests/example.test.mjs")
    else:
        argv = ("node", "--test")
    assert classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "node.test_runner",
        "",
    )
    decision = evaluate_verifier_command_policy(_query(tmp_path, argv))
    assert decision.authorized is True
    assert decision.profile_id == "node.test_runner"
    assert decision.normalized_argv == argv
    assert decision.executable_hash
    assert decision.policy_decision_hash

    execution = subprocess.run(argv, cwd=tmp_path, capture_output=True, timeout=15, check=False)

    assert (
        evaluate_verifier_proof(
            profile_id=decision.profile_id,
            modality="test",
            exit_code=execution.returncode,
            timed_out=False,
            output_bytes=execution.stdout + execution.stderr,
        )
        is expected
    )


@pytest.mark.parametrize(
    "argv",
    [
        ("node", "--test", "dist/tests/*.test.js"),
        ("node", "--test", "tests/input.mjs", "tests/edge_cases.ts"),
        ("node", "--test", "--test-reporter=tap", "tests/input.mjs"),
        ("node", "--test", "--test-reporter", "tap", "tests/input.mjs"),
        ("node", "--import", "tsx", "--test", "tests/input.ts"),
        ("node", "--import=tsx", "--test", "tests/input.ts"),
        ("node", "--test", "--import", "tsx", "tests/input.ts"),
        ("node", "--test", "--import=tsx", "tests/input.ts"),
    ],
)
def test_declared_node_runner_shape_matches_runtime_policy(tmp_path: Path, argv: tuple[str, ...]) -> None:
    assert classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "node.test_runner",
        "",
    )
    assert evaluate_verifier_command_policy(_query(tmp_path, argv)).authorized is True


@pytest.mark.parametrize(
    "argv",
    [
        ("node", "--test", "-e", "process.exit(0)"),
        ("node", "-e", "process.exit(0)", "--test"),
        ("node", "--test", "--eval=process.exit(0)"),
        ("node", "--test", "--require", "tests/side_effect.mjs"),
        ("node", "--test", "--import=tests/side_effect.mjs"),
        ("node", "--test", "--watch", "tests/input.mjs"),
        ("node", "--test", "--test-reporter=tests/reporter.mjs"),
        ("node", "--test", "../foreign/input.mjs"),
        ("node", "--test", "/foreign/input.mjs"),
        ("node", "--test", "C:/foreign/input.mjs"),
        ("node", "--test", "tests/input.py"),
        ("node", "--test", "tests/input.mjs", ";", "sh"),
        ("node", "--import=tsx", "--test"),
        ("node", "--test", "--import=TSX", "tests/input.ts"),
        ("node", "--test", "--import=tsx/esm", "tests/input.ts"),
        ("node", "--test", "tests/input.ts", "--import=tsx"),
    ],
)
def test_direct_runner_rejects_execution_flags_and_foreign_operands(tmp_path: Path, argv: tuple[str, ...]) -> None:
    assert classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv)[0] == ""
    assert evaluate_verifier_command_policy(_query(tmp_path, argv)).authorized is False


@pytest.mark.parametrize(
    "output",
    [
        b"PASS\n",
        b"# pass 1\n",
        b"TAP version 13\n# tests 1\n# pass 1\n# fail 0\n",
        b"TAP version 13\nok 1 - example\n1..1\n# tests 1\n# pass 1\n# fail 1\n",
        b"TAP version 13\n1..0\n# tests 0\n# pass 0\n# fail 0\n",
    ],
)
def test_model_like_or_incomplete_tap_text_cannot_prove_direct_tests(output: bytes) -> None:
    assert (
        evaluate_verifier_proof(
            profile_id="node.test_runner", modality="test", exit_code=0, timed_out=False, output_bytes=output
        )
        is False
    )


@pytest.mark.parametrize("exit_code,timed_out", [(0, False), (1, False), (0, True)])
def test_accepted_preload_grammar_still_requires_physical_proof(exit_code: int, timed_out: bool) -> None:
    """Synthetic output checks proof semantics; this does not execute tsx."""
    assert (
        evaluate_verifier_proof(
            profile_id="node.test_runner",
            modality="test",
            exit_code=exit_code,
            timed_out=timed_out,
            output_bytes=b"",
        )
        is False
    )
