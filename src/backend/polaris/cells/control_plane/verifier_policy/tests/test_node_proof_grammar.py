"""Public policy regression using private manifests and synthetic executable identity.

No Node, npm, tsx, broker or target command is executed by this module.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy import public as policy
from polaris.cells.control_plane.verifier_policy.public import service
from polaris.cells.control_plane.verifier_policy.tests.test_verifier_command_profile_declaration import _query


@pytest.fixture(autouse=True)
def private_policy_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substitute only policy storage and physical executable identity discovery."""
    monkeypatch.setattr(
        service,
        "read_verifier_policy",
        lambda _query: policy.VerifierPolicyResultV1(policy={"enabled_modalities": ["command"]}),
    )
    monkeypatch.setattr(
        service,
        "_resolve_executable_identity",
        lambda query, **_kwargs: (f"/synthetic/{query.argv[0]}", f"/synthetic/{query.argv[0]}", "c" * 64, ""),
    )


def _manifest(workspace: Path, script: str) -> None:
    (workspace / "package.json").write_text(
        json.dumps({"scripts": {"test": script}, "devDependencies": {"tsx": "^4.7.0"}}), encoding="utf-8"
    )


@pytest.mark.parametrize(
    "script,accepted",
    [
        ("node --test", True),
        ("node --test tests/core.test.mjs", True),
        ("node --import tsx --test tests/core.test.ts", True),
        ("node --import=tsx --test tests/core.test.ts", True),
        ("node --test --import tsx tests/core.test.ts", True),
        ("node --test --import=tsx tests/core.test.ts", True),
        ("node --test-reporter tap --import=tsx --test tests/Core.test.ts", True),
        ("node --import tsx --test --test-reporter=tap dist/tests/*.test.js", True),
        ("node --test --import=./arbitrary.mjs tests/core.test.ts", False),
        ("node --test --import=../arbitrary.mjs tests/core.test.ts", False),
        ("node --test --import=/arbitrary.mjs tests/core.test.ts", False),
        ("node --test --import=file:///arbitrary.mjs tests/core.test.ts", False),
        ("node --test --import=https://example.com/preload.mjs tests/core.test.ts", False),
        ("node --test --import=data:text/javascript,process.exit(0) tests/core.test.ts", False),
        ("node --test --import=unknown-package tests/core.test.ts", False),
        ("node --test --import=tsx/esm tests/core.test.ts", False),
        ("node --test --import=TSX tests/core.test.ts", False),
        ("node --IMPORT=tsx --test tests/core.test.ts", False),
        ("node --test --require=./arbitrary.cjs tests/core.test.mjs", False),
        ("node --test --loader=tsx tests/core.test.ts", False),
        ("node --test --eval=process.exit(0) tests/core.test.mjs", False),
        ("node --test --print=1 tests/core.test.mjs", False),
        ("node --test -e process.exit(0) tests/core.test.mjs", False),
        ("node --test --unknown-option tests/core.test.mjs", False),
        ("node --test --watch tests/core.test.mjs", False),
        ("node --test --no-run tests/core.test.mjs", False),
        ("node --test --collect-only tests/core.test.mjs", False),
        ("node --test --test-update-snapshots tests/core.test.mjs", False),
        ("node --test --test-reporter=./reporter.mjs tests/core.test.mjs", False),
        ("node --test --test-reporter=TAP tests/core.test.mjs", False),
        ("node --test ../foreign.test.mjs", False),
        ("node --test /foreign.test.mjs", False),
        ("node --test C:/foreign.test.mjs", False),
        ("node --test tests/core.py", False),
        ("node --test --import", False),
        ("node --test --import=", False),
        ("node --import --test tests/core.test.ts", False),
        ("node --test --import=tsx", False),
        ("node --test --import=tsx --import=tsx tests/core.test.ts", False),
        ("node --test --test tests/core.test.mjs", False),
        ("node --test --test-reporter", False),
        ("node --test --test-reporter=tap --test-reporter=tap tests/core.test.mjs", False),
        ("node --import=tsx --test tests/core.test.ts --test-reporter=tap", False),
        ("node --import=tsx tests/core.test.ts --test", False),
        ("node --test tests/core.test.ts --import=tsx", False),
        ("node --TEST tests/core.test.mjs", False),
    ],
)
def test_identical_node_grammar_controls_direct_and_package_policy(tmp_path: Path, script: str, accepted: bool) -> None:
    _manifest(tmp_path, script)
    argv = tuple(shlex.split(script))
    for proposal, profile in [(argv, "node.test_runner"), (("npm", "test"), "node.script_test")]:
        query = _query(tmp_path, proposal)
        decision = policy.evaluate_verifier_command_policy(query)
        assert decision.authorized is accepted, (proposal, decision)
        assert decision.normalized_argv == proposal
        assert decision.normalized_cwd == "."
        assert query.command_authority_hash == "b" * 64
        if accepted:
            assert decision.profile_id == profile
            assert decision.policy_decision_hash
        else:
            assert decision.policy_decision_hash == ""


