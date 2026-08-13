from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.control_plane.verifier_policy.public import (
    CompileEvidencePolicyCommandV1,
    ControlPlaneVerifierPolicyV1Error,
    EvaluateVerifierCommandPolicyQueryV1,
    ReadVerifierPolicyQueryV1,
    UpdateVerifierPolicyCommandV1,
    compile_evidence_policy,
    evaluate_verifier_command_policy,
    read_verifier_policy,
    update_verifier_policy,
    verifier_policy_to_gate_policy,
)


def test_verifier_policy_defaults_all_optional_modalities_disabled(tmp_path: Path) -> None:
    result = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=str(tmp_path)))
    policy = result.policy

    assert policy["source"] == "control_plane.verifier_policy"
    assert policy["enabled_modalities"] == []
    assert policy["required_modalities"] == []
    assert policy["safety"] == {
        "optional_by_default": True,
        "internal_harness_owned": False,
        "executes_verifiers": False,
        "requires_explicit_user_enablement": True,
    }
    assert policy["capabilities"]["browser"]["enabled"] is False
    assert policy["capabilities"]["visual"]["enabled"] is False


def test_verifier_policy_persists_enabled_browser_and_visual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", "1")
    monkeypatch.setenv("KERNELONE_MULTIMODAL_QA_ENABLED", "1")

    result = update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            browser_enabled=True,
            visual_enabled=True,
            required_modalities=("browser", "visual"),
        )
    )

    policy = result.policy
    assert policy["enabled_modalities"] == ["browser", "visual"]
    assert policy["required_modalities"] == ["browser", "visual"]

    loaded = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=str(tmp_path))).policy
    assert loaded["enabled_modalities"] == ["browser", "visual"]
    assert loaded["required_modalities"] == ["browser", "visual"]


def test_verifier_policy_rejects_required_disabled_modality(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneVerifierPolicyV1Error, match="enabled first"):
        update_verifier_policy(
            UpdateVerifierPolicyCommandV1(
                workspace=str(tmp_path),
                browser_enabled=False,
                required_modalities=("browser",),
            )
        )


def test_verifier_policy_rejects_required_unavailable_modality(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneVerifierPolicyV1Error, match="not available"):
        update_verifier_policy(
            UpdateVerifierPolicyCommandV1(
                workspace=str(tmp_path),
                browser_enabled=True,
                required_modalities=("browser",),
            )
        )


def test_verifier_policy_persists_required_custom_script_even_when_runtime_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", raising=False)

    result = update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            custom_script_enabled=True,
            required_modalities=("custom_script",),
            custom_scripts=(
                {
                    "id": "custom-smoke",
                    "path": "verify.py",
                    "modality": "custom_script",
                    "enabled": True,
                    "required": True,
                },
            ),
        )
    )

    policy = result.policy
    assert policy["required_modalities"] == ["custom_script"]
    assert policy["capabilities"]["custom_script"]["enabled"] is True
    assert policy["capabilities"]["custom_script"]["required"] is True
    assert policy["capabilities"]["custom_script"]["available"] is False


def test_verifier_policy_rejects_absolute_custom_script_path(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneVerifierPolicyV1Error, match="workspace-relative"):
        update_verifier_policy(
            UpdateVerifierPolicyCommandV1(
                workspace=str(tmp_path),
                custom_script_enabled=True,
                required_modalities=("custom_script",),
                custom_scripts=({"id": "bad", "path": "/tmp/run.sh", "required": True},),
            )
        )


def test_verifier_policy_exports_gate_policy_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", "1")
    monkeypatch.setenv("KERNELONE_MULTIMODAL_QA_ENABLED", "1")

    policy = update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            browser_enabled=True,
            visual_enabled=True,
            required_modalities=("browser",),
        )
    ).policy

    fragment = verifier_policy_to_gate_policy(policy)

    assert fragment == {
        "source": "control_plane.verifier_policy",
        "enabled_evidence_modalities": ["browser", "visual"],
        "required_evidence_modalities": ["browser"],
        "custom_scripts": [],
    }


def test_evidence_policy_compiler_keeps_unavailable_browser_advisory_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", raising=False)

    policy = compile_evidence_policy(
        CompileEvidencePolicyCommandV1(
            workspace=str(tmp_path),
            task_id="task-web",
            run_id="run-web",
            project_type="interactive_visual",
            language="typescript",
            target_files=("index.html", "src/main.ts"),
            acceptance_criteria=("HTML5 canvas paints a visible first frame",),
        )
    ).policy

    assert {"qa", "code", "tool_receipt"} <= set(policy["required_evidence_modalities"])
    assert "browser" not in policy["required_evidence_modalities"]
    assert "browser" in policy["advisory_modalities"]
    assert policy["unavailable_required_blockers"] == []
    waived = {item["modality"] for item in policy["waived_modalities"]}
    assert "browser" in waived


