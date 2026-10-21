"""Synthetic formal-engineering adapter lifecycle and preserved historical readers.

No server, provider, target command or delivery acceptance is executed here.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import pytest
from scripts.factory_bench._bench_lib import chain as bench_chain, cli as bench_cli
from scripts.factory_bench._bench_lib.catalog import load_projects
from scripts.factory_bench._bench_lib.constants import _FIXTURE

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from scripts.factory_bench.run_factory_bench import (
    build_requirements_doc,
    map_factory_run_to_chain_results,
    read_factory_qa_invocation_status,
    required_llm_roles_for_factory_record,
)


# Shared synthetic boundary fixtures for migrated consumers. No real service,
# provider, target command, install or product acceptance is exercised here.
def _consumer_inputs(base: Path, *, workflow: str = "direct") -> Any:
    base.mkdir(parents=True, exist_ok=True)
    host = base / "host.json"
    host.write_text(
        json.dumps(
            {
                "permission_profiles": {"frozen": {"enabled_tools": []}},
                "default_permission_profile_id": "frozen",
                "require_qa": True,
                "run_timeout_seconds": 5400,
                "verification_output_roots": ["dist"],
            }
        ),
        encoding="utf-8",
    )
    llm = base / "llm.json"
    llm.write_text('{"synthetic_only":true}', encoding="utf-8")
    return bench_chain.prepare_engineering_case(
        load_projects()[0],
        workspace=base / "target",
        evidence_dir=base / "evidence",
        catalog_file=Path(_FIXTURE),
        host_config_file=host,
        llm_config_file=llm,
        instance_id="synthetic-consumer",
        workflow=workflow,
    )


class _ConsumerSupervisor:
    def __init__(self, *, artifacts: bool = False, fail_start: bool = False) -> None:
        self.artifacts, self.fail_start = artifacts, fail_start
        self.starts: list[dict[str, Any]] = []
        self.stops: list[str] = []
        self.record: dict[str, Any] = {}

    def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
        self.starts.append(request)
        if self.fail_start:
            raise PermissionError("synthetic instance launch rejected")
        self.record = {
            **request,
            "backend_port": 50123,
            "frontend_port": 53123,
            "backend_url": "http://127.0.0.1:50123",
            "frontend_url": "http://127.0.0.1:53123",
            "token": "synthetic-secret",
            "backend_alive": True,
            "frontend_alive": True,
            "status": "running",
            "backend_pid": 900001,
            "frontend_pid": 900002,
        }
        if self.artifacts:
            # Explicit private fixture material, never authorized-tool delivery.
            path = Path(request["workspace"]) / "src" / "synthetic.py"
            path.parent.mkdir(parents=True)
            path.write_text("print('synthetic fixture')\n", encoding="utf-8")
        return dict(self.record)

    def health(self, instance_id: str) -> dict[str, Any]:
        assert instance_id == self.record["instance_id"]
        return dict(self.record)

    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        self.stops.append(instance_id)
        self.record.update(status="stopped", backend_alive=False, frontend_alive=False)
        return dict(self.record)


class _ConsumerHTTP:
    def __init__(self, workspace: Path, *, state: str = "unknown", stages: bool = False) -> None:
        self.state, self.stages = state, stages
        self.calls: list[tuple[str, str, Any]] = []
        self.reads = 0
        self.snapshot: dict[str, Any] = {
            "run_id": "consumer-run",
            "session_id": "consumer-run",
            "workspace": str(workspace),
            "engine_version": "engineering_v2",
            "workflow": "direct",
            "role": "director",
            "status": "completed",
            "active_roles": ["director"],
            "required_checks": [],
            "verification": "not_run",
            "require_qa": True,
            "qa_verdict": "not_run",
            "project_outcome": "not_accepted",
            "events": [],
            "last_seq": 0,
        }
        if state == "accepted":
            self.snapshot.update(
                verification="passed",
                qa_verdict="approved",
                project_outcome="accepted",
                acceptance_basis="independent_qa",
                verification_commands=["rtk proxy python -m pytest"],
                checks=[{"command": "rtk proxy python -m pytest", "ok": True, "exit_code": 0, "interrupted": False}],
            )
        elif state == "rejected":
            self.snapshot.update(
                status="failed",
                verification="failed",
                qa_verdict="rejected",
                project_outcome="rejected",
                verification_commands=["rtk proxy python -m pytest"],
                checks=[{"command": "rtk proxy python -m pytest", "ok": False, "exit_code": 1, "interrupted": False}],
            )
        elif state == "running":
            self.snapshot["status"] = "running"

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, payload))
        if method == "POST" and path == "/v2/engineering/runs":
            if self.state == "unknown_post":
                raise TimeoutError("synthetic POST effect unknown")
            assert payload is not None
            self.snapshot["workflow"] = payload["workflow"]
            self.snapshot["role"] = payload["role"]
            self.snapshot["required_checks"] = payload["required_checks"]
        if path.endswith("/cancel"):
            self.snapshot.update(status="cancelled")
        if method == "GET":
            self.reads += 1
            if self.state == "observation_error" and self.reads == 1:
                raise TimeoutError("synthetic read failed")
            self.snapshot["last_seq"] = self.reads
            if self.stages:
                self.snapshot.update(
                    status="running" if self.reads == 1 else "completed",
                    active_roles=["chief_engineer"] if self.reads == 1 else [],
                )
        return dict(self.snapshot)

    def close(self) -> None:
        pass


def _consumer_execute(
    inputs: Any, *, state: str = "unknown", fail_start: bool = False, stages: bool = False
) -> tuple[dict[str, Any], _ConsumerHTTP, _ConsumerSupervisor]:
    supervisor = _ConsumerSupervisor(artifacts=state == "accepted", fail_start=fail_start)
    http = _ConsumerHTTP(inputs.workspace, state=state, stages=stages)
    http.snapshot.update(
        run_id="consumer-" + inputs.project_id.lower(), session_id="consumer-" + inputs.project_id.lower()
    )
    options: dict[str, Any] = {}
    if state in {"running", "observation_error"}:
        clock = iter([1000.0, 7001.0, 7001.0, 7002.0])
        options["now"] = lambda: next(clock)
    report = bench_chain.run_engineering_case(
        inputs,
        supervisor=supervisor,
        http_factory=lambda _: http,
        activity_reader=lambda _: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _: False,
        sleep=lambda _: None,
        **options,
    )
    return report, http, supervisor


def _consumer_case(
    base: Path, *, workflow: str = "direct", **options: Any
) -> tuple[Any, dict[str, Any], _ConsumerHTTP, _ConsumerSupervisor]:
    inputs = _consumer_inputs(base, workflow=workflow)
    report, http, supervisor = _consumer_execute(inputs, **options)
    return inputs, report, http, supervisor


def _consumer_batch(
    base: Path,
    monkeypatch: Any,
    *,
    states: tuple[str, ...] = ("unknown",),
    fail_start: bool = False,
    arguments: tuple[str, ...] = (),
) -> tuple[int, dict[str, Any], list[Any]]:
    seed = _consumer_inputs(base / "inputs")
    traces: list[Any] = []

    def execute(inputs: Any) -> dict[str, Any]:
        state = states[min(len(traces), len(states) - 1)]
        report, http, supervisor = _consumer_execute(inputs, state=state, fail_start=fail_start)
        traces.append((inputs, http, supervisor))
        return report

    monkeypatch.setattr(bench_cli, "run_engineering_case", execute)
    work = base / "batch"
    result = bench_cli.main(
        [
            "--limit",
            str(len(states)),
            "--work-dir",
            str(work),
            "--host-config-file",
            str(seed.host_config_file),
            "--llm-config-file",
            str(seed.llm_config_file),
            *arguments,
        ]
    )
    summary = json.loads(next((work / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    return result, summary, traces


def _assert_retired_consumer(base: Path, *, writer: str = "run_factory_chain", **options: Any) -> None:
    target, log = base / "target", base / "chain.log"
    base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(RuntimeError, match="retired"):
        getattr(bench_chain, writer)(
            {"id": "L1-01", "brief": "Original goal"}, target, timeout_s=5400, log_path=log, **options
        )
    assert not target.exists() and not log.exists()
    assert list(base.iterdir()) == []


class TestFactoryRunsIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "historical-reader"
        self.workspace.mkdir(parents=True, exist_ok=True)

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

    def test_run_factory_chain_happy_path(self) -> None:
        """Formal goal/workflow/current evidence and cleanup replace Factory protocol."""
        inputs, report, http, _ = _consumer_case(
            Path(self._tmp.name) / "formal", state="accepted", workflow="collaborative"
        )
        request = json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8"))
        self.assertEqual(request["workflow"], "collaborative")
        self.assertEqual(request["role"], "director")
        self.assertEqual(request["required_checks"], [])
        self.assertEqual(report["observation"]["harness_status"], "OBSERVED_ACCEPTED")
        self.assertEqual(report["cleanup"]["harness_status"], "STOPPED")
        self.assertFalse(report["delivery_proven"])
        self.assertEqual(report["catalog_quality"], "UNKNOWN")
        self.assertEqual([p for m, p, _ in http.calls if m == "POST"], ["/v2/engineering/runs"])

    def test_run_factory_chain_forwards_stage_changes(self) -> None:
        """Scoped snapshots preserve role/progress transitions; terminal reply is not acceptance."""
        inputs, report, _, _ = _consumer_case(Path(self._tmp.name) / "formal", stages=True, workflow="collaborative")
        snapshots = [
            json.loads(p.read_text(encoding="utf-8")) for p in (inputs.evidence_dir / "snapshots").glob("*.json")
        ]
        self.assertTrue(any(s["status"] == "running" and s["active_roles"] == ["chief_engineer"] for s in snapshots))
        self.assertTrue(any(s["status"] == "completed" and s["active_roles"] == [] for s in snapshots))
        self.assertEqual(report["observation"]["platform"]["last_seq"], 2)
        self.assertNotEqual(report["exit_code"], 0)

    def test_run_factory_chain_failed_qa(self) -> None:
        """Current failed check/QA remains failure, not missing or legacy prose."""
        _, report, _, _ = _consumer_case(Path(self._tmp.name) / "formal", state="rejected")
        platform = report["observation"]["platform"]
        self.assertEqual(report["observation"]["harness_status"], "REJECTED")
        self.assertEqual(platform["qa_verdict"], "rejected")
        self.assertEqual(platform["checks"][0]["exit_code"], 1)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["cleanup"]["harness_status"], "STOPPED")

    def test_map_factory_run_to_chain_results_does_not_infer_director_or_qa_authority(self) -> None:
        """Legacy audit prose cannot synthesize canonical Director/QA authority."""
        run_status = {"status": "failed", "phase": "director_dispatch"}
        audit_bundle = {
            "gates": [
                {"gate_name": "quality_gate", "passed": False, "message": "director crashed"},
            ],
            "events_tail": [],
            "summary_json": {},
        }
        chain_results = map_factory_run_to_chain_results(run_status, audit_bundle)
        self.assertIs(chain_results["qa_ran"], None)
        self.assertIs(chain_results["qa_passed"], None)
        self.assertEqual(chain_results["exit_class"], "legacy_unknown")
        self.assertIs(chain_results["authoritative"], False)

    def test_map_factory_run_to_chain_results_pm_failed(self) -> None:
        run_status = {
            "status": "failed",
            "phase": "planning",
            "current_stage": "pm_planning",
            "metadata": {"current_stage": "pm_planning", "last_failed_stage": "pm_planning"},
        }
        chain_results = map_factory_run_to_chain_results(run_status, {"gates": [], "events_tail": []})
        self.assertEqual(chain_results["exit_class"], "legacy_unknown")
        self.assertEqual(chain_results["factory_stage_hint"], "planning")

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
        self.assertEqual(chain_results["exit_class"], "legacy_unknown")
        self.assertEqual(chain_results["factory_stage_hint"], "implementation")

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

    def test_read_factory_qa_invocation_status_accepts_physical_verifier_report(self) -> None:
        """Physical verifier QA is authoritative without an advisory QA LLM route."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            report = workspace / ".polaris" / "roles" / "qa" / "factory-2" / "report.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                json.dumps(
                    {
                        "schema_version": "factory.qa_physical_verifier_report.v1",
                        "source": "factory_physical_verifier",
                        "verdict": "PASS",
                        "llm_invoked": False,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertIs(read_factory_qa_invocation_status(workspace, "factory-2"), False)

    def test_map_factory_run_to_chain_results_cancelled_remains_non_authoritative(self) -> None:
        """A legacy status payload cannot authoritatively classify cancellation."""
        run_status = {"status": "cancelled", "phase": "unknown"}
        audit_bundle: dict[str, Any] = {"gates": [], "events_tail": [], "summary_json": {}}
        chain_results = map_factory_run_to_chain_results(run_status, audit_bundle)
        self.assertIs(chain_results["qa_ran"], None)
        self.assertIs(chain_results["qa_passed"], None)
        self.assertEqual(chain_results["exit_class"], "legacy_unknown")

    def test_run_factory_chain_start_failure(self) -> None:
        """Failed actual launch persists attempt without target effects or retry."""
        inputs, report, http, _ = _consumer_case(Path(self._tmp.name) / "formal", fail_start=True)
        self.assertEqual(report["launch"]["harness_status"], "UNKNOWN")
        self.assertNotEqual(report["exit_code"], 0)
        self.assertEqual(http.calls, [])
        self.assertFalse(inputs.workspace.exists())
        self.assertEqual(
            json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))["phase"], "instance_unknown"
        )

    def test_map_factory_run_to_chain_results_ignores_legacy_qa_failure_prose(self) -> None:
        """QA authority comes from canonical facts, not a legacy gate artifact."""
        run_status = {"status": "completed", "phase": "qa_gate"}
        audit_bundle = {
            "gates": [
                {"gate_name": "quality_gate", "passed": False, "message": "QA failed"},
            ],
            "events_tail": [],
            "summary_json": {},
        }
        chain_results = map_factory_run_to_chain_results(run_status, audit_bundle)
        self.assertIs(chain_results["qa_ran"], None)
        self.assertIs(chain_results["qa_passed"], None)
        self.assertEqual(chain_results["exit_class"], "legacy_unknown")

    def test_run_factory_chain_event_wait_timeout_non_terminal(self) -> None:
        """Bounded current observation cancels exactly owned run once and waits cleanup."""
        inputs, report, http, _ = _consumer_case(Path(self._tmp.name) / "formal", state="running")
        self.assertEqual(report["observation"]["harness_status"], "TIMEOUT")
        self.assertEqual(report["exit_code"], 124)
        self.assertEqual(report["final_observation"]["platform"]["status"], "cancelled")
        self.assertEqual(report["cleanup"]["harness_status"], "STOPPED")
        self.assertEqual(
            [p for m, p, _ in http.calls if m == "POST"],
            ["/v2/engineering/runs", "/v2/engineering/runs/consumer-l1-01/cancel"],
        )
        self.assertFalse(inputs.workspace.exists())


if __name__ == "__main__":
    unittest.main()
