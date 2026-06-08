from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.orchestration.pm_planning.internal import shared_quality as shared_quality_module  # noqa: E402
from polaris.cells.orchestration.pm_planning.internal.shared_quality import (  # noqa: E402
    detect_integration_verify_command,
    run_integration_verify_runner,
)


@pytest.fixture(autouse=True)
def _clear_integration_qa_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KERNELONE_INTEGRATION_QA_COMMAND", raising=False)
    monkeypatch.delenv("KERNELONE_INTEGRATION_QA_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("KERNELONE_INTEGRATION_QA_ALLOW_STATIC_NODE_FALLBACK", raising=False)
    monkeypatch.delenv("KERNELONE_INTEGRATION_QA_AUTO_INSTALL_NODE_DEPS", raising=False)


def test_detect_integration_verify_command_prefers_compileall_for_python_without_tests(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "fastapi_entrypoint.py").write_text("def ready() -> bool:\n    return True\n", encoding="utf-8")

    command = detect_integration_verify_command(str(tmp_path))

    assert command == f"{sys.executable} -m compileall -q app"


def test_detect_integration_verify_command_uses_pytest_when_python_tests_exist(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_main.py").write_text(
        "def test_ready() -> None:\n    assert True\n",
        encoding="utf-8",
    )

    command = detect_integration_verify_command(str(tmp_path))

    assert command == f"{sys.executable} -m pytest -q"


def test_detect_integration_verify_command_uses_node_verify_final_when_test_script_missing(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"verify:final":"node scripts/verify_final.mjs","smoke:boot":"node scripts/smoke_boot.mjs"}}\n',
        encoding="utf-8",
    )

    command = detect_integration_verify_command(str(tmp_path))

    assert command == "npm run verify:final"


def test_run_integration_verify_runner_passes_node_verify_final_without_test_script(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"verify:final":"node scripts/verify_final.mjs"}}\n',
        encoding="utf-8",
    )
    (scripts_dir / "verify_final.mjs").write_text("console.log('PASS_SUMMARY');\n", encoding="utf-8")

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is True
    assert summary == "Integration verification passed: npm run verify:final"
    assert errors == []


