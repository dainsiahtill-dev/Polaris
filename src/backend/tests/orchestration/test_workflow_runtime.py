from __future__ import annotations

import argparse
import asyncio
import os
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.orchestration.workflow_runtime.internal.config import (  # noqa: E402
    WorkflowConfig,
    resolve_orchestration_runtime,
)
from polaris.cells.orchestration.workflow_runtime.internal.models import (  # noqa: E402
    DirectorWorkflowInput,
    PMWorkflowInput,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_backend_adapter import (
    RuntimeBackendAdapter,  # noqa: E402
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities import (
    validate_task_contract,  # noqa: E402
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities.director_activities import (
    execute_task_phase,  # noqa: E402
)
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import submit_pm_workflow_sync  # noqa: E402
from polaris.cells.runtime.projection.internal import workflow_status as workflow_status_module
from polaris.delivery.cli.pm import orchestration_engine as engine  # noqa: E402


def test_resolve_orchestration_runtime_normalizes_to_workflow() -> None:
    runtime = resolve_orchestration_runtime(
        "nodes",
        environ={
            "POLARIS_ORCHESTRATION_RUNTIME": "legacy",
        },
    )
    assert runtime == "workflow"


def test_engine_resolve_orchestration_runtime_normalizes_to_workflow(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_ORCHESTRATION_RUNTIME", "embedded")
    args = argparse.Namespace(orchestration_runtime="nodes")
    assert engine._resolve_orchestration_runtime(args) == "workflow"


def test_pm_workflow_input_extracts_non_empty_task_contracts() -> None:
    workflow_input = PMWorkflowInput(
        workspace="X:\\workspace",
        run_id="pm-00001",
        precomputed_payload={
            "tasks": [
                {
                    "id": "PM-1",
                    "title": "Define Workflow task contract",
                    "acceptance_criteria": ["has deterministic validation"],
                },
                {"id": "", "title": "ignored"},
            ]
        },
    )
    tasks = workflow_input.payload_tasks()
    assert [task.task_id for task in tasks] == ["PM-1"]
    assert tasks[0].to_dict()["title"] == "Define Workflow task contract"


def test_director_workflow_input_reads_execution_mode_parallel_limits_and_timeouts() -> None:
    workflow_input = DirectorWorkflowInput.from_mapping(
        {
            "workspace": "X:\\workspace",
            "run_id": "pm-00003",
            "tasks": [{"id": "D-1", "title": "Director task"}],
            "metadata": {
                "director_config": {
                    "execution_mode": "serial",
                    "max_parallel_tasks": 8,
                    "ready_timeout_seconds": 45,
                    "task_timeout_seconds": 1200,
                }
            },
        }
    )
    assert workflow_input.execution_mode == "serial"
    assert workflow_input.max_parallel_tasks == 8
    assert workflow_input.ready_timeout_seconds == 45
    assert workflow_input.task_timeout_seconds == 1200


def test_submit_pm_workflow_sync_returns_disabled_when_workflow_is_off() -> None:
    result = submit_pm_workflow_sync(
        PMWorkflowInput(
            workspace="X:\\workspace",
            run_id="pm-00002",
            precomputed_payload={"tasks": []},
        ),
        WorkflowConfig(enabled=False),
    )
    assert result.submitted is False
    assert result.status == "disabled"


def test_validate_task_contract_reuses_pm_quality_gate() -> None:
    result = asyncio.run(
        validate_task_contract(
            {
                "tasks": [
                    {
                        "id": "PM-LEAK-1",
                        "title": "You are Polaris meta architect",
                        "goal": "No Yapping and think before you code",
                        "assigned_to": "Director",
                        "scope_paths": ["src/game"],
                        "acceptance_criteria": ["add compile checks", "emit evidence logs"],
                    }
                ],
                "docs_stage": {},
            }
        )
    )
    assert result["success"] is False
    issues = "\n".join(result.get("errors") or []).lower()
    assert "leakage" in issues


def test_execute_task_phase_skips_verification_for_no_director() -> None:
    result = asyncio.run(
        execute_task_phase(
            {
                "phase": "verify",
                "task_id": "TEMP-VERIFY-1",
                "workspace": "X:\\workspace",
                "run_id": "pm-verify-1",
                "task": {
                    "id": "TEMP-VERIFY-1",
                    "title": "Verify no-director shortcut",
                    "goal": "Skip verification when no-director mode is active",
                    "scope_paths": ["src/app"],
                },
                "director_config": {"type": "none"},
                "runtime_metadata": {},
                "context": {},
            }
        )
    )
    assert result["success"] is True
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    assert payload.get("verification_skipped") is True


def test_runtime_adapter_resolves_writable_db_path_from_context_root(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("POLARIS_RUNTIME_DB", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.setenv("POLARIS_CONTEXT_ROOT", str(workspace))

    db_path = RuntimeBackendAdapter._resolve_runtime_db_path()
    assert db_path.endswith(
        os.path.join(".polaris", "runtime", "state", "workflow.runtime.db")
    )
    assert os.path.isdir(os.path.dirname(db_path))


def test_get_workflow_runtime_status_falls_back_to_persisted_record(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    cache_root = str(tmp_path / "cache")
    os.makedirs(cache_root, exist_ok=True)

    workflow_status_module.write_workflow_state(
        workspace,
        cache_root,
        {
            "workflow_id": "wf-123",
            "workflow_run_id": "run-123",
            "workflow_status": "running",
            "stage": "director_started",
        },
    )

    monkeypatch.setattr(
        workflow_status_module,
        "describe_workflow_sync",
        lambda workflow_id, config: {"ok": False, "error": "unreachable", "workflow_id": workflow_id},
    )
    monkeypatch.setattr(
        workflow_status_module,
        "query_workflow_sync",
        lambda workflow_id, query_name, config=None: {"ok": False, "error": "query_failed"},
    )

    result = workflow_status_module.get_workflow_runtime_status(workspace, cache_root)
    assert isinstance(result, dict)
    assert result["running"] is True
    assert result["workflow_id"] == "wf-123"
    assert result["stage"] == "director_started"


def test_get_workflow_runtime_status_reuses_cached_snapshots_on_query_timeout(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    cache_root = str(tmp_path / "cache")
    os.makedirs(cache_root, exist_ok=True)

    cached_runtime = {"stage": "director_started", "tasks": {"PM-1": {"state": "running"}}}
    cached_director = {"stage": "director_started", "tasks": {"PM-1": {"state": "running"}}}
    workflow_status_module.write_workflow_state(
        workspace,
        cache_root,
        {
            "workflow_id": "wf-456",
            "run_id": "workflow-run-456",
            "workflow_status": "running",
            "stage": "director_started",
            "runtime_snapshot": cached_runtime,
            "director_runtime_snapshot": cached_director,
        },
    )

    def _fake_describe(workflow_id: str, config) -> dict[str, object]:
        if workflow_id == "wf-456":
            return {"ok": True, "workflow_id": workflow_id, "status": "running", "run_id": "run-456"}
        return {"ok": False, "workflow_id": workflow_id, "error": "unavailable"}

    monkeypatch.setattr(workflow_status_module, "describe_workflow_sync", _fake_describe)
    monkeypatch.setattr(
        workflow_status_module,
        "query_workflow_sync",
        lambda workflow_id, query_name, config=None: {
            "ok": False,
            "error": f"query `{query_name}` timed out after 1.0s",
        },
    )

    result = workflow_status_module.get_workflow_runtime_status(workspace, cache_root)

    assert isinstance(result, dict)
    assert result["runtime_snapshot"] == cached_runtime
    assert result["director_runtime_snapshot"] == cached_director
    assert result["stage"] == "director_started"


def test_get_workflow_runtime_status_skips_child_queries_when_child_workflows_are_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    cache_root = str(tmp_path / "cache")
    os.makedirs(cache_root, exist_ok=True)

    workflow_status_module.write_workflow_state(
        workspace,
        cache_root,
        {
            "workflow_id": "wf-789",
            "run_id": "workflow-run-789",
            "workflow_status": "running",
            "stage": "director_started",
        },
    )

    query_calls: list[str] = []

    def _fake_describe(workflow_id: str, config) -> dict[str, object]:
        if workflow_id == "wf-789":
            return {"ok": True, "workflow_id": workflow_id, "status": "running", "run_id": "run-789"}
        return {"ok": False, "workflow_id": workflow_id, "error": "not_found"}

    def _fake_query(workflow_id: str, query_name: str, config=None) -> dict[str, object]:
        query_calls.append(workflow_id)
        return {"ok": False, "error": "query timed out"}

    monkeypatch.setattr(workflow_status_module, "describe_workflow_sync", _fake_describe)
    monkeypatch.setattr(workflow_status_module, "query_workflow_sync", _fake_query)

    result = workflow_status_module.get_workflow_runtime_status(workspace, cache_root)

    assert isinstance(result, dict)
    assert query_calls == ["wf-789"]
    assert result["workflow_id"] == "wf-789"


def test_get_workflow_runtime_status_uses_workspace_runtime_db_env(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    cache_root = str(tmp_path / "cache")
    os.makedirs(cache_root, exist_ok=True)
    runtime_db = os.path.join(cache_root, "state", "workflow.runtime.db")
    os.makedirs(os.path.dirname(runtime_db), exist_ok=True)
    with open(runtime_db, "w", encoding="utf-8") as handle:
        handle.write("")

    workflow_status_module.write_workflow_state(
        workspace,
        cache_root,
        {
            "workflow_id": "wf-env-001",
            "workflow_status": "running",
            "stage": "pm_started",
        },
    )

    observed: dict[str, str] = {}

    def _fake_describe(workflow_id: str, config) -> dict[str, object]:
        observed["runtime_db"] = str(os.environ.get("POLARIS_RUNTIME_DB") or "")
        observed["cache_root"] = str(
            os.environ.get("KERNELONE_RUNTIME_CACHE_ROOT") or ""
        )
        observed["context_root"] = str(os.environ.get("POLARIS_CONTEXT_ROOT") or "")
        return {"ok": False, "workflow_id": workflow_id, "error": "unreachable"}

    monkeypatch.setattr(workflow_status_module, "describe_workflow_sync", _fake_describe)
    monkeypatch.setattr(
        workflow_status_module,
        "query_workflow_sync",
        lambda workflow_id, query_name, config=None: {"ok": False, "error": "query_failed"},
    )

    original_runtime_db = os.environ.get("POLARIS_RUNTIME_DB")
    result = workflow_status_module.get_workflow_runtime_status(workspace, cache_root)

    assert isinstance(result, dict)
    assert observed["runtime_db"] == runtime_db
    assert observed["cache_root"] == cache_root
    assert observed["context_root"] == workspace
    if original_runtime_db is None:
        assert os.environ.get("POLARIS_RUNTIME_DB") is None
    else:
        assert os.environ.get("POLARIS_RUNTIME_DB") == original_runtime_db


def test_get_workflow_runtime_status_uses_workflow_chain_run_id_for_child_queries(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    cache_root = str(tmp_path / "cache")
    os.makedirs(cache_root, exist_ok=True)

    workflow_status_module.write_workflow_state(
        workspace,
        cache_root,
        {
            "workflow_id": "wf-chain-001",
            "workflow_run_id": "wf-chain-001",
            "run_id": "pm-00001",
            "workflow_chain_run_id": "pm-00001-20260306001000",
            "workflow_status": "running",
            "stage": "pm_started",
        },
    )

    query_targets: list[str] = []

    def _fake_describe(workflow_id: str, config) -> dict[str, object]:
        if workflow_id == "wf-chain-001":
            return {
                "ok": True,
                "workflow_id": workflow_id,
                "status": "running",
                "run_id": "wf-chain-001",
            }
        if workflow_id == "polaris-director-pm-00001-20260306001000":
            return {"ok": True, "workflow_id": workflow_id, "status": "running"}
        return {"ok": False, "workflow_id": workflow_id, "error": "not_found"}

    def _fake_query(workflow_id: str, query_name: str, config=None) -> dict[str, object]:
        query_targets.append(workflow_id)
        if workflow_id == "wf-chain-001":
            return {
                "ok": True,
                "payload": {
                    "stage": "pm_started",
                    "history": [{"details": {"run_id": "pm-00001-20260306001000"}}],
                    "tasks": {},
                },
            }
        if workflow_id == "polaris-director-pm-00001-20260306001000":
            return {
                "ok": True,
                "payload": {
                    "stage": "director_started",
                    "tasks": {"PM-1": {"task_id": "PM-1", "state": "running"}},
                },
            }
        return {"ok": False, "error": "query_failed"}

    monkeypatch.setattr(workflow_status_module, "describe_workflow_sync", _fake_describe)
    monkeypatch.setattr(workflow_status_module, "query_workflow_sync", _fake_query)

    result = workflow_status_module.get_workflow_runtime_status(workspace, cache_root)

    assert isinstance(result, dict)
    assert result["workflow_chain_run_id"] == "pm-00001-20260306001000"
    assert (
        result["director_workflow_id"]
        == "polaris-director-pm-00001-20260306001000"
    )
    assert "polaris-director-pm-00001-20260306001000" in query_targets


def test_build_workflow_director_status_payload_uses_child_snapshot() -> None:
    workflow_status = {
        "running": True,
        "workflow_id": "wf-parent",
        "director_workflow_id": "wf-child",
        "director_runtime_snapshot": {
            "stage": "director_started",
            "tasks": {
                "PM-1": {
                    "task_id": "PM-1",
                    "state": "running",
                    "summary": "Director child workflow is executing",
                    "metadata": {
                        "task_title": "Implement live task visibility",
                        "task_goal": "Expose live Workflow state to every page",
                    },
                }
            },
        },
    }

    status_payload = workflow_status_module.build_workflow_director_status_payload(
        workflow_status,
        workspace="X:\\workspace",
    )
    task_rows = workflow_status_module.build_workflow_director_task_rows(
        workflow_status,
        workspace="X:\\workspace",
    )

    assert isinstance(status_payload, dict)
    assert status_payload["state"] == "RUNNING"
    assert status_payload["metrics"]["workflow_id"] == "wf-child"
    assert status_payload["tasks"]["total"] == 1
    assert len(task_rows) == 1
    assert task_rows[0]["id"] == "PM-1"
    assert task_rows[0]["status"] == "RUNNING"
    assert task_rows[0]["metadata"]["pm_task_id"] == "PM-1"


def test_build_workflow_director_status_payload_marks_queued_tasks_as_pending() -> None:
    workflow_status = {
        "running": True,
        "workflow_id": "wf-parent",
        "director_workflow_status": "",
        "director_runtime_snapshot": {
            "stage": "director_pending",
            "tasks": {
                "PM-2": {
                    "task_id": "PM-2",
                    "state": "pending",
                    "summary": "Waiting for local Director execution",
                    "metadata": {
                        "task_title": "Execute pending director task",
                        "task_goal": "Ensure pending tasks do not force RUNNING state",
                    },
                }
            },
        },
    }

    status_payload = workflow_status_module.build_workflow_director_status_payload(
        workflow_status,
        workspace="X:\\workspace",
    )
    assert isinstance(status_payload, dict)
    assert status_payload["state"] == "PENDING"


def test_build_workflow_director_task_rows_merges_base_contract_fields() -> None:
    workflow_status = {
        "running": False,
        "workflow_id": "wf-parent",
        "director_runtime_snapshot": {
            "stage": "director_completed",
            "tasks": {
                "PM-2": {
                    "task_id": "PM-2",
                    "state": "completed",
                    "summary": "Task completed cleanly",
                    "metadata": {"summary": "Task completed cleanly"},
                }
            },
        },
    }
    base_tasks = [
        {
            "id": "PM-2",
            "title": "Wire Workflow snapshot into main dashboard",
            "goal": "Main dashboard must show current task state",
            "priority": "HIGH",
        }
    ]

    task_rows = workflow_status_module.build_workflow_director_task_rows(
        workflow_status,
        base_tasks=base_tasks,
        workspace="X:\\workspace",
    )

    assert len(task_rows) == 1
    assert task_rows[0]["subject"] == "Wire Workflow snapshot into main dashboard"
    assert task_rows[0]["description"] == "Main dashboard must show current task state"
    assert task_rows[0]["status"] == "COMPLETED"
    assert task_rows[0]["priority"] == "HIGH"


def test_merge_workflow_tasks_projects_runtime_metadata_fields() -> None:
    workflow_status = {
        "running": True,
        "workflow_id": "wf-parent",
        "director_runtime_snapshot": {
            "stage": "director_started",
            "tasks": {
                "PM-4": {
                    "task_id": "PM-4",
                    "state": "running",
                    "summary": "Task is actively writing files",
                    "metadata": {
                        "retry_count": 2,
                        "files_modified": 3,
                        "current_file": "src/services/live.ts",
                        "changed_files": [
                            "src/services/live.ts",
                            "src/components/panel.tsx",
                        ],
                        "phase": "implement",
                    },
                }
            },
        },
    }

    merged = workflow_status_module.merge_workflow_tasks(
        workflow_status,
        workspace="X:\\workspace",
    )

    assert len(merged) == 1
    task = merged[0]
    assert task["id"] == "PM-4"
    assert task["retry_count"] == 2
    assert task["retries"] == 2
    assert task["files_modified"] == 3
    assert task["current_file"] == "src/services/live.ts"
    assert task["phase"] == "implement"
    assert task["changed_files"] == [
        "src/services/live.ts",
        "src/components/panel.tsx",
    ]


def test_build_workflow_director_task_rows_backfills_claimed_by_for_running_task() -> None:
    workflow_status = {
        "running": True,
        "workflow_id": "wf-parent",
        "director_runtime_snapshot": {
            "stage": "director_started",
            "tasks": {
                "PM-3": {
                    "task_id": "PM-3",
                    "state": "running",
                    "summary": "Task currently executing",
                    "metadata": {
                        "task_title": "Expose worker assignment",
                        "task_goal": "Director board should show the active worker",
                    },
                }
            },
        },
    }

    task_rows = workflow_status_module.build_workflow_director_task_rows(
        workflow_status,
        workspace="X:\\workspace",
    )

    assert len(task_rows) == 1
    assert task_rows[0]["status"] == "RUNNING"
    assert task_rows[0]["claimed_by"] == "workflow-worker"
    assert task_rows[0]["metadata"]["claimed_by"] == "workflow-worker"


def test_get_workflow_stage_prefers_qa_snapshot() -> None:
    stage = workflow_status_module.get_workflow_stage(
        {
            "qa_runtime_snapshot": {"stage": "qa_completed"},
            "director_runtime_snapshot": {"stage": "director_completed"},
            "runtime_snapshot": {"stage": "pm_completed"},
        }
    )
    assert stage == "qa_completed"


def test_should_prefer_workflow_status_rejects_stale_snapshot_during_local_execution() -> None:
    local_status = {
        "running": True,
        "state": "RUNNING",
        "tasks": {
            "total": 1,
            "by_status": {"IN_PROGRESS": 1},
        },
    }
    workflow_status = {
        "running": False,
        "workflow_status": "queued",
        "director_workflow_status": "queued",
        "tasks": {"total": 1},
    }
    workflow_tasks = [
        {"id": "PM-1", "status": "PENDING"},
    ]

    prefer_workflow = workflow_status_module.should_prefer_workflow_status(
        local_status=local_status,
        workflow_status=workflow_status,
        workflow_tasks=workflow_tasks,
    )

    assert prefer_workflow is False


def test_should_prefer_workflow_status_accepts_running_snapshot_with_live_rows() -> None:
    local_status = {
        "running": True,
        "state": "RUNNING",
        "tasks": {"total": 1, "by_status": {"IN_PROGRESS": 1}},
    }
    workflow_status = {
        "running": True,
        "workflow_status": "running",
        "director_workflow_status": "running",
        "tasks": {"total": 1},
    }
    workflow_tasks = [
        {"id": "PM-1", "status": "RUNNING"},
    ]

    prefer_workflow = workflow_status_module.should_prefer_workflow_status(
        local_status=local_status,
        workflow_status=workflow_status,
        workflow_tasks=workflow_tasks,
    )

    assert prefer_workflow is True

