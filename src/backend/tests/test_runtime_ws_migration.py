from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.bootstrap.config import Settings
from fastapi.testclient import TestClient
from polaris.delivery.http.app_factory import create_app
from polaris.delivery.ws.endpoints.protocol_utils import (
    resolve_runtime_v2_workspace_key as _resolve_runtime_v2_workspace_key,
)
from polaris.infrastructure.log_pipeline.writer import LogEventWriter
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.storage.io_paths import build_cache_root
from starlette.websockets import WebSocketDisconnect


def _create_test_app(tmp_path, monkeypatch) -> tuple[object, str]:
    token = "runtime-ws-migration-token"
    monkeypatch.setenv("POLARIS_TOKEN", token)

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        workspace=str(workspace),
        json_log_path="runtime/events/pm.events.jsonl",
    )
    return create_app(settings), token


def test_runtime_ws_endpoint_available(tmp_path, monkeypatch) -> None:
    app, token = _create_test_app(tmp_path, monkeypatch)

    with TestClient(app) as client, client.websocket_connect(f"/v2/ws/runtime?token={token}") as ws:
        payload = json.loads(ws.receive_text())

    assert payload.get("type") == "status"
    assert isinstance(payload.get("pm_status"), dict)
    assert isinstance(payload.get("director_status"), dict)


def test_legacy_ws_endpoint_removed(tmp_path, monkeypatch) -> None:
    app, token = _create_test_app(tmp_path, monkeypatch)

    with TestClient(app) as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_text()


def test_legacy_v2_director_ws_endpoint_removed(tmp_path, monkeypatch) -> None:
    app, token = _create_test_app(tmp_path, monkeypatch)

    with TestClient(app) as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/v2/ws/director?token={token}") as ws:
            ws.receive_text()


def test_runtime_ws_journal_snapshot_routes_each_line_once(tmp_path, monkeypatch) -> None:
    app, token = _create_test_app(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    cache_root = Path(build_cache_root("", str(workspace)))
    run_id = "pm-00001"
    latest_run_path = cache_root / "latest_run.json"
    journal_path = cache_root / "runs" / run_id / "logs" / "journal.norm.jsonl"

    latest_run_path.parent.mkdir(parents=True, exist_ok=True)
    latest_run_path.write_text(json.dumps({"run_id": run_id}, ensure_ascii=False), encoding="utf-8")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "schema_version": 2,
            "event_id": "evt-system",
            "run_id": run_id,
            "seq": 1,
            "channel": "system",
            "domain": "system",
            "severity": "info",
            "kind": "observation",
            "actor": "System",
            "message": "system-line",
        },
        {
            "schema_version": 2,
            "event_id": "evt-process",
            "run_id": run_id,
            "seq": 2,
            "channel": "process",
            "domain": "process",
            "severity": "info",
            "kind": "output",
            "actor": "Process",
            "message": "process-line",
        },
        {
            "schema_version": 2,
            "event_id": "evt-llm",
            "run_id": run_id,
            "seq": 3,
            "channel": "llm",
            "domain": "llm",
            "severity": "info",
            "kind": "observation",
            "actor": "PM",
            "message": "llm-line",
        },
    ]
    journal_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n",
        encoding="utf-8",
    )

    with TestClient(app) as client, client.websocket_connect(f"/v2/ws/runtime?token={token}") as ws:
        status_payload = json.loads(ws.receive_text())
        assert status_payload.get("type") == "status"

        stream_messages = [json.loads(ws.receive_text()) for _ in range(3)]

    assert len([m for m in stream_messages if m.get("type") == "process_stream" and m.get("channel") == "system"]) == 1
    assert len([m for m in stream_messages if m.get("type") == "process_stream" and m.get("channel") == "process"]) == 1
    assert len([m for m in stream_messages if m.get("type") == "llm_stream" and m.get("channel") == "llm"]) == 1


def test_runtime_ws_realtime_fanout_pushes_llm_stream_without_file_poll_delay(tmp_path, monkeypatch) -> None:
    app, token = _create_test_app(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    cache_root = Path(build_cache_root("", str(workspace)))
    run_id = "pm-rt-0001"
    latest_run_path = cache_root / "latest_run.json"
    latest_run_path.parent.mkdir(parents=True, exist_ok=True)
    latest_run_path.write_text(json.dumps({"run_id": run_id}, ensure_ascii=False), encoding="utf-8")

    with TestClient(app) as client, client.websocket_connect(f"/v2/ws/runtime?token={token}") as ws:
        status_payload = json.loads(ws.receive_text())
        assert status_payload.get("type") == "status"

        writer = LogEventWriter(workspace=str(workspace), run_id=run_id)
        writer.write_event(
            message="realtime-llm",
            channel="llm",
            domain="llm",
            actor="pm",
            raw={"stream_event": "thinking_chunk", "content": "hello"},
        )

        llm_payload = None
        for _ in range(8):
            payload = ws.receive_json()
            if payload.get("type") == "llm_stream" and payload.get("channel") == "llm":
                llm_payload = payload
                break
        assert llm_payload is not None
        assert llm_payload.get("event", {}).get("message") == "realtime-llm"


def test_runtime_v2_workspace_key_uses_connection_workspace_context(tmp_path) -> None:
    workspace = tmp_path / "expense-tracker"
    workspace.mkdir(parents=True, exist_ok=True)

    resolved_key = _resolve_runtime_v2_workspace_key(
        connection_workspace=str(workspace),
        requested_workspace="expense-tracker",
    )

    assert resolved_key == resolve_storage_roots(str(workspace)).workspace_key
