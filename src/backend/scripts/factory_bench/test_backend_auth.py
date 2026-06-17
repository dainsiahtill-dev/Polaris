"""Tests for the bench subprocess Authorization header plumbing.

The Polaris factory router uses ``require_auth`` which demands a Bearer
token in the ``Authorization`` header (query tokens are intentionally
rejected for security). The bench subprocess must therefore read the
token from ``KERNELONE_TOKEN`` (or ``FACTORY_BENCH_BACKEND_TOKEN``) and
include it in every POST; without the header, the backend returns 401
and the bench falls back to local-JSONL-only emission.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from scripts.factory_bench.run_factory_bench import (
    _http_post_json,
    _push_bench_complete_to_backend,
    _push_bench_event_to_backend,
    _push_bench_session_to_backend,
    _resolve_backend_token,
)


class _AuthMockBackend(BaseHTTPRequestHandler):
    """Records Authorization header + path; 200 only when expected token present."""

    received_auth: list[str | None] = []
    received_paths: list[str] = []
    expected_token: str = ""
    response_status: int = 200
    response_body: bytes = b"{}"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        path = urlparse(self.path).path
        auth = self.headers.get("Authorization")
        _AuthMockBackend.received_auth.append(auth)
        _AuthMockBackend.received_paths.append(path)
        if self.expected_token and auth != f"Bearer {self.expected_token}":
            self.send_response(401)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class TestBenchAuth(unittest.TestCase):
    def setUp(self) -> None:
        self._server = HTTPServer(("127.0.0.1", 0), _AuthMockBackend)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        _AuthMockBackend.received_auth.clear()
        _AuthMockBackend.received_paths.clear()
        _AuthMockBackend.expected_token = ""
        _AuthMockBackend.response_status = 200
        _AuthMockBackend.response_body = (
            b'{"session_id": "bench-auth-1", "status": "running", "appended": true, "updated": true}'
        )
        self.backend_url = f"http://127.0.0.1:{self._port}"
        self.addCleanup(self._server.shutdown)
        self.addCleanup(self._server.server_close)
        self.addCleanup(self._thread.join, 1.0)

    def test_resolve_backend_token_prefers_explicit_then_env(self) -> None:
        self.assertEqual(_resolve_backend_token("explicit-token"), "explicit-token")
        with unittest.mock.patch.dict("os.environ", {"FACTORY_BENCH_BACKEND_TOKEN": "env-bench"}):
            self.assertEqual(_resolve_backend_token(), "env-bench")
        with unittest.mock.patch.dict(
            "os.environ", {"FACTORY_BENCH_BACKEND_TOKEN": "", "KERNELONE_TOKEN": "env-kernelone"}
        ):
            self.assertEqual(_resolve_backend_token(), "env-kernelone")
        with unittest.mock.patch.dict(
            "os.environ", {"FACTORY_BENCH_BACKEND_TOKEN": "env-bench", "KERNELONE_TOKEN": "env-kernelone"}
        ):
            # FACTORY_BENCH_BACKEND_TOKEN wins over KERNELONE_TOKEN.
            self.assertEqual(_resolve_backend_token(), "env-bench")
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_resolve_backend_token(), "")

    def test_push_session_sends_authorization_header(self) -> None:
        _AuthMockBackend.response_body = json.dumps({"session_id": "bench-auth-1", "status": "running"}).encode("utf-8")
        sid = _push_bench_session_to_backend(
            backend_url=self.backend_url,
            work_dir="/tmp/ws",
            project_ids=["L1-01"],
            total=1,
            token="secret-token",
        )
        self.assertEqual(sid, "bench-auth-1")
        self.assertEqual(_AuthMockBackend.received_auth, ["Bearer secret-token"])
        self.assertEqual(_AuthMockBackend.received_paths, ["/v2/factory/bench/sessions"])

    def test_push_event_sends_authorization_header(self) -> None:
        ok = _push_bench_event_to_backend(
            backend_url=self.backend_url,
            session_id="bench-auth-1",
            event_type="project.started",
            token="secret-token",
        )
        self.assertTrue(ok)
        self.assertEqual(_AuthMockBackend.received_auth, ["Bearer secret-token"])
        self.assertEqual(_AuthMockBackend.received_paths, ["/v2/factory/bench/sessions/bench-auth-1/events"])

    def test_push_complete_sends_authorization_header(self) -> None:
        ok = _push_bench_complete_to_backend(
            backend_url=self.backend_url,
            session_id="bench-auth-1",
            token="secret-token",
        )
        self.assertTrue(ok)
        self.assertEqual(_AuthMockBackend.received_auth, ["Bearer secret-token"])
        self.assertEqual(_AuthMockBackend.received_paths, ["/v2/factory/bench/sessions/bench-auth-1/complete"])

    def test_push_without_token_gets_401_and_returns_false(self) -> None:
        # No token configured; backend requires one -> 401 -> fail-soft.
        _AuthMockBackend.expected_token = "expected"
        ok = _push_bench_event_to_backend(
            backend_url=self.backend_url,
            session_id="bench-auth-1",
            event_type="project.started",
        )
        self.assertFalse(ok)
        # The auth header was sent as None (no token configured).
        self.assertEqual(_AuthMockBackend.received_auth, [None])

    def test_push_with_wrong_token_gets_401_and_returns_none(self) -> None:
        _AuthMockBackend.expected_token = "expected"
        sid = _push_bench_session_to_backend(
            backend_url=self.backend_url,
            work_dir="/tmp/ws",
            project_ids=["L1-01"],
            total=1,
            token="WRONG",
        )
        self.assertIsNone(sid)
        self.assertEqual(_AuthMockBackend.received_auth, ["Bearer WRONG"])

    def test_http_post_json_omits_header_when_token_empty(self) -> None:
        _http_post_json(f"{self.backend_url}/v2/factory/bench/sessions", {"x": 1})
        self.assertEqual(_AuthMockBackend.received_auth, [None])

    def test_http_post_json_includes_bearer_when_token_set(self) -> None:
        _http_post_json(f"{self.backend_url}/v2/factory/bench/sessions", {"x": 1}, token="abc")
        self.assertEqual(_AuthMockBackend.received_auth, ["Bearer abc"])


if __name__ == "__main__":
    unittest.main()