@pytest.mark.parametrize(
    "script,accepted",
    [
        ("tsc && node --import tsx --test tests/core.test.ts", True),
        ("eslint . && node --test --import=tsx tests/core.test.ts", True),
        ("tsc --noEmit && tsc && node --test tests/core.test.mjs", True),
        ("tsc && node --test --import=./arbitrary.mjs tests/core.test.mjs", False),
        ("node --test tests/core.test.mjs | cat", False),
        ("node --test tests/core.test.mjs || true", False),
        ("node --test tests/core.test.mjs; true", False),
        ("sh -c 'node --test tests/core.test.mjs'", False),
        ("env node --test tests/core.test.mjs", False),
        ("unknown && node --test tests/core.test.mjs", False),
    ],
)
def test_package_preparation_remains_success_dependent(tmp_path: Path, script: str, accepted: bool) -> None:
    _manifest(tmp_path, script)
    assert policy.evaluate_verifier_command_policy(_query(tmp_path, ("npm", "test"))).authorized is accepted


def test_manifest_drift_rebinds_or_rejects_same_outer_command(tmp_path: Path) -> None:
    query = _query(tmp_path, ("npm", "test"))
    _manifest(tmp_path, "node --import tsx --test tests/core.test.ts")
    first = policy.evaluate_verifier_command_policy(query)
    _manifest(tmp_path, "node --test --import=tsx tests/core.test.ts")
    changed = policy.evaluate_verifier_command_policy(query)
    assert first.authorized and changed.authorized
    assert first.normalized_argv == changed.normalized_argv == ("npm", "test")
    assert first.policy_decision_hash != changed.policy_decision_hash
    _manifest(tmp_path, "node --test --import=./arbitrary.mjs tests/core.test.ts")
    assert not policy.evaluate_verifier_command_policy(query).authorized


@pytest.mark.parametrize("changed_identity", ["authority", "contract", "owner", "inputs"])
def test_preload_acceptance_does_not_erase_command_identity(tmp_path: Path, changed_identity: str) -> None:
    _manifest(tmp_path, "node --import=tsx --test tests/core.test.ts")
    query = _query(tmp_path, ("npm", "test"))
    original = policy.evaluate_verifier_command_policy(query)
    changed_queries = {
        "authority": replace(query, command_authority_hash="d" * 64),
        "contract": replace(query, completion_contract_hash="d" * 64),
        "owner": replace(query, task_id="other-owner"),
        "inputs": replace(query, input_obligation_ids=("other-input",)),
    }
    changed = policy.evaluate_verifier_command_policy(changed_queries[changed_identity])
    assert original.authorized and changed.authorized
    assert original.policy_decision_hash != changed.policy_decision_hash


def test_preload_still_consumes_current_executable_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _manifest(tmp_path, "node --import=tsx --test tests/core.test.ts")
    query = _query(tmp_path, ("npm", "test"))
    original = policy.evaluate_verifier_command_policy(query)
    monkeypatch.setattr(service, "_resolve_executable_identity", lambda *_args, **_kwargs: ("/x", "/x", "d" * 64, ""))
    changed = policy.evaluate_verifier_command_policy(query)
    assert original.authorized and changed.authorized
    assert original.policy_decision_hash != changed.policy_decision_hash
    monkeypatch.setattr(
        service,
        "_resolve_executable_identity",
        lambda *_args, **_kwargs: ("", "", "", "untrusted_verifier_executable"),
    )
    rejected = policy.evaluate_verifier_command_policy(query)
    assert not rejected.authorized
    assert rejected.error_code == "untrusted_verifier_executable"


def test_node_application_test_argument_remains_separate_entrypoint(tmp_path: Path) -> None:
    argv = ("node", "src/cli.js", "--test")
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="test", argv=argv) == (
        "",
        "verifier_modality_mismatch",
    )
    assert policy.classify_verifier_command_profile(workspace=str(tmp_path), modality="entrypoint", argv=argv) == (
        "node.file_entrypoint",
        "",
    )
