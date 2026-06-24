from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.delivery.http.app_factory import create_app


def _client(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("KERNELONE_TOKEN", "test-token")
    app = create_app(Settings(workspace=workspace))
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _write_ledger_event(
    workspace: Path,
    *,
    run_id: str = "run-1",
    namespace: str = "control_plane",
) -> None:
    ledger_path = workspace / "runtime" / namespace / "ledger" / f"{run_id}.ndjson"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event_type": "gate_evaluated",
        "event_id": "evt-1",
        "content_id": "cid-1",
        "append_id": "append-1",
        "stage": "qa_verifier",
        "gate": {"name": "qa_verifier", "ok": True, "summary": "physics verifier passed"},
        "job_token": {
            "token_id": "token-1",
            "run_id": run_id,
            "project_id": "P1",
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {
                "enabled_evidence_modalities": ["physics"],
                "required_evidence_modalities": ["physics"],
            },
        },
        "physical_evidence": {
            "user_verifiers": [
                {
                    "id": "physics",
                    "modality": "physics",
                    "script": "tests/physics.test.ts",
                    "ok": True,
                    "detail": "energy drift within tolerance",
                }
            ]
        },
    }
    ledger_path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")


def test_control_plane_ledger_projection_route_is_platform_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_ledger_event(tmp_path, run_id="run-1")
    client = _client(tmp_path, monkeypatch)

    response = client.get(
        "/v2/control-plane/ledger/projection?run_id=run-1",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "run_ledger_projection"
    assert payload["available"] is True
    assert payload["ok"] is True
    assert payload["projects"][0]["project_id"] == "P1"
    assert payload["evidence_policy"]["required_modalities"] == ["physics"]
    assert payload["evidence_policy"]["missing_required_modalities"] == []


def test_control_plane_ledger_projection_route_returns_pending_without_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get("/v2/control-plane/ledger/projection", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "run_ledger_projection"
    assert payload["available"] is False
    assert payload["status"] == "pending"


def test_control_plane_ledger_projection_route_ignores_factory_compat_ledger_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_ledger_event(tmp_path, run_id="run-1", namespace="factory")
    client = _client(tmp_path, monkeypatch)

    response = client.get(
        "/v2/control-plane/ledger/projection?run_id=run-1",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "run_ledger_projection"
    assert payload["available"] is False
    assert payload["ok"] is False
    assert payload["status"] == "pending"
    assert payload["compat_ledgers_included"] is False
    assert payload["projects"] == []


def test_control_plane_verifier_policy_route_defaults_to_optional_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get("/v2/control-plane/verifier-policy", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "control_plane.verifier_policy"
    assert payload["enabled_modalities"] == []
    assert payload["required_modalities"] == []
    assert payload["safety"]["internal_harness_owned"] is False


def test_control_plane_verifier_policy_route_persists_user_enabled_modalities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", "1")
    monkeypatch.setenv("KERNELONE_MULTIMODAL_QA_ENABLED", "1")
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/v2/control-plane/verifier-policy",
        headers=_auth_headers(),
        json={
            "browser_enabled": True,
            "visual_enabled": True,
            "required_modalities": ["browser", "visual"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled_modalities"] == ["browser", "visual"]
    assert payload["required_modalities"] == ["browser", "visual"]

    saved_path = tmp_path / ".polaris" / "verifier_policy.json"
    assert saved_path.is_file()
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert saved["browser_enabled"] is True
    assert saved["visual_enabled"] is True


def test_control_plane_verifier_policy_route_rejects_required_disabled_modality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/v2/control-plane/verifier-policy",
        headers=_auth_headers(),
        json={
            "browser_enabled": False,
            "required_modalities": ["browser"],
        },
    )

    assert response.status_code == 400
    assert "enabled first" in response.text


def test_control_plane_verifier_policy_route_rejects_required_unavailable_modality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/v2/control-plane/verifier-policy",
        headers=_auth_headers(),
        json={
            "browser_enabled": True,
            "required_modalities": ["browser"],
        },
    )

    assert response.status_code == 400
    assert "not available" in response.text
