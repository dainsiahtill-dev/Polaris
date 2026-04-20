from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.orchestration.workflow_runtime.public.service import WorkflowSubmissionResult
from polaris.delivery.cli import polaris_cli


def test_polaris_cli_chat_console_routes_director_to_canonical_console(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(polaris_cli, "_ensure_cli_runtime_bindings", lambda: None)

    def _fake_run_role_console(
        *,
        workspace: str | Path,
        role: str = "director",
        backend: str = "auto",
        session_id: str | None = None,
        session_title: str | None = None,
    ) -> int:
        captured["workspace"] = workspace
        captured["role"] = role
        captured["backend"] = backend
        captured["session_id"] = session_id
        captured["session_title"] = session_title
        return 23

    monkeypatch.setattr(polaris_cli, "run_role_console", _fake_run_role_console)

    exit_code = polaris_cli.main(
        [
            "--workspace",
            str(tmp_path),
            "chat",
            "--role",
            "director",
            "--mode",
            "console",
            "--backend",
            "plain",
            "--session-id",
            "session-9",
            "--session-title",
            "Polaris Director",
        ]
    )

    assert exit_code == 23
    assert captured["workspace"] == str(tmp_path.resolve())
    assert captured["role"] == "director"
    assert captured["backend"] == "plain"
    assert captured["session_id"] == "session-9"
    assert captured["session_title"] == "Polaris Director"


def test_polaris_cli_chat_console_accepts_workspace_after_subcommand(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(polaris_cli, "_ensure_cli_runtime_bindings", lambda: None)

    def _fake_run_role_console(
        *,
        workspace: str | Path,
        role: str = "director",
        backend: str = "auto",
        session_id: str | None = None,
        session_title: str | None = None,
    ) -> int:
        captured["workspace"] = workspace
        captured["role"] = role
        captured["backend"] = backend
        return 0

    monkeypatch.setattr(polaris_cli, "run_role_console", _fake_run_role_console)

    exit_code = polaris_cli.main(
        [
            "chat",
            "--role",
            "director",
            "--mode",
            "console",
            "--workspace",
            str(tmp_path),
            "--backend",
            "plain",
        ]
    )

    assert exit_code == 0
    assert captured["workspace"] == str(tmp_path.resolve())
    assert captured["role"] == "director"
    assert captured["backend"] == "plain"


def test_polaris_cli_chat_interactive_uses_role_runtime_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(polaris_cli, "_ensure_cli_runtime_bindings", lambda: None)

    class _FakeRuntimeService:
        async def run_interactive(
            self,
            *,
            role: str,
            workspace: str,
            welcome_message: str = "",
        ) -> None:
            captured["role"] = role
            captured["workspace"] = workspace
            captured["welcome_message"] = welcome_message

    monkeypatch.setattr(polaris_cli, "RoleRuntimeService", lambda: _FakeRuntimeService())

    exit_code = polaris_cli.main(
        [
            "--workspace",
            str(tmp_path),
            "chat",
            "--role",
            "architect",
            "--mode",
            "interactive",
        ]
    )

    assert exit_code == 0
    assert captured["role"] == "architect"
    assert captured["workspace"] == str(tmp_path.resolve())
    assert "Canonical terminal host active." in captured["welcome_message"]


def test_polaris_cli_status_queries_role_runtime(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(polaris_cli, "_ensure_cli_runtime_bindings", lambda: None)

    async def _fake_query(query):
        captured["workspace"] = query.workspace
        captured["role"] = query.role
        return {"ready": True, "roles": ["director", "pm"], "workspace": query.workspace}

    monkeypatch.setattr(polaris_cli, "query_role_runtime_status", _fake_query)

    exit_code = polaris_cli.main(
        [
            "--workspace",
            str(tmp_path),
            "status",
            "--role",
            "director",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["workspace"] == str(tmp_path.resolve())
    assert captured["role"] == "director"
    assert '"ready": true' in out


def test_polaris_cli_workflow_run_pm_uses_contract_payload(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(polaris_cli, "_ensure_cli_runtime_bindings", lambda: None)
    monkeypatch.setattr(
        polaris_cli,
        "_read_workspace_json",
        lambda workspace, relative_path: {
            "tasks": [
                {
                    "id": "PM-1",
                    "title": "Implement canonical workflow path",
                    "acceptance_criteria": ["workflow starts"],
                }
            ],
            "docs_stage": {"ready": True},
        },
    )

    def _fake_submit(workflow_input):
        captured["workflow_input"] = workflow_input
        return WorkflowSubmissionResult(
            submitted=True,
            status="running",
            workflow_id=workflow_input.workflow_id,
            workflow_run_id="workflow-run-1",
            details={"queued": True},
        )

    monkeypatch.setattr(polaris_cli, "submit_pm_workflow_sync", _fake_submit)

    exit_code = polaris_cli.main(
        [
            "workflow",
            "run",
            "pm",
            "--workspace",
            str(tmp_path),
            "--contracts-file",
            "runtime/contracts/pm_tasks.contract.json",
            "--run-id",
            "pm-run-1",
            "--execution-mode",
            "serial",
            "--max-parallel-tasks",
            "5",
        ]
    )

    out = capsys.readouterr().out
    workflow_input = captured["workflow_input"]
    assert exit_code == 0
    assert workflow_input.workspace == str(tmp_path.resolve())
    assert workflow_input.run_id == "pm-run-1"
    assert workflow_input.precomputed_payload["tasks"][0]["id"] == "PM-1"
    assert workflow_input.metadata["docs_stage"] == {"ready": True}
    assert workflow_input.metadata["director_config"]["execution_mode"] == "serial"
    assert workflow_input.metadata["director_config"]["max_parallel_tasks"] == 5
    assert '"workflow_type": "pm"' in out
    assert '"workflow_id": "polaris-pm-pm-run-1"' in out


def test_polaris_cli_workflow_events_queries_public_service(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(polaris_cli, "_ensure_cli_runtime_bindings", lambda: None)

    def _fake_query(workflow_id: str, query_name: str, *args):
        captured["workflow_id"] = workflow_id
        captured["query_name"] = query_name
        captured["args"] = args
        return {"ok": True, "payload": {"events": [{"type": "workflow_started"}]}}

    monkeypatch.setattr(polaris_cli, "query_workflow_sync", _fake_query)

    exit_code = polaris_cli.main(
        [
            "workflow",
            "events",
            "--workspace",
            str(tmp_path),
            "--workflow-id",
            "wf-001",
            "--event-limit",
            "25",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["workflow_id"] == "wf-001"
    assert captured["query_name"] == "events"
    assert captured["args"] == (25,)
    assert '"workflow_started"' in out


def test_polaris_cli_workflow_cancel_routes_to_public_service(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(polaris_cli, "_ensure_cli_runtime_bindings", lambda: None)

    def _fake_cancel(workflow_id: str, *, reason: str = ""):
        captured["workflow_id"] = workflow_id
        captured["reason"] = reason
        return {"ok": True, "cancelled": True, "workflow_id": workflow_id}

    monkeypatch.setattr(polaris_cli, "cancel_workflow_sync", _fake_cancel)

    exit_code = polaris_cli.main(
        [
            "workflow",
            "cancel",
            "--workspace",
            str(tmp_path),
            "--workflow-id",
            "wf-002",
            "--reason",
            "manual-stop",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["workflow_id"] == "wf-002"
    assert captured["reason"] == "manual-stop"
    assert '"cancelled": true' in out
