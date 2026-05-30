from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import interventions


class _AllowAllAuth:
    def check(self, _auth_header: str) -> bool:
        return True


def _client(workspace: str) -> TestClient:
    app = FastAPI()
    app.include_router(interventions.router)
    app.state.auth = _AllowAllAuth()
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=workspace))
    return TestClient(app)


def test_list_interventions_returns_empty_payload_when_file_missing(tmp_path: Path) -> None:
    client = _client(str(tmp_path))

    response = client.get("/interventions/list", headers={"authorization": "Bearer test"})

    assert response.status_code == 200
    assert response.json() == {"version": 1, "interventions": []}


def test_apply_intervention_action_updates_workspace_file(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "interventions": [
            {
                "id": "case-1",
                "type": "manual_approval",
                "status": "pending",
                "title": "Approve test action",
                "created_at": "2026-05-29T00:00:00Z",
            }
        ],
    }
    path = tmp_path / "INTERVENTIONS.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    client = _client(str(tmp_path))

    response = client.post(
        "/interventions/action",
        headers={"authorization": "Bearer test"},
        json={"id": "case-1", "action": "approve"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["interventions"][0]["status"] == "approved"
    assert stored["interventions"][0]["updated_at"]
