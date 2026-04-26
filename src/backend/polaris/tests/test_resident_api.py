from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import CommandResult
from polaris.cells.resident.autonomy.internal.resident_runtime_service import reset_resident_services
from polaris.delivery.http.app_factory import create_app


def test_resident_api_supports_identity_goals_and_decisions(tmp_path: Path) -> None:
    reset_resident_services()


def test_resident_api_stages_and_runs_goals_through_pm_bridge(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-resident-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        goal_response = client.post(
            "/v2/resident/goals",
            json={
                "workspace": str(workspace),
                "goal_type": "maintenance",
                "title": "Promote governed resident goal",
                "motivation": "Bridge approved goals into PM runtime.",
                "source": "manual",
                "scope": ["src/backend/app/resident"],
                "evidence_refs": ["docs/resident/resident-api.md"],
            },
        )
        assert goal_response.status_code == 200
        goal_id = goal_response.json()["goal_id"]

        materialize_before_approval = client.post(
            f"/v2/resident/goals/{goal_id}/materialize",
            json={"workspace": str(workspace)},
        )
        assert materialize_before_approval.status_code == 409

        approve_response = client.post(
            f"/v2/resident/goals/{goal_id}/approve",
            json={"workspace": str(workspace), "note": "approved for PM bridge"},
        )
        assert approve_response.status_code == 200

        stage_response = client.post(
            f"/v2/resident/goals/{goal_id}/stage",
            json={"workspace": str(workspace), "promote_to_pm_runtime": True},
        )
        assert stage_response.status_code == 200
        staged_payload = stage_response.json()
        assert staged_payload["promoted_to_pm_runtime"] is True
        assert staged_payload["artifacts"]["pm_contract_path"]

        with patch(
            "polaris.cells.resident.autonomy.internal.resident_runtime_service.OrchestrationCommandService.execute_pm_run",
            new=AsyncMock(
                return_value=CommandResult(
                    run_id="pm-resident-001",
                    status="pending",
                    message="Resident PM run started",
                    started_at="2026-03-07T00:00:00+00:00",
                )
            ),
        ):
            run_response = client.post(
                f"/v2/resident/goals/{goal_id}/run",
                json={
                    "workspace": str(workspace),
                    "run_type": "pm",
                    "run_director": True,
                    "director_iterations": 2,
                },
            )
        assert run_response.status_code == 200
        run_payload = run_response.json()
        assert run_payload["pm_run"]["run_id"] == "pm-resident-001"
        assert run_payload["goal"]["materialization_artifacts"]["pm_run"]["run_id"] == "pm-resident-001"

    reset_resident_services()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = create_app(Settings(workspace=str(workspace), ramdisk_root=""))
    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.post("/v2/resident/start", json={"workspace": str(workspace), "mode": "propose"})
        assert response.status_code == 200
        assert response.json()["runtime"]["active"] is True

        identity_response = client.patch(
            "/v2/resident/identity",
            json={
                "workspace": str(workspace),
                "name": "Polaris Resident",
                "mission": "Keep Polaris stable and evidence-driven.",
            },
        )
        assert identity_response.status_code == 200
        assert identity_response.json()["name"] == "Polaris Resident"

        decision_response = client.post(
            "/v2/resident/decisions",
            json={
                "workspace": str(workspace),
                "run_id": "run-api-1",
                "actor": "pm",
                "stage": "contract_validation",
                "summary": "Validated PM contract",
                "strategy_tags": ["contract_validation"],
                "expected_outcome": {"status": "validated", "success": True},
                "actual_outcome": {"status": "validated", "success": True},
                "verdict": "success",
                "evidence_refs": ["runtime/contracts/plan.md"],
                "confidence": 0.85,
            },
        )
        assert decision_response.status_code == 200
        assert decision_response.json()["actor"] == "pm"

        goal_response = client.post(
            "/v2/resident/goals",
            json={
                "workspace": str(workspace),
                "goal_type": "maintenance",
                "title": "Refresh resident docs",
                "motivation": "Keep resident rollout documented.",
                "source": "manual",
                "scope": ["docs/resident"],
                "evidence_refs": ["docs/resident/resident-engineering-rfc.md"],
            },
        )
        assert goal_response.status_code == 200
        goal_id = goal_response.json()["goal_id"]

        approve_response = client.post(
            f"/v2/resident/goals/{goal_id}/approve",
            json={"workspace": str(workspace), "note": "ship it"},
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["status"] == "approved"

        materialize_response = client.post(
            f"/v2/resident/goals/{goal_id}/materialize",
            json={"workspace": str(workspace)},
        )
        assert materialize_response.status_code == 200
        assert materialize_response.json()["focus"] == "resident_goal_materialization"

        status_response = client.get("/v2/resident/status", params={"workspace": str(workspace), "details": True})
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["identity"]["name"] == "Polaris Resident"
        assert payload["counts"]["decisions"] >= 1
        assert payload["counts"]["goals"] >= 1

    reset_resident_services()