def test_evidence_policy_compiler_requires_browser_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", "1")

    policy = compile_evidence_policy(
        CompileEvidencePolicyCommandV1(
            workspace=str(tmp_path),
            task_id="task-web",
            run_id="run-web",
            project_type="html5_canvas",
            target_files=("index.html", "src/main.ts"),
            acceptance_criteria=("browser smoke test must pass",),
        )
    ).policy

    assert "browser" in policy["required_evidence_modalities"]
    assert "browser" in policy["enabled_evidence_modalities"]
    assert policy["unavailable_required_blockers"] == []
    assert policy["gate_policy"]["required_evidence_modalities"] == policy["required_evidence_modalities"]
    assert policy["policy_hash"]


def test_evidence_policy_compiler_blocks_explicit_unavailable_required_modality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", raising=False)

    policy = compile_evidence_policy(
        CompileEvidencePolicyCommandV1(
            workspace=str(tmp_path),
            task_id="task-hard-browser",
            explicit_required_modalities=("browser",),
        )
    ).policy

    assert "browser" in policy["required_evidence_modalities"]
    assert policy["unavailable_required_blockers"] == [
        {
            "modality": "browser",
            "reason": "Set KERNELONE_BROWSER_VERIFIER_AVAILABLE=1 to advertise browser verifier support.",
        }
    ]


def test_evidence_policy_compiler_maps_api_service_to_contract_and_integration(tmp_path: Path) -> None:
    policy = compile_evidence_policy(
        CompileEvidencePolicyCommandV1(
            workspace=str(tmp_path),
            task_id="task-api",
            project_type="api_service",
            language="python",
            target_files=("src/api.py", "tests/test_api.py"),
            acceptance_criteria=("health check endpoint and integration test pass",),
        )
    ).policy

    assert {"qa", "code", "tool_receipt", "api_contract", "integration", "command"} <= set(
        policy["required_evidence_modalities"]
    )
    assert "security" in policy["advisory_modalities"]


def _command_policy_query(
    tmp_path: Path,
    *,
    modality: str,
    argv: tuple[str, ...],
    cwd: str = ".",
) -> EvaluateVerifierCommandPolicyQueryV1:
    return EvaluateVerifierCommandPolicyQueryV1(
        workspace=str(tmp_path),
        project_id="project-1",
        run_id="run-1",
        task_id="task-1",
        completion_contract_hash="a" * 64,
        verifier_obligation_id=f"verify-{modality}",
        command_authority_hash="b" * 64,
        modality=modality,
        argv=argv,
        cwd=cwd,
        input_obligation_ids=("artifact-main", "artifact-tests"),
    )


@pytest.mark.parametrize(
    ("modality", "argv", "profile_id"),
    [
        ("environment_prep", ("npm", "ci"), "node.package_install"),
        ("environment_prep", ("python", "-m", "venv", ".venv"), "python.venv_create"),
        ("build", ("cargo", "build", "--locked"), "rust.cargo.build"),
        ("test", ("python", "-m", "pytest", "-q"), "python.pytest"),
        (
            "test",
            ("python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"),
            "python.unittest_discover",
        ),
        ("lint", ("ruff", "check", "."), "python.ruff_check"),
        ("entrypoint", ("go", "run", "./cmd/app"), "go.run"),
    ],
)
def test_verifier_command_policy_accepts_canonical_toolchain_profiles(
    tmp_path: Path,
    modality: str,
    argv: tuple[str, ...],
    profile_id: str,
) -> None:
    decision = evaluate_verifier_command_policy(_command_policy_query(tmp_path, modality=modality, argv=argv))

    assert decision.authorized is True
    assert decision.error_code == ""
    assert decision.profile_id == profile_id
    assert decision.policy_decision_hash
    assert decision.normalized_argv == argv
    assert decision.input_obligation_ids == ("artifact-main", "artifact-tests")
    assert Path(decision.executable_path).is_absolute()
    assert Path(decision.executable_realpath).is_file()
    assert len(decision.executable_hash) == 64


