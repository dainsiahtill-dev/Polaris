"""Current-manifest public policy experiment; no Node/npm process is executed."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy import public as policy
from polaris.cells.control_plane.verifier_policy.public import service
from polaris.cells.control_plane.verifier_policy.tests.test_verifier_command_profile_declaration import _query


@pytest.fixture(autouse=True)
def private_policy_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace storage and executable discovery, retaining actual policy parsing."""
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


def _manifest(workspace: Path, scripts: dict[str, str]) -> None:
    (workspace / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")


@pytest.mark.parametrize("preparation", ["npm run build", "npm run-script build", "pnpm run build", "yarn run build"])
def test_same_manifest_build_preparation_reaches_real_test_grammar(tmp_path: Path, preparation: str) -> None:
    _manifest(
        tmp_path, {"build": "tsc -p tsconfig.json", "test": f"{preparation} && node --test dist/tests/domain.test.js"}
    )
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, ("npm", "test")))
    assert decision.authorized, decision
    assert decision.normalized_argv == ("npm", "test")
    assert decision.profile_id == "node.script_test"
    assert decision.policy_decision_hash


def test_nested_preparation_and_current_manifest_rebind_after_cold_read(tmp_path: Path) -> None:
    scripts = {"compile": "tsc -p tsconfig.json", "build": "npm run compile", "test": "npm run build && node --test"}
    query = _query(tmp_path, ("npm", "test"))
    _manifest(tmp_path, scripts)
    first = policy.evaluate_verifier_command_policy(query)
    scripts["compile"] = "tsc --project tsconfig.json"
    _manifest(tmp_path, scripts)
    changed = policy.evaluate_verifier_command_policy(query)
    assert first.authorized and changed.authorized
    assert first.policy_decision_hash != changed.policy_decision_hash
    scripts["compile"] = "echo no compiler"
    _manifest(tmp_path, scripts)
    denied = policy.evaluate_verifier_command_policy(query)
    assert not denied.authorized
    assert denied.error_code == "non_proving_package_script"
    assert not denied.policy_decision_hash


@pytest.mark.parametrize(
    "preparation,scripts",
    [
        ("npm run missing", {}),
        ("npm run build", {"build": "npm run build"}),
        ("npm run build", {"build": "npm run compile", "compile": "npm run build"}),
        ("npm run build", {"build": "echo success"}),
        ("npm run build", {"build": "unknown-compiler"}),
        ("npm run build", {"build": "tsc --watch"}),
        ("npm run build", {"build": "tsc || true"}),
        ("npm run build", {"build": "tsc; true"}),
        ("npm run build", {"build": "tsc | cat"}),
        ("npm run build", {"build": "tsc\ntrue"}),
        ("npm run build", {"build": "tsc $(true)"}),
        ("npm run build", {"build": "tsc `true`"}),
        ("npm run build", {"build": "tsc > hidden.log"}),
        ("npm run --if-present build", {"build": "tsc"}),
        ("npm run build -- --watch", {"build": "tsc"}),
        ("/tmp/npm run build", {"build": "tsc"}),
        ("env npm run build", {"build": "tsc"}),
        ("npm run --build", {"--build": "tsc"}),
        ("npm run build", {"build": "node dist/cli.js"}),
    ],
)
def test_preparation_reference_cannot_launder_non_proving_or_alternate_commands(
    tmp_path: Path, preparation: str, scripts: dict[str, str]
) -> None:
    _manifest(tmp_path, {**scripts, "test": f"{preparation} && node --test"})
    decision = policy.evaluate_verifier_command_policy(_query(tmp_path, ("npm", "test")))
    assert not decision.authorized
    assert decision.error_code == "non_proving_package_script"
    assert not decision.policy_decision_hash


@pytest.mark.parametrize("runner", ["true", "node --test --eval=process.exit(0)", "node --test || true"])
def test_preparation_does_not_replace_the_final_test_runner(tmp_path: Path, runner: str) -> None:
    _manifest(tmp_path, {"build": "tsc", "test": f"npm run build && {runner}"})
    assert not policy.evaluate_verifier_command_policy(_query(tmp_path, ("npm", "test"))).authorized


def test_new_package_preparation_cannot_bypass_entrypoint_file_checks(tmp_path: Path) -> None:
    _manifest(tmp_path, {"build": "tsc", "start": "npm run build && node missing.js"})
    query = replace(_query(tmp_path, ("npm", "start")), modality="entrypoint")
    decision = policy.evaluate_verifier_command_policy(query)
    assert not (tmp_path / "missing.js").exists()
    assert not decision.authorized
    assert not decision.policy_decision_hash
