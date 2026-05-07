from __future__ import annotations

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
