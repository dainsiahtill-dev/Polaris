import json
from pathlib import Path

from polaris.bootstrap.config import Settings
from fastapi.testclient import TestClient
from polaris.cells.runtime.state_owner.internal.state import AppState, Auth
from polaris.delivery.http.app_factory import create_app
from polaris.kernelone.storage import resolve_storage_roots


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_history_factory_overview_aggregates_round_flow(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-history-token"
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "0")
    monkeypatch.setenv("POLARIS_TOKEN", test_token)
    workspace = tmp_path
    runtime_root = Path(resolve_storage_roots(str(workspace)).runtime_root)

    _write_json(
        runtime_root / "state" / "task_history.state.json",
        {
            "rounds": [
                {
                    "round_id": "pm-00001",
                    "timestamp": "2026-02-14T00:00:01",
                    "focus": "flow integration",
                    "overall_goal": "unify pm and director flow",
                    "tasks": [{"id": "T1", "title": "task", "goal": "goal"}],
                    "execution_summary": {"total_tasks": 1, "success_rate": 1.0},
                }
            ]
        },
    )
    run_dir = runtime_root / "runs" / "pm-00001"
    _write_json(
        run_dir / "results" / "director.result.json",
        {
            "status": "success",
            "start_time": "2026-02-14T00:00:02",
            "end_time": "2026-02-14T00:00:03",
            "successes": 1,
            "total": 1,
        },
    )
    _write_json(
        run_dir / "state" / "assignee_routing.state.json",
        {
            "run_id": "pm-00001",
            "director_task_ids": ["T1"],
            "docs_only_task_ids": [],
            "non_director_queue": [{"id": "A1", "assigned_to": "Auditor", "title": "audit"}],
            "generated_director_task_ids": ["T1-defect"],
        },
    )
    _write_json(
        run_dir / "state" / "assignee_execution.state.json",
        {
            "run_id": "pm-00001",
            "hard_block": False,
            "blocked_reasons": [],
            "results": [
                {
                    "task_id": "A1",
                    "assigned_to": "Auditor",
                    "status": "failed",
                    "ok": False,
                    "blocking": False,
                    "summary": "Auditor FAIL recorded",
                    "error_code": "AUDITOR_FAILS_WITH_DEFECT",
                },
                {
                    "task_id": "P1",
                    "assigned_to": "PolicyGate",
                    "status": "blocked",
                    "ok": False,
                    "blocking": True,
                    "summary": "PolicyGate decision=BLOCK",
                    "error_code": "POLICY_GATE_BLOCKED",
                    "output": {"decision": "block"},
                },
                {
                    "task_id": "F1",
                    "assigned_to": "FinOps",
                    "status": "blocked",
                    "ok": False,
                    "blocking": True,
                    "summary": "FinOps blocked task",
                    "error_code": "FINOPS_BUDGET_BLOCKED",
                    "output": {"budget_limit": 1, "estimated_units": 3},
                },
            ],
            "generated_director_tasks": [{"id": "T1-defect", "title": "fix defect"}],
        },
    )
    _write_json(
        run_dir / "role_queue" / "A1-auditor.json",
        {
            "task": {"id": "A1", "assigned_to": "Auditor"},
            "result": {"status": "failed", "ok": False, "summary": "Auditor FAIL recorded"},
        },
    )

    settings = Settings(workspace=str(workspace), ramdisk_root="")
    app = create_app(settings)
    app.state.app_state = AppState(settings=settings)
    app.state.auth = Auth(token=test_token)
    client = TestClient(app, headers={"Authorization": f"Bearer {test_token}"})

    response = client.get("/history/factory/overview?limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_rounds"] == 1
    assert payload["summary"]["passed_rounds"] == 1
    assert payload["summary"]["defect_followups_generated"] == 1
    assert payload["summary"]["policy_gate_blocks"] == 1
    assert payload["summary"]["finops_blocks"] == 1
    assert payload["summary"]["auditor_failures"] == 1
    round_item = payload["rounds"][0]
    assert round_item["factory_flow"]["pipeline_status"]["status"] == "passed"
    assert round_item["factory_flow"]["defect_loop"]["auditor_fail_detected"] is True
    assert round_item["factory_flow"]["non_director_execution"]["results"][0]["error_code"] == "AUDITOR_FAILS_WITH_DEFECT"
