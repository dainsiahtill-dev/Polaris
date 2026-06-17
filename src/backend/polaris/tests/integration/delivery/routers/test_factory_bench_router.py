"""Contract tests for the factory-bench HTTP endpoints.

These endpoints are workspace-agnostic (the bench subprocess drives
projects across many workspaces, so its session state cannot live inside
``FactoryRunService``). They expose the ``FactoryBenchService`` so the
``scripts/factory_bench/run_factory_bench.py`` subprocess can publish
lifecycle events, and the Factory front-end panel can subscribe via SSE.
"""

from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import factory as factory_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(factory_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestFactoryBenchRouter:
    def setup_method(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {"FACTORY_BENCH_SESSIONS_ROOT": self._tmp.name}
        self._patcher = patch.dict(os.environ, self._env, clear=False)
        self._patcher.start()
        # Re-create the module-level service so it points at the temp root.
        from polaris.cells.factory.pipeline.internal import bench_service

        self._bench_patcher = patch.object(factory_router, "_bench_service", bench_service.FactoryBenchService())
        self._bench_patcher.start()
        self.client = _build_client()

    def teardown_method(self) -> None:
        self._bench_patcher.stop()
        self._patcher.stop()
        self._tmp.cleanup()

    def test_register_session_returns_session_id(self) -> None:
        response = self.client.post(
            "/v2/factory/bench/sessions",
            json={
                "work_dir": "/tmp/ws",
                "project_ids": ["L1-01", "L2-07"],
                "total": 2,
            },
        )
        assert response.status_code == 200, response.text
        payload: dict[str, Any] = response.json()
        assert payload["status"] == "running"
        assert payload["session_id"].startswith("bench-")

    def test_register_then_list_sessions(self) -> None:
        sid_a = self.client.post(
            "/v2/factory/bench/sessions",
            json={"work_dir": "/tmp/a", "project_ids": ["L1-01"], "total": 1},
        ).json()["session_id"]
        sid_b = self.client.post(
            "/v2/factory/bench/sessions",
            json={"work_dir": "/tmp/b", "project_ids": ["L2-07"], "total": 1},
        ).json()["session_id"]
        listed = self.client.get("/v2/factory/bench/sessions").json()
        ids = [s["session_id"] for s in listed["sessions"]]
        assert sid_a in ids
        assert sid_b in ids
        assert listed["total"] == len(ids)

    def test_append_event_persists_to_events_jsonl(self) -> None:
        sid = self.client.post(
            "/v2/factory/bench/sessions",
            json={"work_dir": "/tmp/ws", "project_ids": ["L1-01"], "total": 1},
        ).json()["session_id"]
        ok = self.client.post(
            f"/v2/factory/bench/sessions/{sid}/events",
            json={
                "type": "project.started",
                "name": "L1-01",
                "actor": "factory-bench",
                "summary": "L1-01 starting",
                "meta": {"level": 1},
            },
        )
        assert ok.status_code == 200 and ok.json()["appended"] is True
        snapshot = self.client.get(f"/v2/factory/bench/sessions/{sid}").json()
        assert any(e["type"] == "project.started" for e in snapshot["events"])

    def test_append_event_to_unknown_session_returns_appended_false(self) -> None:
        ok = self.client.post(
            "/v2/factory/bench/sessions/bench-missing-xyz/events",
            json={"type": "noop"},
        )
        assert ok.status_code == 200
        assert ok.json()["appended"] is False

    def test_complete_session_marks_terminal_status(self) -> None:
        sid = self.client.post(
            "/v2/factory/bench/sessions",
            json={"work_dir": "/tmp/ws", "project_ids": ["L1-01"], "total": 1},
        ).json()["session_id"]
        response = self.client.post(
            f"/v2/factory/bench/sessions/{sid}/complete",
            json={"success": True, "summary": {"passed": 1, "failed": 0}},
        )
        assert response.status_code == 200
        snapshot = self.client.get(f"/v2/factory/bench/sessions/{sid}").json()
        assert snapshot["status"] == "completed"
        assert snapshot["metadata"]["passed"] == 1

    def test_get_unknown_session_returns_404(self) -> None:
        response = self.client.get("/v2/factory/bench/sessions/bench-missing-xyz")
        assert response.status_code == 404

    def test_stream_emits_initial_status_and_events_then_done(self) -> None:
        sid = self.client.post(
            "/v2/factory/bench/sessions",
            json={"work_dir": "/tmp/ws", "project_ids": ["L1-01"], "total": 1},
        ).json()["session_id"]
        # Append two events + complete BEFORE the stream starts so the SSE
        # generator walks through status -> events -> done within its first
        # poll cycle.
        self.client.post(
            f"/v2/factory/bench/sessions/{sid}/events",
            json={"type": "project.started", "name": "L1-01", "actor": "factory-bench"},
        )
        self.client.post(
            f"/v2/factory/bench/sessions/{sid}/events",
            json={"type": "project.completed", "name": "L1-01", "actor": "factory-bench", "ok": True},
        )
        self.client.post(
            f"/v2/factory/bench/sessions/{sid}/complete",
            json={"success": True},
        )

        with self.client.stream("GET", f"/v2/factory/bench/sessions/{sid}/stream") as response:
            assert response.status_code == 200
            seen: list[tuple[str, dict[str, Any]]] = []
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    name = line[len("event: ") :].strip()
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    seen.append((name, data))
                if len(seen) >= 4 and any(n == "done" for n, _ in seen):
                    break
        names = [n for n, _ in seen]
        assert "status" in names
        assert "event" in names
        assert "done" in names
        event_types = [d.get("type") for n, d in seen if n == "event"]
        assert "project.started" in event_types
        assert "project.completed" in event_types
