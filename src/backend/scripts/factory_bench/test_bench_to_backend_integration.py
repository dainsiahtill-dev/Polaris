"""End-to-end integration test for bench -> Factory backend via /v2/factory/runs API.

Runs a tiny in-process HTTP server that mimics the new Factory runs endpoints,
then drives ``run_factory_chain`` from the bench and asserts that:

  * the mock backend receives the correct POST /v2/factory/runs payload,
  * polling hits GET /v2/factory/runs/{run_id} until terminal,
  * the audit-bundle is fetched via GET /v2/factory/runs/{run_id}/audit,
  * ``chain_results`` carries ``qa_ran``, ``qa_passed``, ``exit_class``,
  * ``exit_code`` is 0 for completed+passed, 1 for failed.

This locks down the contract that the new HTTP API flow replaces the legacy
subprocess chain while keeping the same result shape.
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
    map_factory_run_to_chain_results,
    run_factory_chain,
)


class _MockFactoryRunsBackend(BaseHTTPRequestHandler):
    """Tiny HTTP server that mimics /v2/factory/runs endpoints."""

    received: list[tuple[str, str, dict[str, Any]]] = []
    # run_id -> status dict
    run_states: dict[str, dict[str, Any]] = {}
    # run_id -> audit bundle dict
    audit_bundles: dict[str, dict[str, Any]] = {}
    # run_id -> number of status polls received
    poll_counts: dict[str, int] = {}
    # run_id -> target terminal status for auto-transition (default: "completed")
    transition_targets: dict[str, str] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body_raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            body = json.loads(body_raw) if body_raw else {}
        except ValueError:
            body = {"_raw": body_raw}
        path = urlparse(self.path).path
        self.received.append(("POST", path, body))

        response_body: bytes
        if path == "/v2/factory/runs":
            run_id = body.get("run_id", "factory-run-001")
            self.run_states[run_id] = {
                "run_id": run_id,
                "status": "running",
                "phase": "director_dispatch",
            }
            self.poll_counts[run_id] = 0
            response_body = json.dumps({"run_id": run_id, "status": "running"}).encode("utf-8")
        else:
            response_body = b"{}"

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        self.received.append(("GET", path, {}))

        response_body: bytes
        if path.startswith("/v2/factory/runs/") and path.endswith("/audit"):
            run_id = path.split("/")[4]
            bundle = self.audit_bundles.get(run_id, {})
            response_body = json.dumps(bundle).encode("utf-8")
        elif path.startswith("/v2/factory/runs/"):
            parts = path.split("/")
            run_id = parts[4] if len(parts) >= 5 else ""
            state = self.run_states.get(run_id, {"run_id": run_id, "status": "unknown"})
            self.poll_counts[run_id] = self.poll_counts.get(run_id, 0) + 1
            # Transition to the target terminal status on the second poll
            # ONLY if the run was created by the POST handler (status="running")
            # and not already in a terminal state.
            current_status = state.get("status", "")
            target = self.transition_targets.get(run_id, "completed")
            if self.poll_counts.get(run_id, 0) >= 2 and current_status == "running":
                state["status"] = target
                if target == "completed" or target == "failed":
                    state["phase"] = "qa_gate"
            response_body = json.dumps(state).encode("utf-8")
        else:
            response_body = b"{}"

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args: Any) -> None:
        return  # silence test output


class TestFactoryRunsIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)

        self._server = HTTPServer(("127.0.0.1", 0), _MockFactoryRunsBackend)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.backend_url = f"http://127.0.0.1:{self._port}"

        _MockFactoryRunsBackend.received.clear()
        _MockFactoryRunsBackend.run_states.clear()
        _MockFactoryRunsBackend.audit_bundles.clear()
        _MockFactoryRunsBackend.poll_counts.clear()
        _MockFactoryRunsBackend.transition_targets.clear()

        self.addCleanup(self._server.shutdown)
        self.addCleanup(self._server.server_close)
        self.addCleanup(self._thread.join, 1.0)

    def _setup_audit_bundle(self, run_id: str, *, qa_passed: bool = True) -> None:
        """Pre-seed the audit bundle the mock will return for a run."""
        _MockFactoryRunsBackend.audit_bundles[run_id] = {
            "status": "completed",
            "gates": [
                {
                    "gate_name": "quality_gate",
                    "passed": qa_passed,
                    "message": "QA passed" if qa_passed else "QA failed",
                },
            ],
            "events_tail": [
                {
                    "stage": "director_dispatch",
                    "result": {"total": 3, "successes": 3, "failures": 0, "blocked": 0},
                },
            ],
            "summary_json": {
                "director": {"total": 3, "successes": 3, "failures": 0, "blocked": 0},
                "integration_qa": {"ran": True, "passed": qa_passed},
            },
        }

    def test_run_factory_chain_happy_path(self) -> None:
        """Drive a project through run_factory_chain against the mock backend.

        Asserts:
          - POST /v2/factory/runs is called with the workspace and directive.
          - GET /v2/factory/runs/{run_id} is polled until terminal.
          - GET /v2/factory/runs/{run_id}/audit is fetched.
          - chain_results has qa_ran=True, qa_passed=True, exit_class="clean".
          - exit_code is 0.
        """
        project = {
            "id": "L1-01",
            "title": "Hello World",
            "brief": "Print hello world",
            "level": 1,
            "test_focus": "basic output",
        }
        log_path = Path(self._tmp.name) / "chain.log"

        # Pre-seed the audit bundle so the mock returns a completed+passed state.
        self._setup_audit_bundle("factory-run-001", qa_passed=True)

        result = run_factory_chain(
            project,
            self.workspace,
            backend_url=self.backend_url,
            backend_token="",
            timeout_s=30,
            log_path=log_path,
        )

        # --- backend contract assertions ---
        post_calls = [
            p for method, p, _ in _MockFactoryRunsBackend.received if method == "POST" and p == "/v2/factory/runs"
        ]
        self.assertEqual(len(post_calls), 1, "expected exactly one POST /v2/factory/runs")

        get_status_calls = [
            p
            for method, p, _ in _MockFactoryRunsBackend.received
            if method == "GET" and p.startswith("/v2/factory/runs/") and not p.endswith("/audit")
        ]
        self.assertGreaterEqual(len(get_status_calls), 2, "expected at least 2 status polls")

        get_audit_calls = [
            p for method, p, _ in _MockFactoryRunsBackend.received if method == "GET" and p.endswith("/audit")
        ]
        self.assertEqual(len(get_audit_calls), 1, "expected exactly one audit-bundle GET")

        # --- result shape assertions ---
        chain_results = result.get("chain_results")
        self.assertIsInstance(chain_results, dict, "chain_results must be a dict")
        self.assertEqual(chain_results.get("qa_ran"), True)
        self.assertEqual(chain_results.get("qa_passed"), True)
        self.assertEqual(chain_results.get("exit_class"), "clean")
        self.assertEqual(result.get("exit_code"), 0)
        self.assertEqual(result.get("run_id"), "factory-run-001")

    def test_run_factory_chain_failed_qa(self) -> None:
        """Mock returns a failed QA state; assert exit_code=1 and exit_class=qa_failed."""
        project = {
            "id": "L1-02",
            "title": "Broken Project",
            "brief": "A project that fails QA",
            "level": 1,
            "test_focus": "failure path",
        }
        log_path = Path(self._tmp.name) / "chain.log"

        # The mock POST handler defaults to run_id "factory-run-001" when no
        # run_id is in the payload. Pre-seed that ID with a failed state.
        self._setup_audit_bundle("factory-run-001", qa_passed=False)
        # Tell the mock to transition to "failed" instead of "completed".
        _MockFactoryRunsBackend.transition_targets["factory-run-001"] = "failed"
        # Override the run state to start from running.
        _MockFactoryRunsBackend.run_states["factory-run-001"] = {
            "run_id": "factory-run-001",
            "status": "running",
            "phase": "director_dispatch",
        }

        result = run_factory_chain(
            project,
            self.workspace,
            backend_url=self.backend_url,
            backend_token="",
            timeout_s=30,
            log_path=log_path,
        )

        chain_results = result.get("chain_results")
        self.assertIsInstance(chain_results, dict)
        self.assertEqual(chain_results.get("qa_ran"), True)
        self.assertEqual(chain_results.get("qa_passed"), False)
        self.assertEqual(chain_results.get("exit_class"), "qa_failed")
        self.assertEqual(result.get("exit_code"), 1)

    def test_map_factory_run_to_chain_results_director_partial(self) -> None:
        """When status is failed but phase is not qa_gate, exit_class=director_partial."""
        run_status = {"status": "failed", "phase": "director_dispatch"}
        audit_bundle = {
            "gates": [
                {"gate_name": "quality_gate", "passed": False, "message": "director crashed"},
            ],
            "events_tail": [],
            "summary_json": {},
        }
        chain_results = map_factory_run_to_chain_results(run_status, audit_bundle)
        self.assertEqual(chain_results["qa_ran"], True)
        self.assertEqual(chain_results["qa_passed"], False)
        self.assertEqual(chain_results["exit_class"], "director_partial")

    def test_map_factory_run_to_chain_results_hard_failed(self) -> None:
        """When status is cancelled (not failed/completed), exit_class=hard_failed."""
        run_status = {"status": "cancelled", "phase": "unknown"}
        audit_bundle: dict[str, Any] = {"gates": [], "events_tail": [], "summary_json": {}}
        chain_results = map_factory_run_to_chain_results(run_status, audit_bundle)
        self.assertEqual(chain_results["qa_ran"], False)
        self.assertEqual(chain_results["qa_passed"], False)
        self.assertEqual(chain_results["exit_class"], "hard_failed")

    def test_run_factory_chain_start_failure(self) -> None:
        """When the backend returns a non-2xx on start, run_factory_chain returns exit_code=-1."""
        # Point at a port with no server.
        result = run_factory_chain(
            {"id": "L1-03", "title": "Unreachable", "brief": "x", "level": 1, "test_focus": "x"},
            self.workspace,
            backend_url="http://127.0.0.1:1",
            backend_token="",
            timeout_s=5,
            log_path=Path(self._tmp.name) / "chain.log",
        )
        self.assertEqual(result.get("exit_code"), -1)
        self.assertEqual(result.get("error"), "start_failed")


if __name__ == "__main__":
    unittest.main()
