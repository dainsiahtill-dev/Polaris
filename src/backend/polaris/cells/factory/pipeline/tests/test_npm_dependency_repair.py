"""Regression tests for retired Factory workspace mutation shortcuts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_workspace_quality import (
    WorkspaceQualityRunner,
)


def _factory_run(run_id: str) -> FactoryRun:
    return FactoryRun(
        id=run_id,
        config=FactoryConfig(name=run_id),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-07-13T00:00:00+00:00",
    )


def _write_package_json(tmp_path: Path, payload: dict[str, Any]) -> str:
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    (tmp_path / "package.json").write_text(content, encoding="utf-8")
    return content


def test_workspace_quality_runner_has_no_workspace_mutation_api(tmp_path: Path) -> None:
    """Quality measurement must not expose the retired Factory repair surface."""

    runner = WorkspaceQualityRunner(tmp_path)

    assert not hasattr(runner, "repair_hallucinated_npm_dependencies")
    assert not hasattr(runner, "repair_cjs_export_import_mismatch")
    assert not hasattr(runner, "repair_test_trim_mismatch")
    assert not hasattr(runner, "consume_repair_receipts")
    assert not hasattr(runner, "_apply_workspace_quality_patch")


def test_workspace_quality_skips_long_lived_http_server_start(tmp_path: Path) -> None:
    """Static web servers must not be required to exit during validation."""

    _write_package_json(
        tmp_path,
        {
            "name": "web-project",
            "scripts": {
                "build": "tsc -p tsconfig.json",
                "test": "npm run build",
                "start": "npx --yes http-server . -p ${PORT:-0} -c-1",
            },
        },
    )

    commands = WorkspaceQualityRunner(tmp_path).workspace_quality_commands({})

    assert ["npm", "run", "build"] in commands
    assert ["npm", "test"] in commands
    assert ["npm", "run", "start"] not in commands


def test_workspace_quality_keeps_exiting_node_start_smoke(tmp_path: Path) -> None:
    """CLI-style npm start scripts remain real entrypoint smoke checks."""

    _write_package_json(
        tmp_path,
        {
            "name": "cli-project",
            "scripts": {
                "build": "tsc -p tsconfig.json",
                "start": "node dist/main.js",
            },
        },
    )

    commands = WorkspaceQualityRunner(tmp_path).workspace_quality_commands({})

    assert ["npm", "run", "build"] in commands
    assert ["npm", "run", "start"] in commands


@pytest.mark.asyncio
async def test_npm_prepare_failure_is_preserved_without_workspace_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed npm install remains evidence; Factory never edits or retries it."""

    package_before = _write_package_json(
        tmp_path,
        {
            "name": "dependency-contract",
            "dependencies": {"nonexistent-package": "0.0.0"},
        },
    )
    source_path = tmp_path / "src" / "index.js"
    source_path.parent.mkdir(parents=True)
    source_before = "module.exports = 'stable';\n"
    source_path.write_text(source_before, encoding="utf-8")
    test_path = tmp_path / "tests" / "behavior.test.js"
    test_path.parent.mkdir(parents=True)
    test_before = "throw new Error('not reached');\n"
    test_path.write_text(test_before, encoding="utf-8")

    executor = OrchestrationStageExecutor(tmp_path)
    command_calls: list[list[str]] = []

    def run_command(command: list[str], timeout_seconds: float) -> dict[str, Any]:
        del timeout_seconds
        command_calls.append(command)
        assert command == ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
        return {
            "command": command,
            "exit_code": 1,
            "passed": False,
            "stdout_tail": "",
            "stderr_tail": "npm error notarget No matching version found for nonexistent-package@0.0.0",
            "error": "npm install failed",
        }

    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
    monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
    monkeypatch.setattr(
        executor,
        "_workspace_quality_prepare_commands",
        lambda commands, context: [["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]],
    )
    monkeypatch.setattr(executor, "_run_workspace_quality_command", run_command)

    passed, artifact = await executor._run_workspace_quality_checks(
        _factory_run("factory-npm-prepare-failure"),
        {},
    )

    assert passed is False
    assert command_calls == [["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]]
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == package_before
    assert source_path.read_text(encoding="utf-8") == source_before
    assert test_path.read_text(encoding="utf-8") == test_before
    payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
    prepare_result = payload["commands"][0]
    assert prepare_result["phase"] == "prepare"
    assert prepare_result["passed"] is False
    assert "No matching version found" in prepare_result["stderr_tail"]
    assert "repair" not in prepare_result
    assert payload["repair"]["attempted"] is False


@pytest.mark.asyncio
async def test_check_failure_enters_runtime_then_llm_repair_without_factory_shortcut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary check failures use the existing Director runtime/LLM loop."""

    package_before = _write_package_json(
        tmp_path,
        {"name": "runtime-repair-contract", "scripts": {"test": "node tests/behavior.test.js"}},
    )
    source_path = tmp_path / "src" / "index.js"
    source_path.parent.mkdir(parents=True)
    source_before = "module.exports = { value: ' Tide ' };\n"
    source_path.write_text(source_before, encoding="utf-8")
    test_path = tmp_path / "tests" / "behavior.test.js"
    test_path.parent.mkdir(parents=True)
    test_before = "assert.strictEqual(result.value, 'Tide');\n"
    test_path.write_text(test_before, encoding="utf-8")

    executor = OrchestrationStageExecutor(tmp_path)
    state = {"director_repair_completed": False}
    runtime_calls: list[list[str]] = []
    llm_calls: list[list[str]] = []

    def run_command(command: list[str], timeout_seconds: float) -> dict[str, Any]:
        del timeout_seconds
        return {
            "command": command,
            "exit_code": 0 if state["director_repair_completed"] else 1,
            "passed": state["director_repair_completed"],
            "stdout_tail": "" if state["director_repair_completed"] else "AssertionError: ' Tide ' !== 'Tide'",
            "stderr_tail": "",
            "error": "" if state["director_repair_completed"] else "npm test failed",
        }

    def runtime_repair(
        *,
        run_id: str,
        artifact_quality_errors: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert run_id == "factory-check-runtime-route"
        runtime_calls.append(list(artifact_quality_errors))
        return [], {
            "attempted": True,
            "success": False,
            "source_tools": [],
            "tool_results": 0,
            "write_tool_evidence": False,
        }

    async def llm_repair(
        *,
        run_id: str,
        context: dict[str, Any],
        artifact_quality_errors: list[str],
        repair_attempt: int,
        **_: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert run_id == "factory-check-runtime-route"
        assert context["workspace_quality_repair_max_rounds"] == 1
        assert repair_attempt == 1
        llm_calls.append(list(artifact_quality_errors))
        state["director_repair_completed"] = True
        return (
            [
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "director_llm_workspace_quality_repair",
                        "file": "src/index.js",
                        "operation": "modify",
                    },
                }
            ],
            {
                "attempted": True,
                "success": True,
                "source_tools": ["director_llm_workspace_quality_repair"],
                "tool_results": 1,
                "write_tool_evidence": True,
            },
        )

    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
    monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
    monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
    monkeypatch.setattr(executor, "_run_workspace_quality_command", run_command)
    monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", runtime_repair)
    monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", llm_repair)

    passed, artifact = await executor._run_workspace_quality_checks(
        _factory_run("factory-check-runtime-route"),
        {"workspace_quality_repair_max_rounds": 1},
    )

    assert passed is True
    assert runtime_calls and llm_calls
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == package_before
    assert source_path.read_text(encoding="utf-8") == source_before
    assert test_path.read_text(encoding="utf-8") == test_before
    payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["repair"]["source_tools"] == ["director_llm_workspace_quality_repair"]
    assert "deterministic_repairs" not in payload["repair"]
