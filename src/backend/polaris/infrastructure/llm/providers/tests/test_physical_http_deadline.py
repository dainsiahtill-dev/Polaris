"""Actual localhost HTTP bodies cannot outlive an admitted request budget."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest
import requests
from polaris.infrastructure.llm.providers.provider_helpers import (
    CircuitBreaker,
    _raw_blocking_http_post,
    invoke_with_retry,
)
from polaris.kernelone.llm.engine.contracts import Usage
from polaris.kernelone.llm.engine.executor import _invoke_with_timeout
from polaris.kernelone.llm.engine.invocation_budget import bind_provider_invocation_budget


@contextmanager
def _trickling_provider(observed: list[float] | None = None) -> Iterator[str]:
    """An actual server continuously sends bytes faster than the idle timeout."""
    body = json.dumps({"choices": [{"message": {"content": "delayed valid output"}}]}).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            if observed is not None:
                observed.append(time.monotonic())
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for index in range(0, len(body), 5):
                    self.wfile.write(body[index : index + 5])
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


@pytest.mark.asyncio
async def test_caller_cancellation_terminates_physical_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    started = threading.Event()
    finished = threading.Event()
    with _trickling_provider() as endpoint:

        def worker() -> None:
            started.set()
            try:
                _raw_blocking_http_post(endpoint, {}, {"messages": []}, (5.0, 5.0))
            finally:
                finished.set()

        task = asyncio.create_task(_invoke_with_timeout(asyncio.to_thread(worker), timeout=5.0))
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(finished.wait, 0.5), "cancelled await left physical worker active"


def test_deadline_stops_retries_before_a_second_physical_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    received: list[float] = []
    with _trickling_provider(received) as endpoint, bind_provider_invocation_budget(0.4):
        result = invoke_with_retry(
            endpoint,
            {},
            {"messages": []},
            (1.0, 1.0),
            retries=3,
            prompt="unit fixture",
            extract_output=lambda data: data["choices"][0]["message"]["content"],
            usage_from_response=lambda *_args: Usage.estimate("unit fixture", ""),
            circuit_breaker=CircuitBreaker(),
            backoff_base_seconds=0,
            backoff_max_seconds=0,
        )
    assert result.ok is False
    assert result.error == "provider_invocation_deadline"
    assert len(received) == 1
