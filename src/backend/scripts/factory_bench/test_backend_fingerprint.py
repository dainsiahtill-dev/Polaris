"""Tests for backend_fingerprint: resolver, metadata, stale fail-closed, fresh pass.

Covers:
  - compute_source_fingerprint deterministic hashing
  - resolve_backend_fingerprint with mock HTTP
  - check_backend_freshness gate logic (fresh / stale / unreachable / missing)
  - build_run_backend_metadata structure
  - stale_backend_or_unknown gate integration in build_factory_bench_gates
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from scripts.factory_bench.backend_fingerprint import (
    build_run_backend_metadata,
    check_backend_freshness,
    compute_source_fingerprint,
    resolve_backend_fingerprint,
    resolve_token_source,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FingerprintBackend(BaseHTTPRequestHandler):
    """Mock backend that returns configurable fingerprint responses."""

    responses: dict[str, dict[str, Any]] = {}
    request_log: list[tuple[str, str]] = []

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        self.request_log.append(("GET", path))
        body = self.responses.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _start_mock_backend(responses: dict[str, dict[str, Any]]) -> tuple[HTTPServer, str, Thread]:
    """Start a mock HTTP server and return (server, base_url, thread)."""
    _FingerprintBackend.responses = responses
    _FingerprintBackend.request_log = []
    server = HTTPServer(("127.0.0.1", 0), _FingerprintBackend)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}", thread


def _stop_mock_backend(server: HTTPServer) -> None:
    server.shutdown()


# ---------------------------------------------------------------------------
# Tests: compute_source_fingerprint
# ---------------------------------------------------------------------------


class TestComputeSourceFingerprint(unittest.TestCase):
    """Test deterministic source fingerprint computation."""

    def test_returns_empty_for_missing_root(self) -> None:
        result = compute_source_fingerprint("/nonexistent/path")
        self.assertEqual(result, "")

    def test_returns_empty_for_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = compute_source_fingerprint(tmpdir)
            self.assertEqual(result, "")

    def test_deterministic_for_same_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "polaris" / "delivery").mkdir(parents=True)
            (root / "polaris" / "delivery" / "server.py").write_text("print('hello')", encoding="utf-8")
            fp1 = compute_source_fingerprint(root, sources=("polaris/delivery",))
            fp2 = compute_source_fingerprint(root, sources=("polaris/delivery",))
            self.assertEqual(fp1, fp2)
            self.assertTrue(len(fp1) > 0)

    def test_changes_on_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("v1", encoding="utf-8")
            fp1 = compute_source_fingerprint(root, sources=("src",))
            (root / "src" / "a.py").write_text("v2", encoding="utf-8")
            fp2 = compute_source_fingerprint(root, sources=("src",))
            self.assertNotEqual(fp1, fp2)

    def test_includes_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "deep" / "nested" / "dir").mkdir(parents=True)
            (root / "deep" / "nested" / "dir" / "mod.py").write_text("x=1", encoding="utf-8")
            fp = compute_source_fingerprint(root, sources=("deep",))
            self.assertTrue(len(fp) > 0)

    def test_ignores_non_py_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            root.joinpath("data.txt").write_text("not python", encoding="utf-8")
            fp = compute_source_fingerprint(root, sources=("data.txt",))
            # data.txt is not a .py file but is a direct file source, so it IS included
            self.assertTrue(len(fp) > 0)


# ---------------------------------------------------------------------------
# Tests: resolve_backend_fingerprint
# ---------------------------------------------------------------------------


class TestResolveBackendFingerprint(unittest.TestCase):
    """Test backend fingerprint resolution via HTTP."""

    def test_unreachable_backend(self) -> None:
        result = resolve_backend_fingerprint("http://127.0.0.1:1", timeout_s=0.5)
        self.assertFalse(result["reachable"])
        self.assertEqual(result["fingerprint"], "")
        self.assertEqual(result["source"], "unreachable")

    def test_fingerprint_endpoint(self) -> None:
        server, url, _ = _start_mock_backend(
            {
                "/v2/runtime/fingerprint": {
                    "fingerprint": "abc123def456",
                    "pid": 12345,
                    "startup_time": "2026-06-21T00:00:00Z",
                    "workspace": "/tmp/test",
                },
            }
        )
        try:
            result = resolve_backend_fingerprint(url, timeout_s=2.0)
            self.assertTrue(result["reachable"])
            self.assertEqual(result["fingerprint"], "abc123def456")
            self.assertEqual(result["pid"], 12345)
            self.assertEqual(result["source"], "runtime/fingerprint")
        finally:
            _stop_mock_backend(server)

    def test_health_fallback(self) -> None:
        server, url, _ = _start_mock_backend(
            {
                "/v2/runtime/fingerprint": {},  # empty = no fingerprint field
                "/v2/health": {
                    "ok": True,
                    "fingerprint": "health_fp_789",
                    "timestamp": "2026-06-21T00:00:00Z",
                },
            }
        )
        try:
            result = resolve_backend_fingerprint(url, timeout_s=2.0)
            self.assertTrue(result["reachable"])
            self.assertEqual(result["fingerprint"], "health_fp_789")
            self.assertEqual(result["source"], "health")
        finally:
            _stop_mock_backend(server)

    def test_no_fingerprint_anywhere(self) -> None:
        server, url, _ = _start_mock_backend(
            {
                "/v2/runtime/fingerprint": {},
                "/v2/health": {"ok": True},
            }
        )
        try:
            result = resolve_backend_fingerprint(url, timeout_s=2.0)
            self.assertTrue(result["reachable"])
            self.assertEqual(result["fingerprint"], "")
        finally:
            _stop_mock_backend(server)


# ---------------------------------------------------------------------------
# Tests: check_backend_freshness
# ---------------------------------------------------------------------------


class TestCheckBackendFreshness(unittest.TestCase):
    """Test freshness gate logic: fresh, stale, unreachable, missing."""

    def test_fresh_when_fingerprints_match(self) -> None:
        server, url, _ = _start_mock_backend(
            {
                "/v2/runtime/fingerprint": {
                    "fingerprint": "match_me",
                    "pid": 100,
                    "startup_time": "2026-06-21T00:00:00Z",
                },
            }
        )
        try:
            result = check_backend_freshness(
                url,
                expected_fingerprint="match_me",
                timeout_s=2.0,
            )
            self.assertTrue(result["ok"])
            self.assertIn("fresh", result["detail"])
            self.assertEqual(result["gate"], "stale_backend_or_unknown")
        finally:
            _stop_mock_backend(server)

    def test_stale_when_fingerprints_differ(self) -> None:
        server, url, _ = _start_mock_backend(
            {
                "/v2/runtime/fingerprint": {
                    "fingerprint": "old_process",
                    "pid": 99,
                    "startup_time": "2026-06-20T00:00:00Z",
                },
            }
        )
        try:
            result = check_backend_freshness(
                url,
                expected_fingerprint="new_source",
                timeout_s=2.0,
            )
            self.assertFalse(result["ok"])
            self.assertIn("STALE", result["detail"])
            self.assertEqual(result["expected_fingerprint"], "new_source")
            self.assertEqual(result["actual_fingerprint"], "old_process")
        finally:
            _stop_mock_backend(server)

    def test_fail_closed_when_unreachable(self) -> None:
        result = check_backend_freshness(
            "http://127.0.0.1:1",
            expected_fingerprint="fp",
            timeout_s=0.5,
        )
        self.assertFalse(result["ok"])
        self.assertIn("unreachable", result["detail"])

    def test_fail_closed_when_fingerprint_missing(self) -> None:
        server, url, _ = _start_mock_backend(
            {
                "/v2/runtime/fingerprint": {},
                "/v2/health": {"ok": True},
            }
        )
        try:
            result = check_backend_freshness(
                url,
                expected_fingerprint="fp",
                timeout_s=2.0,
            )
            self.assertFalse(result["ok"])
            self.assertIn("missing", result["detail"])
        finally:
            _stop_mock_backend(server)

    def test_fail_closed_when_local_fingerprint_empty(self) -> None:
        server, url, _ = _start_mock_backend(
            {
                "/v2/runtime/fingerprint": {
                    "fingerprint": "backend_has_fp",
                    "pid": 1,
                    "startup_time": "",
                },
            }
        )
        try:
            result = check_backend_freshness(
                url,
                expected_fingerprint="",
                timeout_s=2.0,
            )
            # Empty local fingerprint => fail closed (STALE or unavailable)
            self.assertFalse(result["ok"])
        finally:
            _stop_mock_backend(server)


# ---------------------------------------------------------------------------
# Tests: resolve_token_source
# ---------------------------------------------------------------------------


class TestResolveTokenSource(unittest.TestCase):
    def test_explicit_arg(self) -> None:
        self.assertEqual(resolve_token_source(explicit="my_token"), "arg:explicit")

    def test_env_token(self) -> None:
        label = resolve_token_source(env_token="env_tok")
        self.assertEqual(label, "env:FACTORY_BENCH_BACKEND_TOKEN")

    def test_desktop_token(self) -> None:
        label = resolve_token_source(desktop_token="dtok")
        self.assertEqual(label, "desktop-backend.json")

    def test_default_local(self) -> None:
        label = resolve_token_source(is_local=True)
        self.assertEqual(label, "default_local")

    def test_none(self) -> None:
        label = resolve_token_source()
        self.assertEqual(label, "none")


# ---------------------------------------------------------------------------
# Tests: build_run_backend_metadata
# ---------------------------------------------------------------------------


class TestBuildRunBackendMetadata(unittest.TestCase):
    def test_structure(self) -> None:
        meta = build_run_backend_metadata(
            "http://127.0.0.1:49977",
            token_source="default_local",
            workspace="/tmp/ws",
            expected_fingerprint="abc",
            actual_fingerprint="abc",
            backend_pid=1234,
            backend_startup_time="2026-06-21T00:00:00Z",
            fingerprint_source="runtime/fingerprint",
        )
        self.assertEqual(meta["backend_base_url"], "http://127.0.0.1:49977")
        self.assertEqual(meta["token_source"], "default_local")
        self.assertEqual(meta["workspace"], "/tmp/ws")
        self.assertEqual(meta["expected_source_fingerprint"], "abc")
        self.assertEqual(meta["actual_backend_fingerprint"], "abc")
        self.assertEqual(meta["backend_pid"], 1234)
        self.assertIn("recorded_at", meta)


# ---------------------------------------------------------------------------
# Tests: stale_backend_or_unknown gate in build_factory_bench_gates
# ---------------------------------------------------------------------------


class TestStaleGateIntegration(unittest.TestCase):
    """Test that the stale_backend_or_unknown gate is included in factory bench gates."""

    def _import_gate_builder(self) -> Any:
        from scripts.factory_bench.run_factory_bench import build_factory_bench_gates

        return build_factory_bench_gates

    def test_gate_present_when_freshness_ok(self) -> None:
        build_factory_bench_gates = self._import_gate_builder()
        record: dict[str, Any] = {
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "wrong_product_suspect": False,
            "real_run_gate": {"ok": True},
            "llm_route_audit": {"ok": True},
            "backend_freshness": {"ok": True, "detail": "fresh (fingerprint=abc)"},
            "chain_results": {"qa_ran": True, "qa_passed": True},
            "chain_state": "clean",
        }
        chain: dict[str, Any] = {"exit_code": 0}
        gates = build_factory_bench_gates(record, chain)
        fp_gate = next(g for g in gates if g["gate"] == "stale_backend_or_unknown")
        self.assertTrue(fp_gate["ok"])

    def test_gate_fails_when_stale(self) -> None:
        build_factory_bench_gates = self._import_gate_builder()
        record: dict[str, Any] = {
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "wrong_product_suspect": False,
            "real_run_gate": {"ok": True},
            "llm_route_audit": {"ok": True},
            "backend_freshness": {"ok": False, "detail": "STALE backend"},
            "chain_results": {"qa_ran": True, "qa_passed": True},
            "chain_state": "clean",
        }
        chain: dict[str, Any] = {"exit_code": 0}
        gates = build_factory_bench_gates(record, chain)
        fp_gate = next(g for g in gates if g["gate"] == "stale_backend_or_unknown")
        self.assertFalse(fp_gate["ok"])
        self.assertIn("STALE", fp_gate["detail"])

    def test_gate_fails_when_missing(self) -> None:
        build_factory_bench_gates = self._import_gate_builder()
        record: dict[str, Any] = {
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "wrong_product_suspect": False,
            "real_run_gate": {"ok": True},
            "llm_route_audit": {"ok": True},
            # No backend_freshness key at all
            "chain_results": {"qa_ran": True, "qa_passed": True},
            "chain_state": "clean",
        }
        chain: dict[str, Any] = {"exit_code": 0}
        gates = build_factory_bench_gates(record, chain)
        fp_gate = next(g for g in gates if g["gate"] == "stale_backend_or_unknown")
        self.assertFalse(fp_gate["ok"])
        self.assertIn("missing", fp_gate["detail"])


if __name__ == "__main__":
    unittest.main()
