from __future__ import annotations

import asyncio
import io
import json
import sys
import unittest
from email.message import Message
from typing import Any
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend/scripts/factory_bench")

import factory_http_client as factory_http_client_mod
from factory_http_client import (
    _factory_event_payload,
    _http_get_json,
    _http_post_json,
    _http_terminal_status_snapshot,
    _runtime_ws_url,
    _status_from_factory_event,
    _subscribe_factory_events,
    _wait_run_until_terminal_async,
    cancel_factory_run,
    get_audit_bundle,
    get_run_artifacts,
    get_run_status,
    start_factory_run,
    wait_run_until_terminal,
)


class FakeHTTPResponse:
    """Minimal fake HTTP response for urllib.request.urlopen mocking."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _http_error(url: str, code: int, msg: str, retry_after: str | None = None, body: bytes | None = None):
    from urllib.error import HTTPError

    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(url, code, msg, headers, io.BytesIO(body or b""))


class TestHTTPPostJson(unittest.TestCase):
    def test_post_success(self) -> None:
        payload = {"ok": True}
        fake_resp = FakeHTTPResponse(json.dumps({"result": "created"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = _http_post_json("http://localhost:49977/v2/factory/runs", payload)
        self.assertEqual(result, {"result": "created"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertIsNone(req.get_header("Authorization"))

    def test_post_with_token(self) -> None:
        payload = {"ok": True}
        fake_resp = FakeHTTPResponse(json.dumps({"result": "created"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = _http_post_json("http://localhost:49977/v2/factory/runs", payload, token="secret123")
        self.assertEqual(result, {"result": "created"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer secret123")

    def test_post_401(self) -> None:
        payload = {"ok": True}
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("http://localhost:49977/v2/factory/runs", 401, "Unauthorized"),
        ):
            result = _http_post_json("http://localhost:49977/v2/factory/runs", payload, token="bad")
        self.assertIsNone(result)

    def test_post_retries_429_retry_after(self) -> None:
        payload = {"ok": True}
        fake_resp = FakeHTTPResponse(json.dumps({"result": "created"}).encode("utf-8"))
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[
                    _http_error("http://localhost:49977/v2/factory/runs", 429, "Too Many Requests", "0"),
                    fake_resp,
                ],
            ) as mock_urlopen,
            patch("time.sleep") as sleep_mock,
        ):
            result = _http_post_json("http://localhost:49977/v2/factory/runs", payload, max_retries=1)
        self.assertEqual(result, {"result": "created"})
        self.assertEqual(mock_urlopen.call_count, 2)
        sleep_mock.assert_called_once_with(0.0)

    def test_post_timeout(self) -> None:
        from urllib.error import URLError

        payload = {"ok": True}
        with patch(
            "urllib.request.urlopen",
            side_effect=URLError("timed out"),
        ):
            result = _http_post_json("http://localhost:49977/v2/factory/runs", payload, timeout_s=1.0)
        self.assertIsNone(result)

    def test_post_url_error_return_errors_includes_exception(self) -> None:
        from urllib.error import URLError

        payload = {"ok": True}
        with patch(
            "urllib.request.urlopen",
            side_effect=URLError("connection refused"),
        ):
            result = _http_post_json(
                "http://localhost:49977/v2/factory/runs",
                payload,
                timeout_s=1.0,
                return_errors=True,
            )

        self.assertIsInstance(result, dict)
        error = result["_http_error"]
        self.assertEqual(error["status"], 0)
        self.assertEqual(error["exception"], "URLError")
        self.assertIn("connection refused", error["reason"])

    def test_post_malformed_json(self) -> None:
        fake_resp = FakeHTTPResponse(b"not json")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = _http_post_json("http://localhost:49977/v2/factory/runs", {})
        self.assertIsNone(result)

    def test_post_empty_body(self) -> None:
        fake_resp = FakeHTTPResponse(b"")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = _http_post_json("http://localhost:49977/v2/factory/runs", {})
        self.assertEqual(result, {})

    def test_post_custom_timeout(self) -> None:
        payload = {"ok": True}
        fake_resp = FakeHTTPResponse(json.dumps({"result": "created"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            _http_post_json("http://localhost:49977/v2/factory/runs", payload, timeout_s=30.0)
        self.assertEqual(mock_urlopen.call_args[1]["timeout"], 30.0)


class TestHTTPGetJson(unittest.TestCase):
    def test_get_success(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "running"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = _http_get_json("http://localhost:49977/v2/factory/runs/123")
        self.assertEqual(result, {"status": "running"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertIsNone(req.get_header("Authorization"))

    def test_get_with_token(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "running"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = _http_get_json("http://localhost:49977/v2/factory/runs/123", token="secret123")
        self.assertEqual(result, {"status": "running"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer secret123")

    def test_get_401(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("http://localhost:49977/v2/factory/runs/123", 401, "Unauthorized"),
        ):
            result = _http_get_json("http://localhost:49977/v2/factory/runs/123", token="bad")
        self.assertIsNone(result)

    def test_get_retries_429_retry_after(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "running"}).encode("utf-8"))
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[
                    _http_error("http://localhost:49977/v2/factory/runs/123", 429, "Too Many Requests", "0"),
                    fake_resp,
                ],
            ) as mock_urlopen,
            patch("time.sleep") as sleep_mock,
        ):
            result = _http_get_json("http://localhost:49977/v2/factory/runs/123", max_retries=1)
        self.assertEqual(result, {"status": "running"})
        self.assertEqual(mock_urlopen.call_count, 2)
        sleep_mock.assert_called_once_with(0.0)

    def test_get_timeout(self) -> None:
        from urllib.error import URLError

        with patch(
            "urllib.request.urlopen",
            side_effect=URLError("timed out"),
        ):
            result = _http_get_json("http://localhost:49977/v2/factory/runs/123", timeout_s=1.0)
        self.assertIsNone(result)

    def test_get_malformed_json(self) -> None:
        fake_resp = FakeHTTPResponse(b"not json")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = _http_get_json("http://localhost:49977/v2/factory/runs/123")
        self.assertIsNone(result)

    def test_get_empty_body(self) -> None:
        fake_resp = FakeHTTPResponse(b"")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = _http_get_json("http://localhost:49977/v2/factory/runs/123")
        self.assertEqual(result, {})


class TestHelpers(unittest.TestCase):
    def test_start_factory_run(self) -> None:
        payload = {"project_id": "abc"}
        fake_resp = FakeHTTPResponse(json.dumps({"run_id": "r1"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = start_factory_run("http://localhost:49977", payload, token="t")
        self.assertEqual(result, {"run_id": "r1"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://localhost:49977/v2/factory/runs")
        self.assertEqual(req.get_header("Authorization"), "Bearer t")

    def test_start_factory_run_preserves_http_error_body(self) -> None:
        payload = {"project_id": "abc"}
        error_body = {
            "error": {
                "code": "RUNTIME_ROLES_NOT_READY",
                "details": {
                    "role_issues": {
                        "director": "director binding (qwen-a/qwen3.6-27b-gpu0) LLM not ready; run tests first"
                    }
                },
            }
        }
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(
                "http://localhost:49977/v2/factory/runs",
                409,
                "Conflict",
                body=json.dumps(error_body).encode("utf-8"),
            ),
        ):
            result = start_factory_run("http://localhost:49977", payload, token="t")

        assert isinstance(result, dict)
        assert result["_http_error"]["status"] == 409
        assert result["_http_error"]["json"] == error_body

    def test_get_run_status(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "running"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = get_run_status("http://localhost:49977", "run-42", token="t")
        self.assertEqual(result, {"status": "running"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://localhost:49977/v2/factory/runs/run-42")
        self.assertEqual(req.get_header("Authorization"), "Bearer t")

    def test_get_run_status_with_workspace(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "running"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = get_run_status(
                "http://localhost:49977",
                "run-42",
                token="t",
                workspace="/tmp/factory bench/L1-01",
            )
        self.assertEqual(result, {"status": "running"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(
            req.full_url,
            "http://localhost:49977/v2/factory/runs/run-42?workspace=%2Ftmp%2Ffactory%20bench%2FL1-01",
        )

    def test_cancel_factory_run(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "cancelled"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = cancel_factory_run(
                "http://localhost:49977",
                "run-42",
                reason="bench event wait timeout",
                token="t",
                workspace="/tmp/ws",
            )
        self.assertEqual(result, {"status": "cancelled"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://localhost:49977/v2/factory/runs/run-42/control?workspace=%2Ftmp%2Fws")
        self.assertEqual(req.get_header("Authorization"), "Bearer t")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["action"], "cancel")
        self.assertEqual(body["reason"], "bench event wait timeout")

    def test_cancel_factory_run_can_return_connection_error_payload(self) -> None:
        from urllib.error import URLError

        with patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            result = cancel_factory_run(
                "http://localhost:49977",
                "run-42",
                reason="bench event wait timeout",
                return_errors=True,
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["_http_error"]["exception"], "URLError")
        self.assertIn("connection refused", result["_http_error"]["reason"])

    def test_get_audit_bundle(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"audit": "data"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = get_audit_bundle("http://localhost:49977", "run-42", token="t")
        self.assertEqual(result, {"audit": "data"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://localhost:49977/v2/factory/runs/run-42/audit-bundle")

    def test_get_run_artifacts(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"artifacts": []}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = get_run_artifacts("http://localhost:49977", "run-42", token="t")
        self.assertEqual(result, {"artifacts": []})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://localhost:49977/v2/factory/runs/run-42/artifacts")


class TestEventWaitUntilTerminal(unittest.TestCase):
    def test_runtime_ws_url_encodes_workspace(self) -> None:
        self.assertEqual(
            _runtime_ws_url("http://localhost:49977", token="t", workspace="/tmp/factory bench/L1-01"),
            "ws://localhost:49977/v2/ws/runtime?protocol=runtime.v2&token=t&workspace=%2Ftmp%2Ffactory%20bench%2FL1-01",
        )

    def test_factory_event_payload_accepts_pinned_channel(self) -> None:
        message = {
            "type": "EVENT",
            "cursor": 12,
            "event": {
                "channel": "event.factory:run-42",
                "run_id": "run-42",
                "payload": {"type": "completed", "timestamp": "2026-06-19T00:00:00"},
            },
        }

        result = _factory_event_payload(message, "run-42")

        self.assertEqual(result, (12, {"type": "completed", "timestamp": "2026-06-19T00:00:00", "run_id": "run-42"}))

    def test_subscribe_factory_events_includes_pinned_channel(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.sent: list[dict[str, object]] = []

            async def send(self, payload: str) -> None:
                self.sent.append(json.loads(payload))

            async def recv(self) -> str:
                return json.dumps({"type": "SUBSCRIBED"})

        ws = FakeWebSocket()

        asyncio.run(_subscribe_factory_events(ws, run_id="run-42", workspace="/tmp/ws"))

        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(ws.sent[0]["channels"], ["event.factory", "event.factory:run-42"])

    def test_factory_event_payload_rejects_other_run(self) -> None:
        message = {
            "type": "EVENT",
            "event": {
                "channel": "event.factory:run-99",
                "run_id": "run-99",
                "payload": {"type": "completed"},
            },
        }

        self.assertIsNone(_factory_event_payload(message, "run-42"))

    def test_status_from_factory_event_tracks_terminal_status(self) -> None:
        status = _status_from_factory_event(
            "run-42",
            {"type": "stage_started", "stage": "director_dispatch"},
            {},
        )
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["phase"], "director_dispatch")

        completed = _status_from_factory_event("run-42", {"type": "completed"}, status)
        self.assertEqual(completed["status"], "completed")

    def test_status_from_factory_event_exposes_raw_event_payload(self) -> None:
        payload = {
            "type": "task_runtime_execution",
            "stage": "director_dispatch",
            "task_id": "TASK-1",
            "status": "in_progress",
        }

        status = _status_from_factory_event("run-42", payload, {})

        self.assertEqual(status["status"], "running")
        self.assertEqual(status["event_type"], "task_runtime_execution")
        self.assertEqual(status["event_payload"], payload)

    def test_wait_run_until_terminal_delegates_to_runtime_v2_waiter(self) -> None:
        async def _fake_wait(
            backend_url: str,
            run_id: str,
            *,
            token: str = "",
            workspace: str = "",
            timeout_s: float = 5400.0,
            on_status=None,
            initial_status=None,
            return_diagnostics: bool = False,
        ):
            self.assertEqual(backend_url, "http://localhost:49977")
            self.assertEqual(run_id, "run-42")
            self.assertEqual(token, "t")
            self.assertEqual(workspace, "/tmp/ws")
            self.assertEqual(timeout_s, 5.0)
            self.assertEqual(initial_status, {"status": "running"})
            self.assertFalse(return_diagnostics)
            if on_status is not None:
                on_status({"run_id": run_id, "status": "completed"})
            return {"run_id": run_id, "status": "completed"}

        seen: list[dict[str, str]] = []
        with patch("factory_http_client._wait_run_until_terminal_async", _fake_wait):
            result = wait_run_until_terminal(
                "http://localhost:49977",
                "run-42",
                token="t",
                workspace="/tmp/ws",
                timeout_s=5.0,
                initial_status={"status": "running"},
                on_status=seen.append,
            )

        self.assertEqual(result, {"run_id": "run-42", "status": "completed"})
        self.assertEqual(seen, [{"run_id": "run-42", "status": "completed"}])

    def test_wait_run_until_terminal_returns_diagnostics_on_runtime_error(self) -> None:
        async def _fake_wait(*args, **kwargs):
            raise RuntimeError("received 1012 (service restart)")

        with (
            patch("factory_http_client._wait_run_until_terminal_async", _fake_wait),
            patch("factory_http_client.get_run_status", return_value=None),
        ):
            result = wait_run_until_terminal(
                "http://localhost:49977",
                "run-42",
                workspace="/tmp/ws",
                initial_status={"status": "running", "phase": "director_dispatch"},
                return_diagnostics=True,
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["run_id"], "run-42")
        self.assertEqual(result["phase"], "director_dispatch")
        self.assertEqual(result["_event_wait_error"]["kind"], "runtime_error")
        self.assertIn("service restart", result["_event_wait_error"]["message"])

    def test_wait_run_until_terminal_http_poll_recovers_missed_ws_terminal(self) -> None:
        """When runtime.v2 misses the terminal event, HTTP status must still close the wait."""

        http_snapshot = {
            "run_id": "run-42",
            "status": "failed",
            "phase": "director_dispatch",
            "completed_at": "2026-07-25T14:00:57Z",
        }

        with patch("factory_http_client.get_run_status", return_value=http_snapshot):
            terminal = _http_terminal_status_snapshot(
                "http://localhost:49977",
                "run-42",
                workspace="/tmp/ws",
                latest_status={"status": "running"},
            )
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(terminal["_terminal_source"], "http_status_poll")

        class _FakeWS:
            async def __aenter__(self) -> _FakeWS:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def recv(self) -> str:
                # Never deliver a usable factory event; force the HTTP poll path.
                await asyncio.sleep(0.05)
                raise asyncio.TimeoutError

        # First HTTP poll (pre-WS) already sees failed → returns without needing WS events.
        with patch.object(factory_http_client_mod, "get_run_status", return_value=http_snapshot):
            result = asyncio.run(
                _wait_run_until_terminal_async(
                    "http://localhost:49977",
                    "run-42",
                    workspace="/tmp/ws",
                    timeout_s=2.0,
                    initial_status={"status": "running"},
                )
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result.get("_terminal_source"), "http_status_poll")

        # Mid-wait HTTP poll: pre-WS poll empty, then WS recv times out, then HTTP terminal.
        poll_state = {"n": 0}

        def _poll_then_terminal(*_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
            poll_state["n"] += 1
            if poll_state["n"] == 1:
                return {"run_id": "run-42", "status": "running"}
            return http_snapshot

        with (
            patch.object(factory_http_client_mod, "websockets") as ws_mod,
            patch.object(factory_http_client_mod, "_subscribe_factory_events", new=AsyncMock()),
            patch.object(factory_http_client_mod, "get_run_status", side_effect=_poll_then_terminal),
        ):
            ws_mod.connect = lambda *a, **k: _FakeWS()
            result2 = asyncio.run(
                _wait_run_until_terminal_async(
                    "http://localhost:49977",
                    "run-42",
                    workspace="/tmp/ws",
                    timeout_s=2.0,
                    initial_status={"status": "running"},
                )
            )
        self.assertIsNotNone(result2)
        assert result2 is not None
        self.assertEqual(result2["status"], "failed")
        self.assertEqual(result2.get("_terminal_source"), "http_status_poll")
        self.assertGreaterEqual(poll_state["n"], 2)

    def test_wait_run_until_terminal_returns_immediate_terminal_initial_status(self) -> None:
        result = asyncio.run(
            _wait_run_until_terminal_async(
                "http://localhost:49977",
                "run-42",
                initial_status={"status": "failed", "phase": "director_dispatch"},
                timeout_s=1.0,
            )
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["run_id"], "run-42")

    def test_terminal_snapshot_falls_back_to_durable_run_json_when_http_times_out(
        self,
    ) -> None:
        """R63: HTTP GETs timed out during complete_run; durable run.json is SSoT."""

        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            runtime = Path(tmp) / "runtime"
            run_dir = runtime / "factory" / "run-42"
            run_dir.mkdir(parents=True)
            workspace.mkdir()
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "id": "run-42",
                        "status": "failed",
                        "completed_at": "2026-07-26T00:00:00+00:00",
                        "metadata": {"current_stage": "director_dispatch"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            fake_roots = SimpleNamespace(runtime_root=str(runtime))

            with (
                patch("factory_http_client.get_run_status", return_value=None),
                patch(
                    "polaris.kernelone.storage.resolve_storage_roots",
                    return_value=fake_roots,
                ),
            ):
                from factory_http_client import _http_terminal_status_snapshot

                terminal = _http_terminal_status_snapshot(
                    "http://localhost:49984",
                    "run-42",
                    workspace=str(workspace),
                    latest_status={"status": "running"},
                )

            self.assertIsNotNone(terminal)
            assert terminal is not None
            self.assertEqual(terminal["status"], "failed")
            self.assertEqual(terminal["_terminal_source"], "durable_run_json")
            self.assertEqual(terminal["run_id"], "run-42")


if __name__ == "__main__":
    unittest.main()
