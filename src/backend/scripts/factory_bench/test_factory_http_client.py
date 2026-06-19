from __future__ import annotations

import json
import sys
import unittest
from email.message import Message
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend/scripts/factory_bench")

from factory_http_client import (
    _http_get_json,
    _http_post_json,
    cancel_factory_run,
    get_audit_bundle,
    get_run_artifacts,
    get_run_status,
    poll_run_until_terminal,
    start_factory_run,
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


def _http_error(url: str, code: int, msg: str, retry_after: str | None = None):
    from urllib.error import HTTPError

    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(url, code, msg, headers, None)


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

    def test_get_run_status(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "running"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = get_run_status("http://localhost:49977", "run-42", token="t")
        self.assertEqual(result, {"status": "running"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://localhost:49977/v2/factory/runs/run-42")
        self.assertEqual(req.get_header("Authorization"), "Bearer t")

    def test_cancel_factory_run(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "cancelled"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = cancel_factory_run("http://localhost:49977", "run-42", reason="bench poll timeout", token="t")
        self.assertEqual(result, {"status": "cancelled"})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://localhost:49977/v2/factory/runs/run-42/control")
        self.assertEqual(req.get_header("Authorization"), "Bearer t")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["action"], "cancel")
        self.assertEqual(body["reason"], "bench poll timeout")

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


class TestPollRunUntilTerminal(unittest.TestCase):
    def test_poll_completes(self) -> None:
        responses = [
            {"status": "running"},
            {"status": "completed", "result": "ok"},
        ]
        fake_resps = [FakeHTTPResponse(json.dumps(r).encode("utf-8")) for r in responses]
        with patch("urllib.request.urlopen", side_effect=fake_resps) as mock_urlopen:
            result = poll_run_until_terminal(
                "http://localhost:49977",
                "run-42",
                token="t",
                poll_interval_s=0.01,
                timeout_s=5.0,
            )
        self.assertEqual(result, {"status": "completed", "result": "ok"})
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_poll_calls_on_status(self) -> None:
        responses = [
            {"status": "running"},
            {"status": "failed", "error": "oops"},
        ]
        fake_resps = [FakeHTTPResponse(json.dumps(r).encode("utf-8")) for r in responses]
        callback = MagicMock()
        with patch("urllib.request.urlopen", side_effect=fake_resps):
            result = poll_run_until_terminal(
                "http://localhost:49977",
                "run-42",
                token="t",
                poll_interval_s=0.01,
                timeout_s=5.0,
                on_status=callback,
            )
        self.assertEqual(result, {"status": "failed", "error": "oops"})
        self.assertEqual(callback.call_count, 2)
        callback.assert_any_call({"status": "running"})
        callback.assert_any_call({"status": "failed", "error": "oops"})

    def test_poll_timeout(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "running"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = poll_run_until_terminal(
                "http://localhost:49977",
                "run-42",
                token="t",
                poll_interval_s=0.01,
                timeout_s=0.05,
            )
        self.assertIsNone(result)

    def test_poll_none_then_timeout(self) -> None:
        from urllib.error import URLError

        with patch("urllib.request.urlopen", side_effect=URLError("network down")):
            result = poll_run_until_terminal(
                "http://localhost:49977",
                "run-42",
                token="t",
                poll_interval_s=0.01,
                timeout_s=0.05,
            )
        self.assertIsNone(result)

    def test_poll_cancelled(self) -> None:
        fake_resp = FakeHTTPResponse(json.dumps({"status": "cancelled"}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = poll_run_until_terminal(
                "http://localhost:49977",
                "run-42",
                token="t",
                poll_interval_s=0.01,
                timeout_s=5.0,
            )
        self.assertEqual(result, {"status": "cancelled"})


if __name__ == "__main__":
    unittest.main()
