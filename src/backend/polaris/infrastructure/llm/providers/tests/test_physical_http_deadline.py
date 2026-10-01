"""Actual localhost HTTP bodies cannot outlive an admitted request budget."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest
import requests
from polaris.infrastructure.llm.providers.provider_helpers import _raw_blocking_http_post


@contextmanager
def _trickling_provider() -> Iterator[str]:
    """An actual server continuously sends bytes faster than the idle timeout."""
    body = json.dumps({"choices": [{"message": {"content": "delayed valid output"}}]}).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for index in range(0, len(body), 5):
                    self.wfile.write(body[index:index + 5])
                    self.wfile.flush()
                    time.sleep(0.10)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/chat"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_nonstream_http_total_budget_not_reset_by_body_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing idle-only transport back in must expose a late successful body."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    with _trickling_provider() as endpoint:
        start = time.monotonic()
        with pytest.raises(requests.Timeout):
            _raw_blocking_http_post(endpoint, {}, {"messages": []}, (0.4, 0.4))
        assert time.monotonic() - start < 1.0


def test_nonstream_http_fast_response_preserves_response_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    with _trickling_provider() as endpoint:
        response = _raw_blocking_http_post(endpoint, {}, {"messages": []}, (2.0, 5.0))
        assert response.ok is True
        assert response.status_code == 200
        assert response.json() == {"choices": [{"message": {"content": "delayed valid output"}}]}
