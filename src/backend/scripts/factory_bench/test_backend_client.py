"""Unit tests for the bench subprocess -> backend HTTP client.

The bench runs in a terminal and pushes lifecycle events to the Factory HTTP
backend so the Factory front-end panel can stream them in real time. The
client is fail-soft: a missing/unreachable backend must NEVER crash the bench
run. These tests lock down both the happy path and the silent-failure path.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import urlparse

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from scripts.factory_bench import run_factory_bench as run_factory_bench_module
from scripts.factory_bench.run_factory_bench import (
    _push_bench_complete_to_backend,
    _push_bench_event_to_backend,
    _push_bench_session_to_backend,
    _push_bench_workspace_to_backend,
    _resolve_bench_work_dir,
)


class _MockBackend(BaseHTTPRequestHandler):
    """Tiny HTTP server that returns a per-path response and records requests."""

    received: list[tuple[str, str, dict[str, Any]]] = []
    response_status: int = 200
    # path-suffix -> response body. Most-specific match wins (longest suffix).
    path_responses: dict[str, bytes] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body_raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            body = json.loads(body_raw) if body_raw else {}
        except ValueError:
            body = {"_raw": body_raw}
        path = urlparse(self.path).path
        self.received.append((self._classify(path), path, body))
        body_bytes = self._body_for(path)
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        self.received.append(("GET", path, {}))
        body = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _classify(self, path: str) -> str:
        parts = [p for p in path.split("/") if p]
        return "/".join(parts[3:]) if len(parts) >= 4 else "/".join(parts)

    def _body_for(self, path: str) -> bytes:
        # Longest suffix wins so ``sessions/.../events`` beats ``sessions``.
        suffix = path.split("/v2/factory/bench/", 1)[-1] if "/v2/factory/bench/" in path else path
        best_key = ""
        for key in self.path_responses:
            if suffix.endswith(key) and len(key) > len(best_key):
                best_key = key
        if not best_key:
            return b"{}"
        return self.path_responses[best_key]

    def log_message(self, format: str, *args: Any) -> None:
        return  # silence test output


class _BenchBackendClientTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._server = HTTPServer(("127.0.0.1", 0), _MockBackend)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        _MockBackend.received.clear()
        _MockBackend.response_status = 200
        _MockBackend.path_responses = {
            "sessions": json.dumps({"session_id": "bench-abc", "status": "running"}).encode("utf-8"),
            "events": json.dumps({"appended": True}).encode("utf-8"),
            "complete": json.dumps({"updated": True}).encode("utf-8"),
            "/settings": json.dumps({"workspace": "/tmp/project"}).encode("utf-8"),
        }
        self.addCleanup(self._server.shutdown)
        self.addCleanup(self._server.server_close)
        self.addCleanup(self._thread.join, 1.0)
        self.backend_url = f"http://127.0.0.1:{self._port}"

    def set_response(self, *, status: int, body: dict[str, Any], path_suffix: str = "sessions") -> None:
        _MockBackend.response_status = status
        _MockBackend.path_responses[path_suffix] = json.dumps(body).encode("utf-8")


class TestBenchBackendClient(_BenchBackendClientTestBase):
    def test_register_session_posts_to_backend(self) -> None:
        sid = _push_bench_session_to_backend(
            backend_url=self.backend_url,
            work_dir="/tmp/ws",
            project_ids=["L1-01", "L2-07"],
            total=2,
            metadata={"levels": [1, 2]},
        )
        self.assertEqual(sid, "bench-abc")
        self.assertEqual(len(_MockBackend.received), 1)
        kind, _path, body = _MockBackend.received[0]
        self.assertEqual(kind, "sessions")
        self.assertEqual(_path, "/v2/factory/bench/sessions")
        self.assertEqual(body["work_dir"], "/tmp/ws")
        self.assertEqual(body["project_ids"], ["L1-01", "L2-07"])
        self.assertEqual(body["total"], 2)

    def test_append_event_posts_to_session_events(self) -> None:
        ok = _push_bench_event_to_backend(
            backend_url=self.backend_url,
            session_id="bench-xyz",
            event_type="project.started",
            name="L1-01",
            actor="factory-bench",
            summary="L1-01 starting",
            meta={"level": 1, "project_id": "L1-01", "session_id": "bench-xyz"},
        )
        self.assertTrue(ok)
        self.assertEqual(len(_MockBackend.received), 1)
        kind, _path, body = _MockBackend.received[0]
        self.assertEqual(kind, "sessions/bench-xyz/events")
        self.assertEqual(body["type"], "project.started")
        self.assertEqual(body["name"], "L1-01")
        self.assertEqual(body["meta"]["level"], 1)

    def test_complete_posts_to_session_complete(self) -> None:
        ok = _push_bench_complete_to_backend(
            backend_url=self.backend_url,
            session_id="bench-xyz",
            success=True,
            summary={"passed": 1, "failed": 0},
        )
        self.assertTrue(ok)
        self.assertEqual(len(_MockBackend.received), 1)
        kind, _path, body = _MockBackend.received[0]
        self.assertEqual(kind, "sessions/bench-xyz/complete")
        self.assertEqual(body["success"], True)
        self.assertEqual(body["summary"]["passed"], 1)

    def test_workspace_switch_posts_to_settings_before_project_observation(self) -> None:
        ok = _push_bench_workspace_to_backend(
            backend_url=self.backend_url,
            workspace="/tmp/project",
        )
        self.assertTrue(ok)
        self.assertEqual(len(_MockBackend.received), 1)
        kind, path, body = _MockBackend.received[0]
        self.assertEqual(kind, "settings")
        self.assertEqual(path, "/settings")
        self.assertEqual(body["workspace"], "/tmp/project")

    def test_workspace_switch_retries_transient_settings_failure(self) -> None:
        calls: list[dict[str, Any]] = []

        def fake_post_json(url: str, payload: dict[str, Any], *, token: str = "") -> dict[str, Any] | None:
            self.assertEqual(url, f"{self.backend_url}/settings")
            self.assertEqual(token, "dev-token")
            calls.append(payload)
            if len(calls) == 1:
                return None
            return {"workspace": payload["workspace"]}

        with mock.patch.object(run_factory_bench_module, "_http_post_json", side_effect=fake_post_json):
            ok = run_factory_bench_module._push_bench_workspace_to_backend(
                backend_url=self.backend_url,
                workspace="/tmp/project",
                token="dev-token",
                attempts=2,
                retry_delay_seconds=0,
            )

        self.assertTrue(ok)
        self.assertEqual(calls, [{"workspace": "/tmp/project"}, {"workspace": "/tmp/project"}])

    def test_relative_work_dir_resolves_from_repo_root_not_process_cwd(self) -> None:
        previous_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                os.chdir(tmp_dir)
                resolved = _resolve_bench_work_dir("runtime/factory-bench/bench-x")
            finally:
                os.chdir(previous_cwd)

        expected = Path("/home/dains/Documents/polaris/runtime/factory-bench/bench-x").resolve()
        self.assertEqual(resolved, expected)

    def test_unreachable_backend_returns_none_silently(self) -> None:
        sid = _push_bench_session_to_backend(
            backend_url="http://127.0.0.1:1",
            work_dir="/tmp/ws",
            project_ids=[],
            total=0,
        )
        self.assertIsNone(sid)

    def test_unreachable_backend_event_returns_false(self) -> None:
        ok = _push_bench_event_to_backend(
            backend_url="http://127.0.0.1:1",
            session_id="bench-xyz",
            event_type="noop",
        )
        self.assertFalse(ok)

    def test_unreachable_backend_complete_returns_false(self) -> None:
        ok = _push_bench_complete_to_backend(
            backend_url="http://127.0.0.1:1",
            session_id="bench-xyz",
        )
        self.assertFalse(ok)

    def test_non_2xx_response_is_treated_as_failure(self) -> None:
        self.set_response(status=500, body={"error": "boom"})
        sid = _push_bench_session_to_backend(
            backend_url=self.backend_url,
            work_dir="/tmp/ws",
            project_ids=[],
            total=0,
        )
        self.assertIsNone(sid)

    def test_malformed_session_id_response_is_treated_as_failure(self) -> None:
        self.set_response(status=200, body={"oops": "no session_id"})
        sid = _push_bench_session_to_backend(
            backend_url=self.backend_url,
            work_dir="/tmp/ws",
            project_ids=[],
            total=0,
        )
        self.assertIsNone(sid)


if __name__ == "__main__":
    unittest.main()
