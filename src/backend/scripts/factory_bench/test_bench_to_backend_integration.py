"""End-to-end integration test for bench -> Factory backend.

Runs a tiny in-process HTTP server that mimics the factory bench endpoints,
then drives ``_emit_bench_event`` from the bench subprocess and asserts that:

  * the local JSONL in the workspace is written, AND
  * the backend HTTP endpoint receives the same event.

This locks down the contract that the Factory panel can stream bench
progress for any L1-L8 run, while the WS bridge still gets the local
``runtime.events.jsonl`` copy.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from scripts.factory_bench.run_factory_bench import (
    _emit_bench_event,
    _push_bench_complete_to_backend,
    _push_bench_session_to_backend,
    configure_bench_backend,
)


class _RecordingBackend(BaseHTTPRequestHandler):
    received: list[tuple[str, dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body_raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            body = json.loads(body_raw) if body_raw else {}
        except ValueError:
            body = {"_raw": body_raw}
        path = urlparse(self.path).path
        # Simulate the real factory router responses.
        if path == "/v2/factory/bench/sessions":
            response = json.dumps({"session_id": "bench-test-001", "status": "running"}).encode("utf-8")
        elif path.endswith("/events"):
            response = json.dumps({"appended": True}).encode("utf-8")
        elif path.endswith("/complete"):
            response = json.dumps({"updated": True}).encode("utf-8")
        else:
            response = b"{}"
        _RecordingBackend.received.append((path, body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: Any) -> None:
        return


class TestBenchToBackendIntegration(unittest.TestCase):
    def setUp(self) -> None:
        # Local workspace + cache_root (the WS bridge path).
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.cache_root = Path(self._tmp.name) / "cache"
        self.run_id = "factory-bench-test-run"
        (self.cache_root / "runs" / self.run_id / "events").mkdir(parents=True)
        (self.cache_root / "latest_run.json").write_text(json.dumps({"run_id": self.run_id}), encoding="utf-8")
        # Tiny HTTP server that records every call.
        self._server = HTTPServer(("127.0.0.1", 0), _RecordingBackend)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.backend_url = f"http://127.0.0.1:{self._port}"
        _RecordingBackend.received.clear()
        self.addCleanup(self._server.shutdown)
        self.addCleanup(self._server.server_close)
        self.addCleanup(self._thread.join, 1.0)

    def test_emit_writes_local_jsonl_and_posts_to_backend(self) -> None:
        # Wire the bench subprocess state to our test backend.
        sid = _push_bench_session_to_backend(
            backend_url=self.backend_url,
            work_dir=str(self.workspace),
            project_ids=["L1-01", "L2-07"],
            total=2,
        )
        self.assertEqual(sid, "bench-test-001")
        configure_bench_backend(self.backend_url, sid)
        # Drive the same helper that main() uses.
        _emit_bench_event(
            workspace=self.workspace,
            project_id="L1-01",
            level=1,
            name="project.started",
            summary="L1-01 starting",
            cache_root=str(self.cache_root),
        )
        _emit_bench_event(
            workspace=self.workspace,
            project_id="L1-01",
            level=1,
            name="project.completed",
            summary="L1-01 done",
            cache_root=str(self.cache_root),
        )
        _emit_bench_event(
            workspace=self.workspace,
            project_id="L1-01",
            level=1,
            name="gate.evaluated",
            summary="L1-01 chain_clean ok",
            cache_root=str(self.cache_root),
        )
        # Mark the session complete (this is the main() exit step).
        ok = _push_bench_complete_to_backend(
            backend_url=self.backend_url,
            session_id=sid,
            success=True,
            summary={"total": 2, "passed": 1, "failed": 1},
        )
        self.assertTrue(ok)
        # --- local JSONL: chain subprocess path ---
        local_events_file = self.cache_root / "runs" / self.run_id / "events" / "runtime.events.jsonl"
        self.assertTrue(local_events_file.is_file())
        local_lines = [json.loads(line) for line in local_events_file.read_text(encoding="utf-8").splitlines() if line]
        local_names = [e["name"] for e in local_lines]
        self.assertEqual(
            local_names,
            [
                "factory_bench.project.started",
                "factory_bench.project.completed",
                "factory_bench.gate.evaluated",
            ],
        )
        # --- backend: factory panel SSE path ---
        backend_paths = [p for p, _ in _RecordingBackend.received]
        self.assertIn("/v2/factory/bench/sessions", backend_paths)
        event_calls = [p for p in backend_paths if p.endswith("/events")]
        self.assertEqual(len(event_calls), 3)
        self.assertTrue(any(p.endswith("/complete") for p in backend_paths))
        # The backend events must carry the same canonical names the local
        # JSONL emits — the Factory panel renders them by name.
        backend_event_types = [
            body.get("type") for path, body in _RecordingBackend.received if path.endswith("/events")
        ]
        self.assertEqual(
            backend_event_types,
            [
                "factory_bench.project.started",
                "factory_bench.project.completed",
                "factory_bench.gate.evaluated",
            ],
        )

    def test_emit_works_when_backend_unreachable(self) -> None:
        # No backend wiring -> local JSONL must still be written.
        configure_bench_backend("", "")
        ok = _emit_bench_event(
            workspace=self.workspace,
            project_id="L1-01",
            level=1,
            name="project.started",
            summary="L1-01 starting",
            cache_root=str(self.cache_root),
        )
        self.assertTrue(ok)
        local_events_file = self.cache_root / "runs" / self.run_id / "events" / "runtime.events.jsonl"
        self.assertTrue(local_events_file.is_file())
        self.assertEqual(len(_RecordingBackend.received), 0)

    def test_emit_works_when_backend_fails(self) -> None:
        # Backend pointed at a closed port -> local JSONL must still be
        # written, the bench must NOT crash.
        configure_bench_backend("http://127.0.0.1:1", "bench-test-001")
        ok = _emit_bench_event(
            workspace=self.workspace,
            project_id="L1-01",
            level=1,
            name="project.started",
            summary="L1-01 starting",
            cache_root=str(self.cache_root),
        )
        self.assertTrue(ok)
        local_events_file = self.cache_root / "runs" / self.run_id / "events" / "runtime.events.jsonl"
        self.assertTrue(local_events_file.is_file())

    def test_emit_pushes_to_backend_when_workspace_lacks_cache_root(self) -> None:
        """Real bench runtime: work_dir is a plain parent dir, not a Polaris
        workspace, so cache_root resolution fails. The backend push must
        still happen — otherwise the Factory panel sees zero events even
        though the chain ran. The local JSONL is a best-effort side
        channel; the Factory HTTP path is the canonical real-time one.
        """
        # Plain empty workspace — no .polaris, no docs/, no cache_root.
        plain_ws = Path(self._tmp.name) / "plain-parent"
        plain_ws.mkdir(parents=True, exist_ok=True)
        sid = _push_bench_session_to_backend(
            backend_url=self.backend_url,
            work_dir=str(plain_ws),
            project_ids=["L1-01"],
            total=1,
        )
        self.assertEqual(sid, "bench-test-001")
        configure_bench_backend(self.backend_url, sid)
        # DO NOT pass cache_root: this is the real bench runtime path.
        ok = _emit_bench_event(
            workspace=plain_ws,
            project_id="L1-01",
            level=1,
            name="project.started",
            summary="L1-01 starting (no cache_root)",
        )
        self.assertTrue(
            ok,
            "_emit_bench_event must succeed on the backend path even when the workspace has no Polaris cache_root",
        )
        # The backend must have received the event.
        event_calls = [p for p, _ in _RecordingBackend.received if p.endswith("/events")]
        self.assertEqual(
            len(event_calls),
            1,
            f"expected exactly one /events POST, got: {_RecordingBackend.received!r}",
        )
        # The event must carry the canonical name the Factory panel renders.
        event_body = next(body for path, body in _RecordingBackend.received if path.endswith("/events"))
        self.assertEqual(event_body.get("type"), "factory_bench.project.started")
        self.assertEqual(event_body.get("meta", {}).get("project_id"), "L1-01")


if __name__ == "__main__":
    unittest.main()
