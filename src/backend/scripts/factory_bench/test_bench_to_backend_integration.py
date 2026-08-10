"""Integration test for bench -> Factory backend via /v2/factory/runs API.

Runs a tiny in-process HTTP server that mimics the new Factory runs endpoints,
then drives ``run_factory_chain`` from the bench and asserts that:

  * the mock backend receives the correct POST /v2/factory/runs payload,
  * runtime.v2 event waiting is invoked for the created run,
  * the audit-bundle is fetched via GET /v2/factory/runs/{run_id}/audit-bundle,
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
from unittest.mock import patch
from urllib.parse import urlparse

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from scripts.factory_bench.run_factory_bench import (
    build_requirements_doc,
    map_factory_run_to_chain_results,
    read_factory_qa_invocation_status,
    required_llm_roles_for_factory_record,
    run_factory_chain,
)


class _MockFactoryRunsBackend(BaseHTTPRequestHandler):
    """Tiny HTTP server that mimics /v2/factory/runs endpoints."""

    received: list[tuple[str, str, dict[str, Any]]] = []
    # run_id -> status dict
    run_states: dict[str, dict[str, Any]] = {}
    # run_id -> audit bundle dict
    audit_bundles: dict[str, dict[str, Any]] = {}
    # Class-level counter for unique run_ids across tests
    _run_id_counter: int = 0

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
            _MockFactoryRunsBackend._run_id_counter += 1
            run_id = body.get("run_id", f"factory-run-{_MockFactoryRunsBackend._run_id_counter:03d}")
            self.run_states[run_id] = {
                "run_id": run_id,
                "status": "running",
                "phase": "director_dispatch",
            }
            response_body = json.dumps({"run_id": run_id, "status": "running"}).encode("utf-8")
        elif path.startswith("/v2/factory/runs/") and path.endswith("/control"):
            run_id = path.split("/")[4]
            state = self.run_states.setdefault(run_id, {"run_id": run_id})
            if body.get("action") == "cancel":
                state["status"] = "cancelled"
                state["phase"] = "cancelled"
                state["failure"] = {"detail": body.get("reason") or ""}
            response_body = json.dumps(state).encode("utf-8")
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
        if path.startswith("/v2/factory/runs/") and path.endswith("/audit-bundle"):
            run_id = path.split("/")[4]
            bundle = self.audit_bundles.get(run_id, {})
            response_body = json.dumps(bundle).encode("utf-8")
        elif path.startswith("/v2/factory/runs/"):
            parts = path.split("/")
            run_id = parts[4] if len(parts) >= 5 else ""
            state = self.run_states.get(run_id, {"run_id": run_id, "status": "unknown"})
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
        _MockFactoryRunsBackend._run_id_counter = 0

        self.addCleanup(self._server.shutdown)
        self.addCleanup(self._server.server_close)
        self.addCleanup(self._thread.join, 1.0)

    def test_requirements_doc_preserves_entrypoint_and_deterministic_checks(self) -> None:
        project = {
            "id": "L1-01",
            "title": "Glow Garden",
            "brief": "Build a TypeScript visual simulation.",
            "level": 1,
            "test_focus": "entrypoint and core rules",
            "checks": ["html", "ts_syntax", "package_scripts"],
        }

        doc = build_requirements_doc(project)

        self.assertIn("真实可执行入口", doc)
        self.assertIn("<html>", doc)
        self.assertIn("package.json 脚本不得是只检查 manifest 的占位脚本", doc)
        self.assertIn("## Deterministic Checks", doc)
        self.assertIn("- html", doc)
        self.assertIn("- ts_syntax", doc)
        self.assertIn("- package_scripts", doc)

    def _expected_run_id(self) -> str:
        """Return the run_id the mock will assign on the next POST.

        The counter is reset to 0 in setUp; the first POST increments it to 1.
        """
        return "factory-run-001"

    def _setup_audit_bundle(
        self,
        run_id: str,
        *,
        qa_passed: bool = True,
        status: str = "completed",
    ) -> None:
        """Pre-seed the audit bundle the mock will return for a run."""
        _MockFactoryRunsBackend.audit_bundles[run_id] = {
            "status": status,
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
          - runtime.v2 event waiting is called with the run id and workspace.
          - GET /v2/factory/runs/{run_id}/audit-bundle is fetched.
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
        run_id = self._expected_run_id()
        self._setup_audit_bundle(run_id, qa_passed=True)

        with patch(
            "scripts.factory_bench.run_factory_bench.wait_run_until_terminal",
            return_value={"run_id": run_id, "status": "completed", "phase": "qa_gate"},
        ) as wait_mock:
            result = run_factory_chain(
                project,
                self.workspace,
                backend_url=self.backend_url,
                backend_token="",
                timeout_s=30,
                log_path=log_path,
            )

        # --- backend contract assertions ---
        post_records = [
            (method, p, body)
            for method, p, body in _MockFactoryRunsBackend.received
            if method == "POST" and p == "/v2/factory/runs"
        ]
        self.assertEqual(len(post_records), 1, "expected exactly one POST /v2/factory/runs")
        post_body = post_records[0][2]
        self.assertEqual(post_body.get("workspace"), str(self.workspace))
        self.assertEqual(post_body.get("run_director"), True)
        self.assertIn("directive", post_body)
        self.assertEqual(post_body.get("start_from"), "pm")
        self.assertEqual(post_body.get("loop"), False)

        wait_mock.assert_called_once()
        self.assertEqual(wait_mock.call_args.args[:2], (self.backend_url, run_id))
        self.assertEqual(wait_mock.call_args.kwargs.get("workspace"), str(self.workspace))
        self.assertEqual(wait_mock.call_args.kwargs.get("return_diagnostics"), True)

        get_status_calls = [
            p
            for method, p, _ in _MockFactoryRunsBackend.received
            if method == "GET" and p.startswith("/v2/factory/runs/") and not p.endswith("/audit-bundle")
        ]
        self.assertEqual(len(get_status_calls), 0, "status GET must not be used for realtime waiting")

        get_audit_calls = [
            p for method, p, _ in _MockFactoryRunsBackend.received if method == "GET" and p.endswith("/audit-bundle")
        ]
        self.assertEqual(len(get_audit_calls), 1, "expected exactly one audit-bundle GET")

        # --- result shape assertions ---
        chain_results = result.get("chain_results")
        self.assertIsInstance(chain_results, dict, "chain_results must be a dict")
        self.assertEqual(chain_results.get("qa_ran"), True)
        self.assertEqual(chain_results.get("qa_passed"), True)
        self.assertEqual(chain_results.get("exit_class"), "clean")
        self.assertEqual(result.get("exit_code"), 0)
        self.assertEqual(result.get("run_id"), run_id)

    def test_run_factory_chain_forwards_stage_changes(self) -> None:
        project = {
            "id": "L1-01",
            "title": "Hello World",
            "brief": "Print hello world",
            "level": 1,
            "test_focus": "basic output",
        }
        log_path = Path(self._tmp.name) / "chain.log"
        run_id = self._expected_run_id()
        self._setup_audit_bundle(run_id, qa_passed=True)
        stage_events: list[tuple[str, str]] = []

        def _fake_wait(*args: Any, **kwargs: Any) -> dict[str, Any]:
            on_status = kwargs.get("on_status")
            self.assertTrue(callable(on_status))
            on_status({"run_id": run_id, "status": "running", "phase": "pm_planning"})
            on_status({"run_id": run_id, "status": "running", "phase": "chief_engineer_review"})
            on_status({"run_id": run_id, "status": "running", "phase": "director_dispatch"})
            return {"run_id": run_id, "status": "completed", "phase": "qa_gate"}

        with patch("scripts.factory_bench.run_factory_bench.wait_run_until_terminal", _fake_wait):
            result = run_factory_chain(
                project,
                self.workspace,
                backend_url=self.backend_url,
                backend_token="",
                timeout_s=30,
                log_path=log_path,
                on_stage_change=lambda status, payload: stage_events.append((status, str(payload.get("phase") or ""))),
            )

        self.assertEqual(result.get("exit_code"), 0)
        self.assertEqual(
            stage_events,
            [
                ("running", "pm_planning"),
                ("running", "chief_engineer_review"),
                ("running", "director_dispatch"),
            ],
        )

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
        run_id = self._expected_run_id()
        self._setup_audit_bundle(run_id, qa_passed=False)
        with patch(
            "scripts.factory_bench.run_factory_bench.wait_run_until_terminal",
            return_value={"run_id": run_id, "status": "failed", "phase": "qa_gate"},
        ):
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
        """Director-stage failures remain director_partial."""
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

    def test_map_factory_run_to_chain_results_pm_failed(self) -> None:
        run_status = {
            "status": "failed",
            "phase": "planning",
            "current_stage": "pm_planning",
            "metadata": {"current_stage": "pm_planning", "last_failed_stage": "pm_planning"},
        }
        chain_results = map_factory_run_to_chain_results(run_status, {"gates": [], "events_tail": []})
        self.assertEqual(chain_results["exit_class"], "pm_failed")
        self.assertEqual(chain_results["factory_stage_hint"], "pm_planning")

    def test_map_factory_run_to_chain_results_chief_engineer_failed(self) -> None:
        run_status = {
            "status": "failed",
            "phase": "implementation",
            "current_stage": "chief_engineer_review",
            "metadata": {
                "current_stage": "chief_engineer_review",
                "last_failed_stage": "chief_engineer_review",
            },
        }
        chain_results = map_factory_run_to_chain_results(run_status, {"gates": [], "events_tail": []})
        self.assertEqual(chain_results["exit_class"], "chief_engineer_failed")

    def test_required_llm_roles_are_stage_aware(self) -> None:
        pm_chain = {"chain_results": {"exit_class": "pm_failed", "factory_stage_hint": "pm_planning"}}
        self.assertEqual(required_llm_roles_for_factory_record(chain=pm_chain, record={}), ("pm",))

        director_chain = {
            "chain_results": {
                "exit_class": "director_partial",
                "factory_stage_hint": "director_dispatch",
                "qa_ran": False,
            }
        }
        self.assertEqual(
            required_llm_roles_for_factory_record(chain=director_chain, record={}),
            ("pm", "chief_engineer", "director"),
        )
        self.assertEqual(
            required_llm_roles_for_factory_record(
                chain=director_chain,
                record={"factory_bench_start_from": "director"},
            ),
            ("director",),
        )

        chief_chain = {
            "chain_results": {
                "exit_class": "chief_engineer_failed",
                "factory_stage_hint": "chief_engineer_review",
                "director": {"total": None, "successes": None, "failures": None, "blocked": None},
            }
        }
        self.assertEqual(
            required_llm_roles_for_factory_record(chain=chief_chain, record={}),
            ("pm", "chief_engineer"),
        )

        clean_chain = {"chain_results": {"exit_class": "clean", "factory_stage_hint": "quality_gate", "qa_ran": True}}
        self.assertEqual(
            required_llm_roles_for_factory_record(chain=clean_chain, record={}),
            ("pm", "chief_engineer", "director", "qa"),
        )
        self.assertEqual(
            required_llm_roles_for_factory_record(
                chain=clean_chain,
                record={"qa_invoked": False},
            ),
            ("pm", "chief_engineer", "director"),
        )
        self.assertEqual(
            required_llm_roles_for_factory_record(
                chain=clean_chain,
                record={"factory_bench_start_from": "director"},
            ),
            ("director", "qa"),
        )

        resume_evidence_chain = {
            "chain_results": {
                "exit_class": "hard_failed",
                "factory_stage_hint": "resume_evidence_gate",
                "qa_ran": False,
            }
        }
        self.assertEqual(
            required_llm_roles_for_factory_record(
                chain=resume_evidence_chain,
                record={"factory_bench_start_from": "director"},
            ),
            (),
        )

    def test_read_factory_qa_invocation_status_distinguishes_not_run_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            report = workspace / ".polaris" / "roles" / "qa" / "factory-1" / "report.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                json.dumps(
                    {
                        "verdict": "NOT_RUN",
                        "qa_invoked": False,
                        "verdict_source": "deterministic_factory_gate",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertIs(read_factory_qa_invocation_status(workspace, "factory-1"), False)

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
        # No audit-bundle GET should have been attempted when start fails
        audit_calls = [
            p for method, p, _ in _MockFactoryRunsBackend.received if method == "GET" and p.endswith("/audit-bundle")
        ]
        self.assertEqual(len(audit_calls), 0, "no audit-bundle GET expected when start fails")

    def test_map_factory_run_to_chain_results_completed_qa_failed(self) -> None:
        """When status is completed but qa_passed=False, exit_class=qa_failed."""
        run_status = {"status": "completed", "phase": "qa_gate"}
        audit_bundle = {
            "gates": [
                {"gate_name": "quality_gate", "passed": False, "message": "QA failed"},
            ],
            "events_tail": [],
            "summary_json": {},
        }
        chain_results = map_factory_run_to_chain_results(run_status, audit_bundle)
        self.assertEqual(chain_results["qa_ran"], True)
        self.assertEqual(chain_results["qa_passed"], False)
        self.assertEqual(chain_results["exit_class"], "qa_failed")

    def test_run_factory_chain_event_wait_timeout_non_terminal(self) -> None:
        """When runtime.v2 never delivers a terminal state, event waiting times out."""
        project = {
            "id": "L1-04",
            "title": "Never Ends",
            "brief": "A project that stays running forever",
            "level": 1,
            "test_focus": "timeout path",
        }
        log_path = Path(self._tmp.name) / "chain.log"
        run_id = self._expected_run_id()
        with patch(
            "scripts.factory_bench.run_factory_bench.wait_run_until_terminal",
            return_value={
                "run_id": run_id,
                "status": "running",
                "phase": "director_dispatch",
                "_event_wait_error": {
                    "kind": "runtime_v2_connection_failed",
                    "message": "received 1012 (service restart)",
                    "backend_url": self.backend_url,
                    "workspace": str(self.workspace),
                },
                "last_observed_status": {
                    "run_id": run_id,
                    "status": "running",
                    "phase": "director_dispatch",
                },
            },
        ) as wait_mock:
            result = run_factory_chain(
                project,
                self.workspace,
                backend_url=self.backend_url,
                backend_token="",
                timeout_s=0,
                log_path=log_path,
            )

        self.assertEqual(result.get("exit_code"), -1)
        self.assertEqual(result.get("error"), "event_wait_timeout")
        self.assertEqual(wait_mock.call_args.kwargs.get("return_diagnostics"), True)
        self.assertEqual(result.get("event_wait_error", {}).get("kind"), "runtime_v2_connection_failed")
        self.assertEqual(result.get("last_observed_status", {}).get("phase"), "director_dispatch")
        self.assertEqual(result.get("cancel_response", {}).get("status"), "cancelled")
        control_calls = [
            body
            for method, path, body in _MockFactoryRunsBackend.received
            if method == "POST" and path == f"/v2/factory/runs/{run_id}/control"
        ]
        self.assertEqual(len(control_calls), 1, "event wait timeout should cancel the backend run")
        self.assertEqual(control_calls[0].get("action"), "cancel")
        cancel_reason = str(control_calls[0].get("reason") or "")
        # R153: cancel reason distinguishes connection-failed exhaustion from pure timeout.
        self.assertIn("event wait", cancel_reason)
        self.assertTrue(
            ("timeout" in cancel_reason) or ("connection failed" in cancel_reason),
            msg=f"unexpected cancel reason: {cancel_reason!r}",
        )
        # No audit-bundle GET should have been attempted when event waiting times out
        audit_calls = [
            p for method, p, _ in _MockFactoryRunsBackend.received if method == "GET" and p.endswith("/audit-bundle")
        ]
        self.assertEqual(len(audit_calls), 0, "no audit-bundle GET expected when event waiting times out")


if __name__ == "__main__":
    unittest.main()
