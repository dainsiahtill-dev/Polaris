from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter
from polaris.delivery.cli.pm import qa_auditor


class _TimeoutCommandExecutionService:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def parse_command(self, command: str, *, cwd: str, timeout_seconds: int) -> SimpleNamespace:
        return SimpleNamespace(
            executable="npm",
            args=["run", "test", "--", "--watch=false"],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            raw_command=command,
        )

    def run(self, request: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "partial stdout before timeout",
            "stderr": "partial stderr before timeout",
            "timed_out": True,
            "error": "Command timed out after 120s",
            "command": {
                "executable": request.executable,
                "args": list(request.args),
            },
        }


class _SuccessfulNodeCommandExecutionService:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def parse_command(self, command: str, *, cwd: str, timeout_seconds: int) -> SimpleNamespace:
        return SimpleNamespace(
            executable="node",
            args=["scripts/build-check.js"],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            raw_command=command,
        )

    def run(self, request: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "returncode": 0,
            "stdout": "build check passed",
            "stderr": "",
            "timed_out": False,
            "command": {
                "executable": request.executable,
                "args": list(request.args),
            },
        }


def test_qa_auditor_verify_command_uses_command_execution_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(qa_auditor, "CommandExecutionService", _TimeoutCommandExecutionService)

    result = qa_auditor._execute_verify_command(
        command="npm run test -- --watch=false",
        working_dir=str(tmp_path),
        timeout_seconds=120,
    )

    assert result["exit_code"] == 124
    assert result["command_args"] == ["npm", "run", "test", "--", "--watch=false"]
    assert result["stdout_tail"] == ["partial stdout before timeout"]
    assert "partial stderr before timeout" in "\n".join(result["stderr_tail"])
    assert "Command timed out after 120s" in "\n".join(result["stderr_tail"])


def test_qa_adapter_test_execution_preserves_timeout_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.qa_adapter.CommandExecutionService",
        _TimeoutCommandExecutionService,
    )
    adapter = QAAdapter(str(tmp_path))

    result = adapter._verify_test_execution(
        target=str(tmp_path),
        context={"metadata": {"test_commands": ["npm run test -- --watch=false"]}},
    )

    assert result["passed"] is False
    assert result["failed_count"] == 1
    assert result["test_results"][0]["exit_code"] == 124
    assert "partial stdout before timeout" in result["test_results"][0]["output"]
    assert "partial stderr before timeout" in result["test_results"][0]["output"]
    assert result["errors"][0].startswith("test_timeout:npm run test -- --watch=false")


def test_qa_contract_infers_missing_package_json_script_from_acceptance(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "node --test"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = qa_auditor.evaluate_qa_contract(
        contract={},
        context={
            "director_status": "success",
            "task": {
                "assigned_to": "Director",
                "acceptance_criteria": [
                    "`package.json` 包含 `build` 与 `test` 脚本",
                ],
            },
        },
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / ".polaris" / "runtime" / "runs" / "r1"),
    )

    assert result["verdict"] == "FAIL"
    assert result["diagnostics"] == "gates_failed"
    assert any("package_json_scripts_present:missing=build" in failed_gate for failed_gate in result["failed_gates"])


def test_qa_contract_passes_when_required_package_json_scripts_exist(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "node scripts/build-check.js",
                    "test": "node --test",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = qa_auditor.evaluate_qa_contract(
        contract={},
        context={
            "director_status": "success",
            "task": {
                "assigned_to": "Director",
                "acceptance_criteria": [
                    "`package.json` 包含 `build` 与 `test` 脚本",
                ],
            },
        },
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / ".polaris" / "runtime" / "runs" / "r1"),
    )

    assert result["verdict"] == "PASS"
    assert result["failed_gates"] == []


def test_qa_contract_infers_verify_command_from_acceptance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(qa_auditor, "CommandExecutionService", _SuccessfulNodeCommandExecutionService)

    result = qa_auditor.evaluate_qa_contract(
        contract={},
        context={
            "director_status": "success",
            "task": {
                "assigned_to": "Director",
                "acceptance_criteria": [
                    "执行 `node scripts/build-check.js` 必须通过",
                ],
            },
        },
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / ".polaris" / "runtime" / "runs" / "r1"),
    )

    assert result["verdict"] == "PASS"
    assert result["verify_runs"][0]["command"] == "node scripts/build-check.js"
    assert result["verify_runs"][0]["command_args"] == ["node", "scripts/build-check.js"]