def test_run_integration_verify_runner_blocks_node_static_fallback_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_INTEGRATION_QA_AUTO_INSTALL_NODE_DEPS", "0")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"jest"},"devDependencies":{"jest":"^29.0.0"}}\n',
        encoding="utf-8",
    )
    (tests_dir / "sample.test.js").write_text(
        "test('ready', () => expect(true).toBe(true));\n",
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is False
    assert "Node dependencies are declared but not installed" in summary
    assert any("KERNELONE_INTEGRATION_QA_ALLOW_STATIC_NODE_FALLBACK=1" in error for error in errors)


def test_run_integration_verify_runner_installs_missing_node_dependencies_before_real_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    class FakeCommandExecutionService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def run(self, request: Any) -> dict[str, object]:
            executable = str(request.executable)
            args = [str(item) for item in request.args]
            calls.append((executable, args))
            if executable == "npm" and args == ["install", "--ignore-scripts"]:
                (tmp_path / "node_modules").mkdir()
                return {"returncode": 0, "stdout": "installed\n", "stderr": ""}
            return {"returncode": 0, "stdout": "tests passed\n", "stderr": ""}

    monkeypatch.setattr(shared_quality_module, "_run_typescript_typecheck", lambda workspace: None)
    monkeypatch.setattr(shared_quality_module, "CommandExecutionService", FakeCommandExecutionService)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"devDependencies":{"vitest":"^2.1.0"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "tests" / "sample.test.js").write_text(
        "test('ready', () => expect(true).toBe(true));\n",
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is True
    assert summary == "Integration verification passed: npm run test -- --watch=false"
    assert errors == []
    assert calls == [
        ("npm", ["install", "--ignore-scripts"]),
        ("npm", ["run", "test", "--", "--watch=false"]),
    ]


def test_run_integration_verify_runner_installs_node_dependencies_before_typescript_typecheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    class FakeCompletedProcess:
        stdout = ""
        stderr = ""
        returncode = 0

    class FakeCommandExecutionService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def run(self, request: Any) -> dict[str, object]:
            executable = str(request.executable)
            args = [str(item) for item in request.args]
            if executable == "npm" and args == ["install", "--ignore-scripts"]:
                (tmp_path / "node_modules").mkdir()
                calls.append(("install", [executable, *args]))
                return {"returncode": 0, "stdout": "installed\n", "stderr": ""}
            calls.append(("verify", [executable, *args]))
            return {"returncode": 0, "stdout": "tests passed\n", "stderr": ""}

    def _fake_subprocess_run(*args: Any, **kwargs: Any) -> FakeCompletedProcess:
        del kwargs
        command = [str(item) for item in args[0]]
        calls.append(("tsc", command))
        return FakeCompletedProcess()

    monkeypatch.setattr(shared_quality_module, "_resolve_repo_tsc", lambda: "/repo/node_modules/.bin/tsc")
    monkeypatch.setattr(shared_quality_module, "CommandExecutionService", FakeCommandExecutionService)
    monkeypatch.setattr(shared_quality_module.subprocess, "run", _fake_subprocess_run)

    (tmp_path / "package.json").write_text(
        """
{
  "scripts": {
    "test": "vitest run"
  },
  "dependencies": {
    "express": "^4.18.2"
  },
  "devDependencies": {
    "@types/node": "^22.10.0",
    "vitest": "^2.1.0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"module":"NodeNext","moduleResolution":"NodeNext","target":"ES2022","strict":true},'
        '"include":["src/**/*.ts","tests/**/*.ts"]}\n',
        encoding="utf-8",
    )
    source_dir = tmp_path / "src" / "middleware"
    source_dir.mkdir(parents=True)
    source_dir.joinpath("auth.ts").write_text(
        "import { AsyncLocalStorage } from 'async_hooks';\n"
        "export const tenantContext = new AsyncLocalStorage<{ tenantId: string }>();\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    tests_dir.joinpath("auth.test.ts").write_text(
        "import { describe, expect, it } from 'vitest';\n"
        "describe('auth', () => { it('loads', () => expect(true).toBe(true)); });\n",
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is True
    assert summary == "Integration verification passed: npm run test -- --watch=false"
    assert errors == []
    assert calls == [
        ("install", ["npm", "install", "--ignore-scripts"]),
        (
            "tsc",
            ["/repo/node_modules/.bin/tsc", "--noEmit", "--skipLibCheck", "--pretty", "false", "-p", "tsconfig.json"],
        ),
        ("verify", ["npm", "run", "test", "--", "--watch=false"]),
    ]


def test_run_integration_verify_runner_runs_self_contained_node_script_with_declared_dependencies(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node scripts/test.mjs"},"dependencies":{"three":"^0.165.0"}}\n',
        encoding="utf-8",
    )
    (scripts_dir / "test.mjs").write_text("console.log('STRUCTURAL_TEST_PASS');\n", encoding="utf-8")

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is True
    assert summary == "Integration verification passed: npm run test -- --watch=false"
    assert errors == []


def test_run_integration_verify_runner_ignores_uninstalled_test_framework_import_noise(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node scripts/test.mjs"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"module":"NodeNext","moduleResolution":"NodeNext","target":"ES2022","strict":true},'
        '"include":["src/**/*.ts","tests/**/*.ts"]}\n',
        encoding="utf-8",
    )
    (src_dir / "index.ts").write_text("export const ready = true;\n", encoding="utf-8")
    (tests_dir / "sample.test.ts").write_text(
        'import { describe, expect, it } from "@jest/globals";\n'
        "describe('ready', () => { it('is true', () => expect(true).toBe(true)); });\n",
        encoding="utf-8",
    )
    (scripts_dir / "test.mjs").write_text("console.log('SELF_CONTAINED_TEST_PASS');\n", encoding="utf-8")

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is True
    assert summary == "Integration verification passed: npm run test -- --watch=false"
    assert errors == []


def test_run_integration_verify_runner_fails_on_deterministic_scaffold_marker(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "src" / "client"
    source_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node scripts/test.mjs"}}\n',
        encoding="utf-8",
    )
    (scripts_dir / "test.mjs").write_text("console.log('PASS');\n", encoding="utf-8")
    (source_dir / "three-scene.ts").write_text(
        'export const scaffoldVersion = "deterministic-declared-scope-v1";\n',
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is False
    assert "artifact quality scan" in summary
    assert any("deterministic scaffold marker" in error for error in errors)


def test_run_integration_verify_runner_fails_on_audit_seed_scenario_scaffold(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "src" / "game"
    source_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node scripts/test.mjs"}}\n',
        encoding="utf-8",
    )
    (scripts_dir / "test.mjs").write_text("console.log('PASS');\n", encoding="utf-8")
    (source_dir / "rules-engine.ts").write_text(
        "\n".join(
            [
                "export const cardRulesEngineScenario0 = {",
                '  title: "card-rules-engine planning scenario 0",',
                '  tags: ["planning", "draft", "audit-seed"],',
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is False
    assert "artifact quality scan" in summary
    assert any("audit-seed" in error or "planning scenario" in error for error in errors)


def test_run_integration_verify_runner_fails_on_structural_verification_script(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "src"
    source_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node scripts/test.mjs"}}\n',
        encoding="utf-8",
    )
    (source_dir / "index.ts").write_text("export const ready = true;\n", encoding="utf-8")
    (scripts_dir / "test.mjs").write_text(
        "const tests = [];\nconsole.log(`test verification completed: ${tests.length} files`);\n",
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is False
    assert "artifact quality scan" in summary
    assert any("test verification completed" in error for error in errors)


def test_run_integration_verify_runner_fails_on_trivial_arithmetic_placeholder_tests(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node scripts/test.mjs"}}\n',
        encoding="utf-8",
    )
    (scripts_dir / "test.mjs").write_text("console.log('PASS');\n", encoding="utf-8")
    (tests_dir / "multiplayer-flow.test.ts").write_text(
        "\n".join(
            [
                "describe('placeholder', () => {",
                "  it('does arithmetic', () => {",
                "    expect(1 + 1).toBe(2);",
                "    expect(2 + 2).toBe(4);",
                "    expect(3 + 3).toBe(6);",
                "  });",
                "});",
            ]
        ),
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is False
    assert "artifact quality scan" in summary
    assert any("trivial arithmetic placeholder" in error for error in errors)


def test_run_integration_verify_runner_allows_explicit_node_static_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_INTEGRATION_QA_ALLOW_STATIC_NODE_FALLBACK", "1")
    monkeypatch.setenv("KERNELONE_INTEGRATION_QA_AUTO_INSTALL_NODE_DEPS", "0")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"jest"},"devDependencies":{"jest":"^29.0.0"}}\n',
        encoding="utf-8",
    )
    (tests_dir / "sample.test.js").write_text(
        "test('ready', () => expect(true).toBe(true));\n",
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is True
    assert summary.startswith("Node static verification passed while dependencies are not installed")
    assert errors == []


def test_run_integration_verify_runner_fails_when_pytest_assertion_fails(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_main.py").write_text(
        "def test_ready() -> None:\n    assert False\n",
        encoding="utf-8",
    )

    ok, summary, errors = run_integration_verify_runner(str(tmp_path))

    assert ok is False
    assert summary == f"Integration verification failed: {sys.executable} -m pytest -q"
    assert any("assert False" in error or "AssertionError" in error or "FAILED" in error for error in errors)
