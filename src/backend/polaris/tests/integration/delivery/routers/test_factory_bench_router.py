"""Contract tests for the factory-bench HTTP endpoints.

These endpoints are workspace-agnostic (the bench subprocess drives
projects across many workspaces, so its session state cannot live inside
``FactoryRunService``). They expose the ``FactoryBenchService`` so the
``scripts/factory_bench/run_factory_bench.py`` subprocess can publish
lifecycle events, and the Factory front-end panel can subscribe via the
unified Nats-JetStream WebSocket transport.
"""

from __future__ import annotations

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

    def test_append_event_publishes_to_jetstream(self) -> None:
        """Lock the real-time push contract: publish_to_jetstream must
        receive a JSON-serializable payload (a ``dict``, NOT a
        ``RuntimeEventEnvelope`` dataclass) so the front-end actually
        sees the events. A previous bug passed the dataclass straight
        to ``publish_to_jetstream``; ``client.publish_js`` calls
        ``json.dumps(payload)`` which raised ``TypeError``, the
        call-site ``except (RuntimeError, ValueError, TypeError)``
        silently swallowed it, and the response reported
        ``published: false`` while the JSONL was still written — the
        front-end therefore never received a single event despite a
        perfectly normal-looking bench run. ``publish_to_jetstream``
        is mocked here to dodge the platform's default NATS client's
        5-second connect-timeout race that flakes the test in the
        full-file run; the production code path is exercised by
        ``tests/integration/scripts/factory_bench/test_e2e_unified_ws.py``.
        """
        from dataclasses import is_dataclass

        from polaris.delivery.http.routers import factory as factory_router

        captured: dict[str, object] = {}

        async def fake_publish(subject: str, payload):
            captured["subject"] = subject
            captured["payload"] = payload
            return True

        with patch.object(factory_router, "publish_to_jetstream", fake_publish):
            sid = self.client.post(
                "/v2/factory/bench/sessions",
                json={"work_dir": "/tmp/ws", "project_ids": ["L1-02"], "total": 1},
            ).json()["session_id"]
            resp = self.client.post(
                f"/v2/factory/bench/sessions/{sid}/events",
                json={
                    "type": "project.started",
                    "name": "L1-02",
                    "actor": "factory-bench",
                    "summary": "L1-02 starting",
                    "meta": {"level": 1},
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["appended"] is True, body
        assert body.get("published") is True, (
            f"expected published=True, got {body!r}. The bench "
            "subprocess -> backend -> NATS contract is broken: events "
            "are being durably written to JSONL but never make it onto "
            "the JetStream subject ``hp.runtime.bench.<session_id>``."
        )
        # The contract: payload must be a dict, not a dataclass, so
        # ``client.publish_js`` -> ``json.dumps`` actually works.
        assert captured["subject"] == f"hp.runtime.bench.{sid}"
        assert isinstance(captured["payload"], dict), (
            f"publish_to_jetstream must receive a dict for JSON "
            f"serialization, got {type(captured['payload']).__name__}. "
            f"value={captured['payload']!r}"
        )
        # Belt and braces: even if a future refactor re-introduces the
        # dataclass, fail loud and clear.
        assert not is_dataclass(captured["payload"]), (
            "publish_to_jetstream must NOT receive a "
            "RuntimeEventEnvelope dataclass — it would fail to JSON-"
            "serialize downstream and the call-site's broad except "
            "would silently swallow the TypeError."
        )
        # Sanity: the envelope carries the canonical shape.
        envelope = captured["payload"]
        assert envelope["schema_version"] == "runtime.v2"
        assert envelope["channel"] == f"event.bench:{sid}", envelope["channel"]
        assert envelope["kind"] == "project.started"
        assert envelope["workspace_key"] == "bench"
        assert envelope["run_id"] == sid

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

    def test_complete_session_publishes_terminal_event_to_jetstream(self) -> None:
        from polaris.delivery.http.routers import factory as factory_router

        captured: dict[str, object] = {}

        async def fake_publish(subject: str, payload):
            captured["subject"] = subject
            captured["payload"] = payload
            return True

        sid = self.client.post(
            "/v2/factory/bench/sessions",
            json={"work_dir": "/tmp/ws", "project_ids": ["L1-01", "L1-02"], "total": 2},
        ).json()["session_id"]

        with patch.object(factory_router, "publish_to_jetstream", fake_publish):
            response = self.client.post(
                f"/v2/factory/bench/sessions/{sid}/complete",
                json={
                    "success": False,
                    "summary": {"passed": 0, "failed": 2, "completed": 0},
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["updated"] is True
        assert body["published"] is True
        assert captured["subject"] == f"hp.runtime.bench.{sid}"
        envelope = captured["payload"]
        assert isinstance(envelope, dict)
        assert envelope["channel"] == f"event.bench:{sid}"
        assert envelope["kind"] == "factory_bench.run.failed"
        assert envelope["payload"]["type"] == "factory_bench.run.failed"
        assert envelope["payload"]["meta"]["status"] == "failed"
        assert envelope["payload"]["meta"]["failed"] == 2

    def test_get_unknown_session_returns_404(self) -> None:
        response = self.client.get("/v2/factory/bench/sessions/bench-missing-xyz")
        assert response.status_code == 404

    def test_progress_endpoint_updates_counters(self) -> None:
        sid = self.client.post(
            "/v2/factory/bench/sessions",
            json={"work_dir": "/tmp/ws", "project_ids": ["L1-01", "L2-07"], "total": 2},
        ).json()["session_id"]
        # 1 passed, 0 failed.
        response = self.client.post(
            f"/v2/factory/bench/sessions/{sid}/progress",
            json={"completed": 1, "failed": 0},
        )
        assert response.status_code == 200
        assert response.json()["updated"] is True
        snapshot = self.client.get(f"/v2/factory/bench/sessions/{sid}").json()
        assert snapshot["completed"] == 1
        assert snapshot["failed"] == 0
        # Update again with both counters.
        self.client.post(
            f"/v2/factory/bench/sessions/{sid}/progress",
            json={"completed": 1, "failed": 1},
        )
        snapshot = self.client.get(f"/v2/factory/bench/sessions/{sid}").json()
        assert snapshot["completed"] == 1
        assert snapshot["failed"] == 1

    def test_progress_endpoint_partial_update(self) -> None:
        sid = self.client.post(
            "/v2/factory/bench/sessions",
            json={"work_dir": "/tmp/ws", "project_ids": ["L1-01"], "total": 1},
        ).json()["session_id"]
        # Update only failed, leave completed untouched.
        self.client.post(
            f"/v2/factory/bench/sessions/{sid}/progress",
            json={"failed": 1},
        )
        snapshot = self.client.get(f"/v2/factory/bench/sessions/{sid}").json()
        assert snapshot["completed"] == 0
        assert snapshot["failed"] == 1

    def test_progress_endpoint_unknown_session(self) -> None:
        response = self.client.post(
            "/v2/factory/bench/sessions/bench-missing-xyz/progress",
            json={"completed": 1, "failed": 0},
        )
        assert response.status_code == 200
        assert response.json()["updated"] is False
