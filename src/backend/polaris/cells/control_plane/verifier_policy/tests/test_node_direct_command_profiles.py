"""Normal Node entrypoints and TypeScript checks need no package-script wrapper."""

import json
import subprocess
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy.public import (
    EvaluateVerifierCommandPolicyQueryV1,
    evaluate_verifier_command_policy,
)


def _query(workspace: Path, modality: str, argv: tuple[str, ...]) -> EvaluateVerifierCommandPolicyQueryV1:
    return EvaluateVerifierCommandPolicyQueryV1(
        workspace=str(workspace),
        project_id="project-1",
        run_id="run-1",
        task_id="task-1",
        completion_contract_hash="a" * 64,
        verifier_obligation_id="verify-1",
        command_authority_hash="b" * 64,
        modality=modality,
        argv=argv,
        cwd=".",
        input_obligation_ids=("artifact-1",),
    )


@pytest.mark.parametrize("extension", ["js", "mjs", "cjs"])
def test_node_file_entrypoint_accepts_application_arguments(tmp_path: Path, extension: str) -> None:
    entry = tmp_path / "dist" / "entry" / f"cli.{extension}"
    entry.parent.mkdir(parents=True)
    entry.write_text("console.log('ready');\n", encoding="utf-8")
    argv = ("node", f"dist/entry/cli.{extension}", "--frames", "12")

    decision = evaluate_verifier_command_policy(_query(tmp_path, "entrypoint", argv))

    assert decision.authorized is True
    assert decision.error_code == ""
    assert decision.profile_id == "node.file_entrypoint"
    assert decision.normalized_argv == argv
    assert decision.policy_decision_hash


@pytest.mark.parametrize("argv", [("npx", "tsc", "--noEmit"), ("npx", "tsc", "--noEmit", "--pretty", "false")])
def test_typescript_readonly_check_is_a_lint_command(tmp_path: Path, argv: tuple[str, ...]) -> None:
    decision = evaluate_verifier_command_policy(_query(tmp_path, "lint", argv))

    assert decision.authorized is True
    assert decision.profile_id == "node.typescript_check"


@pytest.mark.parametrize("argv", [("npx", "tsc"), ("npx", "tsc", "-p", "tsconfig.json")])
def test_direct_typescript_compilation_is_a_build_command(tmp_path: Path, argv: tuple[str, ...]) -> None:
    decision = evaluate_verifier_command_policy(_query(tmp_path, "build", argv))

    assert decision.authorized is True
    assert decision.profile_id == "node.typescript_build"


@pytest.mark.parametrize(
    "modality,argv",
    [
        ("entrypoint", ("node", "-e", "process.exit(0)")),
        ("entrypoint", ("node", "../foreign/cli.js")),
        ("entrypoint", ("node", "/foreign/cli.js")),
        ("entrypoint", ("node", "dist/cli.js", "--watch")),
        ("test", ("npx", "tsc", "--noEmit")),
        ("lint", ("npx", "tsc")),
    ],
)
def test_direct_node_profiles_do_not_admit_foreign_or_nonproving_commands(
    tmp_path: Path, modality: str, argv: tuple[str, ...]
) -> None:
    decision = evaluate_verifier_command_policy(_query(tmp_path, modality, argv))

    assert decision.authorized is False
    assert decision.policy_decision_hash == ""


@pytest.mark.parametrize(
    "modality,script",
    [
        ("test", "node --test tests/*.test.mjs"),
        ("test", "tsc --noEmit && tsc -p tsconfig.json && node --test dist/tests/*.test.js"),
        ("test", "tsc&&node --test dist/tests/*.test.js"),
        ("entrypoint", "node dist/entry/cli.js --frames 12"),
    ],
)
def test_package_script_accepts_build_then_test_and_entrypoint_arguments(
    tmp_path: Path, modality: str, script: str
) -> None:
    entry = tmp_path / "dist/entry/cli.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("console.log('ready');\n", encoding="utf-8")
    script_name = "test" if modality == "test" else "start"
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {script_name: script}}), encoding="utf-8")

    decision = evaluate_verifier_command_policy(_query(tmp_path, modality, ("npm", "run", script_name)))

    assert decision.authorized is True
    assert decision.error_code == ""


@pytest.mark.parametrize(
    "script",
    [
        "tsc && echo ok",
        "tsc || node --test tests/*.test.mjs",
        "tsc; node --test tests/*.test.mjs",
        "unknown-command && node --test tests/*.test.mjs",
        "tsc && node --test --watch tests/*.test.mjs",
    ],
)
def test_package_script_requires_proof_runner_after_successful_preparation(tmp_path: Path, script: str) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": script}}), encoding="utf-8")

    decision = evaluate_verifier_command_policy(_query(tmp_path, "test", ("npm", "run", "test")))

    assert decision.authorized is False


@pytest.mark.parametrize("case,expected", [("passing", True), ("failing", False), ("empty", False)])
def test_actual_node_test_execution_preserves_failure_and_zero_test_verdict(
    tmp_path: Path, case: str, expected: bool
) -> None:
    from polaris.cells.control_plane.verifier_policy.internal.trusted_command_profiles import evaluate_builtin_proof

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}), encoding="utf-8")
    if case != "empty":
        test_path = tmp_path / "tests/example.test.mjs"
        test_path.parent.mkdir()
        value = "2" if case == "passing" else "3"
        test_path.write_text(
            "import test from 'node:test'; import assert from 'node:assert/strict';\n"
            f"test('actual assertion', () => assert.equal(2, {value}));\n",
            encoding="utf-8",
        )
    decision = evaluate_verifier_command_policy(_query(tmp_path, "test", ("npm", "run", "test")))
    assert decision.authorized is True

    execution = subprocess.run(("npm", "run", "test"), cwd=tmp_path, capture_output=True, timeout=15, check=False)

    assert (
        evaluate_builtin_proof(
            decision.profile_id, "test", execution.returncode, False, execution.stdout + execution.stderr
        )
        is expected
    )
