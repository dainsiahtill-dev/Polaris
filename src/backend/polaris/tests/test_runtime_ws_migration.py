from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.delivery.http.app_factory import create_app
from polaris.delivery.ws.endpoints.protocol_utils import (
    resolve_runtime_v2_workspace_key as _resolve_runtime_v2_workspace_key,
)
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.storage.io_paths import build_cache_root
from starlette.websockets import WebSocketDisconnect


def _create_test_app(tmp_path, monkeypatch) -> tuple[FastAPI, str]:
    token = "runtime-ws-migration-token"
    monkeypatch.setenv("KERNELONE_TOKEN", token)

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_cache = tmp_path / "runtime-cache"
    runtime_cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(runtime_cache))

    settings = Settings(
        workspace=workspace,
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

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"/ws?token={token}") as ws,
    ):
        ws.receive_text()


def test_legacy_v2_director_ws_endpoint_removed(tmp_path, monkeypatch) -> None:
    app, token = _create_test_app(tmp_path, monkeypatch)

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"/v2/ws/director?token={token}") as ws,
    ):
        ws.receive_text()


def test_runtime_ws_event_query_returns_journal_rows_once(tmp_path, monkeypatch) -> None:
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
        ws.send_json(
            {
                "type": "EVENT",
                "protocol": "runtime.v2",
                "action": "query",
                "run_id": run_id,
                "limit": 10,
            }
        )
        query_payload = ws.receive_json()

    assert query_payload.get("type") == "event"
    assert query_payload.get("action") == "query_result"
    assert [event.get("message") for event in query_payload.get("events", [])] == [
        "system-line",
        "process-line",
        "llm-line",
    ]


def test_runtime_ws_runtime_v2_subscribe_pushes_jetstream_event(tmp_path, monkeypatch) -> None:
    from polaris.delivery.ws.endpoints import protocol
    from polaris.infrastructure.messaging.nats.nats_types import RuntimeEventEnvelope

    app, token = _create_test_app(tmp_path, monkeypatch)

    class _FakeJetStreamConsumerManager:
        is_connected = False

        def __init__(self, **_kwargs) -> None:
            self.is_connected = False
            self.disconnected = False
            self.delivered = False

        async def connect(self) -> bool:
            self.is_connected = True
            return True

        async def next_message(self, timeout: float | None = None) -> RuntimeEventEnvelope | None:
            assert timeout is None
            if self.delivered:
                await asyncio.sleep(60)
                return None
            self.delivered = True
            return RuntimeEventEnvelope(
                workspace_key="workspace",
                channel="llm",
                kind="llm.observation",
                cursor=123,
                payload={"message": "jetstream-llm"},
            )

        def consume_dropped(self) -> int:
            return 0

        async def disconnect(self) -> None:
            self.disconnected = True
            self.is_connected = False

    monkeypatch.setattr(protocol, "JetStreamConsumerManager", _FakeJetStreamConsumerManager)

    with TestClient(app) as client, client.websocket_connect(f"/v2/ws/runtime?token={token}") as ws:
        status_payload = json.loads(ws.receive_text())
        assert status_payload.get("type") == "status"
        ws.send_json(
            {
                "type": "SUBSCRIBE",
                "protocol": "runtime.v2",
                "channels": ["llm"],
                "roles": ["pm"],
                "tail": 0,
            }
        )
        subscribed_payload = ws.receive_json()
        event_payload = ws.receive_json()

    assert subscribed_payload.get("type") == "SUBSCRIBED"
    assert subscribed_payload.get("protocol") == "runtime.v2"
    assert event_payload.get("type") == "EVENT"
    assert event_payload.get("protocol") == "runtime.v2"
    assert event_payload.get("event", {}).get("payload", {}).get("message") == "jetstream-llm"


def test_runtime_ws_v2_subscribe_requires_jetstream(tmp_path, monkeypatch) -> None:
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager

    async def _jetstream_unavailable(self) -> bool:
        del self
        return False

    monkeypatch.setattr(JetStreamConsumerManager, "connect", _jetstream_unavailable)

    app, token = _create_test_app(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    cache_root = Path(build_cache_root("", str(workspace)))
    run_id = "pm-v2-local-fallback"
    latest_run_path = cache_root / "latest_run.json"
    latest_run_path.parent.mkdir(parents=True, exist_ok=True)
    latest_run_path.write_text(json.dumps({"run_id": run_id}, ensure_ascii=False), encoding="utf-8")

    with TestClient(app) as client, client.websocket_connect(f"/v2/ws/runtime?token={token}") as ws:
        status_payload = json.loads(ws.receive_text())
        assert status_payload.get("type") == "status"

        ws.send_json(
            {
                "type": "SUBSCRIBE",
                "protocol": "runtime.v2",
                "channels": ["llm"],
                "roles": ["pm", "director", "qa"],
                "tail": 100,
            }
        )
        subscribed_payload = ws.receive_json()
        assert subscribed_payload.get("type") == "ERROR"
        assert subscribed_payload.get("protocol") == "runtime.v2"
        assert subscribed_payload.get("payload", {}).get("code") == "JETSTREAM_REQUIRED"


def test_runtime_v2_workspace_key_uses_connection_workspace_context(tmp_path) -> None:
    workspace = tmp_path / "expense-tracker"
    workspace.mkdir(parents=True, exist_ok=True)

    resolved_key = _resolve_runtime_v2_workspace_key(
        connection_workspace=str(workspace),
        requested_workspace="expense-tracker",
    )

    assert resolved_key == resolve_storage_roots(str(workspace)).workspace_key