@pytest.mark.parametrize("argv0", ("./pytest", "/tmp/f3c-untrusted/pytest"))
def test_verifier_command_policy_rejects_workspace_or_ephemeral_fake_executable(
    tmp_path: Path,
    argv0: str,
) -> None:
    fake = tmp_path / "pytest" if argv0.startswith("./") else Path(argv0)
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("#!/bin/sh\necho '1 passed in 0.01s'\n", encoding="utf-8")
    fake.chmod(0o755)
    try:
        decision = evaluate_verifier_command_policy(
            _command_policy_query(tmp_path, modality="test", argv=(argv0, "-q"))
        )
        assert decision.authorized is False
        assert decision.error_code == "untrusted_verifier_executable"
    finally:
        if fake.is_relative_to("/tmp"):
            fake.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("modality", "argv", "error_code"),
    [
        ("test", ("python", "-c", "print('ok')"), "untrusted_verifier_command"),
        ("test", ("pytest", "--collect-only"), "non_proving_verifier_command"),
        ("test", ("pytest", "--co"), "non_proving_verifier_command"),
        ("test", ("go", "test", "./...", "-run", "^$"), "non_proving_verifier_command"),
        ("test", ("go", "test", "./...", "-count=0"), "non_proving_verifier_command"),
        ("test", ("cargo", "build"), "verifier_modality_mismatch"),
        ("build", ("npx", "some-random-package"), "untrusted_verifier_command"),
        ("entrypoint", ("sh", "-c", "exit 0"), "untrusted_verifier_command"),
        ("test", ("python", "-m", "unittest", "discover", "-s", "../tests"), "untrusted_verifier_command"),
        ("environment_prep", ("python", "-m", "venv", "/tmp/foreign"), "untrusted_verifier_command"),
    ],
)
def test_verifier_command_policy_rejects_untrusted_noop_or_wrong_modality(
    tmp_path: Path,
    modality: str,
    argv: tuple[str, ...],
    error_code: str,
) -> None:
    decision = evaluate_verifier_command_policy(_command_policy_query(tmp_path, modality=modality, argv=argv))

    assert decision.authorized is False
    assert decision.error_code == error_code
    assert decision.policy_decision_hash == ""


def test_verifier_command_policy_requires_non_empty_input_closure(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_obligation_ids"):
        EvaluateVerifierCommandPolicyQueryV1(
            workspace=str(tmp_path),
            project_id="project-1",
            run_id="run-1",
            task_id="task-1",
            completion_contract_hash="a" * 64,
            verifier_obligation_id="verify-test",
            command_authority_hash="b" * 64,
            modality="test",
            argv=("pytest", "-q"),
            cwd=".",
            input_obligation_ids=(),
        )


@pytest.mark.parametrize(
    "argv",
    [
        ("ruff", "format", "."),
        ("cargo", "fmt"),
        ("go", "fmt", "./..."),
        ("dotnet", "format"),
        ("dart", "format", "."),
        ("mix", "format"),
        ("pytest", "--watch"),
        ("ruff", "check", "--fix", "."),
        ("ruff", "check", "--fix=true", "."),
        ("ruff", "check", "--unsafe-fixes", "."),
    ],
)
def test_verifier_command_policy_rejects_mutating_or_nonterminal_commands(
    tmp_path: Path,
    argv: tuple[str, ...],
) -> None:
    decision = evaluate_verifier_command_policy(_command_policy_query(tmp_path, modality="lint", argv=argv))

    assert decision.authorized is False
    assert decision.error_code == "non_proving_verifier_command"


@pytest.mark.parametrize(
    "script",
    [
        "echo ok",
        "true",
        "exit 0",
        "pytest --collect-only",
        "node -e process.exit(0)",
        "sh -c true",
    ],
)
def test_node_package_script_must_be_current_and_proof_producing(tmp_path: Path, script: str) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":' + json.dumps(script) + '}}\n',
        encoding="utf-8",
    )

    decision = evaluate_verifier_command_policy(
        _command_policy_query(tmp_path, modality="test", argv=("npm", "test"))
    )

    assert decision.authorized is False
    assert decision.error_code == "non_proving_package_script"


@pytest.mark.parametrize(
    "argv",
    [
        ("cargo", "test", "--no-run"),
        ("cargo", "test", "--doc", "--no-run"),
    ],
)
def test_test_profile_rejects_compile_only_commands(tmp_path: Path, argv: tuple[str, ...]) -> None:
    decision = evaluate_verifier_command_policy(
        _command_policy_query(tmp_path, modality="test", argv=argv)
    )

    assert decision.authorized is False
    assert decision.error_code == "non_proving_verifier_command"


def test_node_package_script_decision_binds_current_manifest_content(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    manifest.write_text('{"scripts":{"test":"pytest -q"}}\n', encoding="utf-8")
    query = _command_policy_query(tmp_path, modality="test", argv=("npm", "test"))

    first = evaluate_verifier_command_policy(query)
    manifest.write_text('{"scripts":{"test":"vitest run"}}\n', encoding="utf-8")
    second = evaluate_verifier_command_policy(query)

    assert first.authorized is True
    assert second.authorized is True
    assert first.policy_decision_hash != second.policy_decision_hash


def test_node_package_script_binds_direct_runner_content(tmp_path: Path) -> None:
    runner = tmp_path / "scripts" / "test.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("console.log('[PASS] first\\nVERIFY PASS')\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node scripts/test.mjs"}}\n',
        encoding="utf-8",
    )
    query = _command_policy_query(tmp_path, modality="test", argv=("npm", "test"))

    first = evaluate_verifier_command_policy(query)
    runner.write_text("console.log('[PASS] second\\nVERIFY PASS')\n", encoding="utf-8")
    second = evaluate_verifier_command_policy(query)

    assert first.authorized is True
    assert first.profile_id == "node.script_test"
    assert second.authorized is True
    assert first.policy_decision_hash != second.policy_decision_hash


def test_node_package_script_rejects_missing_or_escaping_direct_runner(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    query = _command_policy_query(tmp_path, modality="test", argv=("npm", "test"))

    manifest.write_text('{"scripts":{"test":"node missing.js"}}\n', encoding="utf-8")
    assert evaluate_verifier_command_policy(query).error_code == "untrusted_package_script"

    manifest.write_text('{"scripts":{"test":"node ../outside.js"}}\n', encoding="utf-8")
    assert evaluate_verifier_command_policy(query).error_code == "untrusted_package_script"


@pytest.mark.parametrize(
    ("profile_id", "output", "expected"),
    [
        ("python.pytest", "1 passed in 0.01s\n", True),
        ("python.pytest", "no tests ran in 0.01s\n", False),
        ("python.unittest_discover", "Ran 29 tests in 0.123s\n\nOK\n", True),
        ("python.unittest_discover", "Ran 0 tests in 0.001s\n\nOK\n", False),
        ("python.unittest_discover", "Ran 2 tests in 0.010s\n\nFAILED (failures=1)\n", False),
        ("rust.cargo.test", "test result: ok. 2 passed; 0 failed; 0 ignored\n", True),
        ("rust.cargo.test", "test result: ok. 0 passed; 0 failed; 0 ignored\n", False),
        ("go.test", "?\tpkg\t[no test files]\n", False),
        ("go.test", "ok\tpkg\t0.01s\n", True),
        ("node.script_test", "Test Files  1 passed (1)\nTests  2 passed (2)\n", True),
        ("node.script_test", "Tests  no tests\n", False),
        ("node.script_test", "[PASS] syntax\n[PASS] rules\nVERIFY PASS\n", True),
        ("node.script_test", "[PASS] rules\n[FAIL] syntax\nVERIFY FAIL\n", False),
    ],
)
def test_verifier_specific_proof_parser_requires_real_test_execution(
    profile_id: str,
    output: str,
    expected: bool,
) -> None:
    from polaris.cells.control_plane.verifier_policy.internal.trusted_command_profiles import (
        evaluate_builtin_proof,
    )

    assert evaluate_builtin_proof(profile_id, "test", 0, False, output.encode("utf-8")) is expected


def test_verifier_command_policy_authorizes_only_hash_pinned_registered_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    script = tmp_path / "scripts" / "qa.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise SystemExit(0)\n", encoding="utf-8")
    import hashlib

    script_hash = hashlib.sha256(script.read_bytes()).hexdigest()
    update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            custom_script_enabled=True,
            custom_scripts=(
                {
                    "id": "qa-script",
                    "path": "scripts/qa.py",
                    "modality": "test",
                    "enabled": True,
                    "content_sha256": script_hash,
                },
            ),
        )
    )

    query = _command_policy_query(
        tmp_path,
        modality="test",
        argv=("python", "scripts/qa.py"),
    )
    accepted = evaluate_verifier_command_policy(query)
    assert accepted.authorized is True
    assert accepted.profile_id == "custom_script:qa-script"

    script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    rejected = evaluate_verifier_command_policy(query)
    assert rejected.authorized is False
    assert rejected.error_code == "custom_verifier_content_drift"


def test_verifier_command_policy_hash_binds_contract_task_inputs_and_argv(tmp_path: Path) -> None:
    first = evaluate_verifier_command_policy(_command_policy_query(tmp_path, modality="test", argv=("pytest", "-q")))
    second_query = _command_policy_query(tmp_path, modality="test", argv=("pytest", "-q", "tests"))
    second = evaluate_verifier_command_policy(second_query)

    assert first.authorized is True
    assert second.authorized is True
    assert first.policy_decision_hash != second.policy_decision_hash
