"""Synthetic harness and loopback component proofs; never real model/instance acceptance."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)


def _inputs(module: Any, tmp_path: Path) -> Any:
    root = tmp_path / "source"
    (root / "src" / "backend").mkdir(parents=True)
    documents = {
        "catalog": {
            "schema_version": "factory-bench/2",
            "projects": [{"id": "CASE-1", "title": "Synthetic case", "checks": ["original_rule"]}],
        },
        "checks": ["verify-original-entry", "verify-original-rules"],
        "host": {
            "permission_profiles": {
                "acceptance": {
                    "read_roots": ["."],
                    "write_roots": ["."],
                    "enabled_tools": ["read_file", "execute_command", "report_review"],
                    "network_allowed": True,
                }
            },
            "default_permission_profile_id": "acceptance",
            "require_qa": True,
            "run_timeout_seconds": 5400,
            "verification_output_roots": ["dist"],
        },
        "llm": {"synthetic": True},
    }
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    for name, value in documents.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    requirements = tmp_path / "original.txt"
    requirements.write_text(
        "Original business goal. Minimum depth 120 lines. Real build/test/entry required.", encoding="utf-8"
    )
    return module.LaunchInputs(
        polaris_root=root,
        workspace=tmp_path / "target",
        evidence_dir=tmp_path / "evidence",
        catalog_file=paths["catalog"],
        catalog_sha256=digests["catalog"],
        project_id="CASE-1",
        requirements_file=requirements,
        requirements_sha256=hashlib.sha256(requirements.read_bytes()).hexdigest(),
        checks_file=paths["checks"],
        checks_sha256=digests["checks"],
        host_config_file=paths["host"],
        host_config_sha256=digests["host"],
        llm_config_file=paths["llm"],
        llm_config_sha256=digests["llm"],
        instance_id="acceptance-case-1",
        registry_home=tmp_path / "registry",
    )


class FakeSupervisor:
    """No process or port operations; only records adapter arguments."""

    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.stops: list[str] = []
        self.settlements: list[dict[str, Any] | None] = []
        self.record: dict[str, Any] = {}
        self.start_environment: dict[str, str | None] = {}

    def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
        self.starts.append(request)
        self.start_environment = {
            key: os.environ.get(key)
            for key in ("KERNELONE_ENGINEERING_HOST_CONFIG", "KERNELONE_LLM_CONFIG", "KERNELONE_ENGINEERING_REQUIRE_QA")
        }
        self.record = {
            **request,
            "backend_port": 50123,
            "frontend_port": 53123,
            "backend_url": "http://127.0.0.1:50123",
            "frontend_url": "http://127.0.0.1:53123",
            "token": "synthetic-instance-token",
            "backend_pid": 900001,
            "frontend_pid": 900002,
            "backend_alive": True,
            "frontend_alive": True,
            "status": "running",
        }
        return dict(self.record)

    def health(self, _instance_id: str) -> dict[str, Any]:
        return dict(self.record)

    def stop_instance(
        self, instance_id: str, *, engineering_settlement: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert engineering_settlement is not None
        assert engineering_settlement["schema_version"] == "engineering.resource_settlement.v1"
        assert not ({"plan", "owner_id", "internal_test_only"} & engineering_settlement.keys())
        self.stops.append(instance_id)
        self.settlements.append(engineering_settlement)
        self.record.update(
            status="stopped", backend_alive=False, frontend_alive=False, backend_pid=None, frontend_pid=None
        )
        return dict(self.record)


class FakeHTTP:
    def __init__(self, workspace: Path, *, post_error: bool = False) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.post_error = post_error
        self.snapshot: dict[str, Any] = {
            "session_id": "server-run",
            "run_id": "server-run",
            "engine_version": "engineering_v2",
            "workspace": str(workspace),
            "workflow": "direct",
            "role": "director",
            "status": "completed",
            "active_roles": [],
            "verification": "passed",
            "require_qa": True,
            "qa_verdict": "approved",
            "project_outcome": "accepted",
            "acceptance_basis": "independent_qa",
            "required_checks": ["verify-original-entry", "verify-original-rules"],
            "checks": [
                {"command": "verify-original-entry", "exit_code": 0},
                {"command": "verify-original-rules", "exit_code": 0},
            ],
            "events": [],
            "last_seq": 0,
        }

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, payload))
        if method == "POST" and self.post_error:
            raise TimeoutError("request result unknown")
        if path.endswith("/cancel"):
            return {**self.snapshot, "status": "cancelled", "project_outcome": "not_accepted"}
        return dict(self.snapshot)

    def close(self) -> None:
        pass


def test_launch_uses_frozen_originals_and_only_formal_new_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = FakeHTTP(inputs.workspace)
    monkeypatch.setenv("KERNELONE_ENGINEERING_HOST_CONFIG", "prior-host-value")
    result = module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http, now=lambda: 1000.0)
    assert result["harness_status"] == "LAUNCHED"
    assert result["exit_code"] == 0
    assert len(supervisor.starts) == 1
    request = supervisor.starts[0]
    assert request["kind"] == "bench_project" and request["metadata"]["internal_test_only"] is True
    assert request["backend_port"] is None and request["frontend_port"] is None
    assert request["backend_reload"] is False and request["require_fresh_instance"] is True
    assert supervisor.start_environment["KERNELONE_ENGINEERING_HOST_CONFIG"] == str(inputs.host_config_file)
    assert supervisor.start_environment["KERNELONE_LLM_CONFIG"] == str(inputs.llm_config_file)
    assert os.environ["KERNELONE_ENGINEERING_HOST_CONFIG"] == "prior-host-value"
    assert len(http.calls) == 1
    method, path, body = http.calls[0]
    assert (method, path) == ("POST", "/v2/engineering/runs")
    assert body["workflow"] == "direct" and body["role"] == "director"
    assert body["required_checks"] == ["verify-original-entry", "verify-original-rules"]
    assert inputs.requirements_file.read_text(encoding="utf-8") in body["instruction"]
    assert not ({"scope", "enabled_tools", "network_allowed", "require_qa", "limits"} & body.keys())
    assert not inputs.workspace.exists(), "harness wrote the target instead of delegating Supervisor"
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    assert binding["overall_deadline_epoch"] == 7000.0
    assert binding["plan"]["workflow"] == "direct"
    assert "engineering_run=server-run" in binding["launch_url"]
    assert "engine" not in parse_qs(urlparse(binding["launch_url"]).query)
    assert "synthetic-instance-token" not in json.dumps(result)


def test_explicit_collaboration_survives_plan_request_and_binding(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = replace(_inputs(module, tmp_path), workflow="collaborative")
    http = FakeHTTP(inputs.workspace)
    http.snapshot["workflow"] = "collaborative"
    result = module.launch(inputs, supervisor=FakeSupervisor(), http_factory=lambda _binding: http)
    assert result["harness_status"] == "LAUNCHED"
    assert http.calls[0][2]["workflow"] == "collaborative"
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    assert binding["plan"]["workflow"] == "collaborative"


def test_invalid_workflow_has_no_instance_or_audit_claim_effect(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = replace(_inputs(module, tmp_path), workflow="old-factory")
    supervisor = FakeSupervisor()
    with pytest.raises(ValueError, match="workflow"):
        module.launch(inputs, supervisor=supervisor)
    assert not supervisor.starts
    assert not inputs.workspace.exists()
    assert not inputs.evidence_dir.exists()


@pytest.mark.parametrize("changed", [{"workflow": "collaborative"}, {"role": "pm"}, {"workflow": None}])
def test_launch_response_for_other_workflow_or_role_stays_unknown_without_replay(
    tmp_path: Path, changed: dict[str, Any]
) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = FakeHTTP(inputs.workspace)
    http.snapshot.update(changed)
    result = module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    assert result["harness_status"] == "UNKNOWN"
    assert result["request_replayed"] is False
    assert len(http.calls) == 1
    assert not supervisor.stops


def test_changed_requirements_fail_before_instance_or_post(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    inputs.requirements_file.write_text("changed", encoding="utf-8")
    supervisor = FakeSupervisor()
    with pytest.raises(ValueError, match=r"requirements.*hash"):
        module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: FakeHTTP(inputs.workspace))
    assert not supervisor.starts
    assert not inputs.workspace.exists()


def test_frozen_empty_user_recipe_delegates_actual_check_selection_to_authorized_agent(tmp_path: Path) -> None:
    """A goal-only request must reach the same formal API, without harness-selected recipes."""
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    inputs.checks_file.write_text("[]\n", encoding="utf-8")
    host = json.loads(inputs.host_config_file.read_text(encoding="utf-8"))
    host["permission_profiles"]["acceptance"]["enabled_tools"].append("verify_project")
    inputs.host_config_file.write_text(json.dumps(host), encoding="utf-8")
    inputs = replace(
        inputs,
        checks_sha256=hashlib.sha256(inputs.checks_file.read_bytes()).hexdigest(),
        host_config_sha256=hashlib.sha256(inputs.host_config_file.read_bytes()).hexdigest(),
    )
    supervisor = FakeSupervisor()
    http = FakeHTTP(inputs.workspace)
    result = module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    assert result["harness_status"] == "LAUNCHED"
    assert http.calls[0][2]["required_checks"] == []
    assert inputs.requirements_file.read_text(encoding="utf-8") in http.calls[0][2]["instruction"]
    assert not inputs.workspace.exists()


def test_uncertain_post_is_retained_without_retry_or_stop(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = FakeHTTP(inputs.workspace, post_error=True)
    result = module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    assert result["harness_status"] == "UNKNOWN" and result["exit_code"] != 0
    assert len(http.calls) == 1 and not supervisor.stops
    with pytest.raises((FileExistsError, ValueError)):
        module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    assert len(http.calls) == 1


def test_completed_without_current_qa_never_becomes_acceptance(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    http = FakeHTTP(inputs.workspace)
    module.launch(inputs, supervisor=FakeSupervisor(), http_factory=lambda _binding: http, now=lambda: 1000.0)
    http.snapshot.update(qa_verdict="not_run", project_outcome="not_accepted", acceptance_basis=None)
    result = module.observe(
        inputs.evidence_dir / "binding.json", http_factory=lambda _binding: http, now=lambda: 1010.0
    )
    assert result["harness_status"] != "OBSERVED_ACCEPTED"
    assert result["platform"]["project_outcome"] == "not_accepted"
    assert result["measurements"]["physical_http_sends"] is None
    assert result["measurements"]["bill"] is None


def test_wait_preserves_original_overall_budget_without_implicit_cancel(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    http = FakeHTTP(inputs.workspace)
    module.launch(inputs, supervisor=FakeSupervisor(), http_factory=lambda _binding: http, now=lambda: 1000.0)
    http.snapshot.update(status="running", project_outcome="not_accepted")
    http.calls.clear()
    result = module.observe(
        inputs.evidence_dir / "binding.json",
        wait=True,
        http_factory=lambda _binding: http,
        now=lambda: 7001.0,
        sleep=lambda _seconds: None,
    )
    assert result["harness_status"] == "TIMEOUT" and result["exit_code"] == 124
    assert all(method == "GET" for method, _path, _body in http.calls)


def test_stop_refuses_live_work_and_stops_only_exact_owned_terminal_instance(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = FakeHTTP(inputs.workspace)
    module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    from polaris.cells.roles.runtime.tests.test_engineering_resource_state import create

    store = create(inputs.workspace, session_id="server-run", status="running")
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE runs SET request_key=?", (binding["request_key"],))
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=lambda _binding: http,
        activity_reader=lambda _workspace: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _pid: False,
    )
    assert result["harness_status"] == "UNKNOWN" and not supervisor.stops
    store.update_run("server-run", status="cancelled")
    store.append_event(
        "server-run",
        kind="run_ended",
        role="director",
        payload={"status": "cancelled", "release_allowed": True, "drain_reporting_error": None},
    )
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=lambda _binding: http,
        activity_reader=lambda _workspace: {
            "status": "readable",
            "active_runs": [{"session_id": "other-live-run", "status": "running"}],
            "pending_tool_calls": [],
        },
        process_probe=lambda _pid: False,
    )
    assert result["harness_status"] == "UNKNOWN" and not supervisor.stops
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=lambda _binding: http,
        activity_reader=lambda _workspace: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _pid: False,
    )
    assert result["harness_status"] == "STOPPED" and supervisor.stops == ["acceptance-case-1"]
    assert result["platform"]["project_outcome"] == "unknown"


def test_internal_test_flag_is_required_before_any_launch() -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    assert module.main(["launch"]) == 2


def _arguments(inputs: Any, action: str = "plan") -> list[str]:
    arguments = [action]
    for name, value in vars(inputs).items():
        if value is not None:
            arguments.extend(["--" + name.replace("_", "-"), str(value)])
    if "--timeout" not in arguments:
        arguments.extend(["--timeout", "5400"])
    return arguments


def test_canonical_aliases_and_fixed_timeout_are_consumed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    arguments = [*_arguments(inputs), "--internal-test"]
    arguments[arguments.index("--project-id")] = "--case"
    arguments[arguments.index("--evidence-dir")] = "--report-dir"
    assert module.main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plan"]["project_budget_seconds"] == 5400
    assert result["plan"]["project"]["id"] == "CASE-1"
    assert result["plan"]["workflow"] == "direct"
    arguments[arguments.index("--timeout") + 1] = "540"
    assert module.main(arguments) == 2


def test_cli_accepts_explicit_collaboration_without_changing_project_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    arguments = [*_arguments(inputs), "--internal-test", "--workflow", "collaborative"]
    assert module.main(arguments) == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    assert plan["workflow"] == "collaborative"
    assert plan["project_budget_seconds"] == 5400
    assert plan["overall_budget_seconds"] == 6000
    assert not inputs.workspace.exists()
    assert not inputs.evidence_dir.exists()


def test_internal_flag_rejects_otherwise_valid_arguments(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    assert module.main(_arguments(_inputs(module, tmp_path))) == 2


def test_host_file_cannot_be_shadowed_by_inherited_engineering_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    monkeypatch.setenv("KERNELONE_ENGINEERING_REQUIRE_QA", "0")
    module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: FakeHTTP(inputs.workspace))
    assert supervisor.start_environment["KERNELONE_ENGINEERING_REQUIRE_QA"] is None
    assert os.environ["KERNELONE_ENGINEERING_REQUIRE_QA"] == "0"


def test_actual_native_event_identity_counts_dispatches_not_capture_or_phases(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, TaskType, Usage
    from polaris.kernelone.llm.engine.executor import AIExecutor
    from polaris.kernelone.llm.engine.native_request_capture import NativeRequestCapturePort

    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    observations: list[dict[str, Any]] = []
    request = AIRequest(
        task_type=TaskType.DIALOGUE, role="director", input="Synthetic request.", context={"call_id": "fixture-call"}
    )
    port = NativeRequestCapturePort(
        request=request,
        model_spec=ModelSpec(
            provider_id="fixture",
            provider_type="openai_compat",
            model="fixture",
            max_context_tokens=8192,
            max_output_tokens=128,
        ),
        trace_id="fixture",
        messages=[{"role": "user", "content": "Synthetic request."}],
        provider_request={"role": "director"},
        persist=AIExecutor._store_context_messages_sync,
        workspace=str(tmp_path),
        native_request_observer=lambda _request, observation: observations.append(dict(observation)),
    )
    wire = {
        "endpoint": "http://127.0.0.1/synthetic-no-network",
        "body": {"model": "fixture", "messages": [{"role": "user", "content": "Synthetic request."}]},
        "transport": {"kind": "synthetic-no-network"},
    }

    def failed(_wire: Any) -> Any:
        raise ConnectionError("synthetic no-network dispatch failure")

    with pytest.raises(ConnectionError):
        port.dispatch_sync(wire_request=wire, send=failed)
    port.dispatch_sync(
        wire_request=wire,
        send=lambda _wire: SimpleNamespace(usage=Usage(prompt_tokens=7, completion_tokens=2, total_tokens=9)),
    )
    assert len({item["capture_id"] for item in observations}) == 1
    events: list[dict[str, Any]] = [
        {"seq": index + 1, "kind": "llm_request_observed", "payload": item} for index, item in enumerate(observations)
    ]
    metrics = module._measurements({"events": events, "last_seq": len(events)})
    assert metrics["observed_dispatch_attempts"] == 2
    assert metrics["usage_known_subtotal"]["reported"]["total_tokens"] == 9
    assert metrics["unknown_usage_attempts"] == 1
    assert metrics["physical_http_sends"] is None
    broken = [
        {**event, "payload": {key: value for key, value in event["payload"].items() if key != "provider_request_id"}}
        for event in events
    ]
    missing = module._measurements({"events": broken, "last_seq": len(broken)})
    assert missing["observed_dispatch_attempts"] is None
    assert missing["attempt_identity_status"] == "unknown"
    assert missing["unidentified_observation_events"] == 4


def test_readonly_activity_respects_committed_reconciliation_without_erasing_unknown(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    database = tmp_path / ".polaris" / "runtime" / "engineering" / "sessions.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE runs(session_id TEXT,status TEXT); CREATE TABLE tool_calls(session_id TEXT,turn_seq INTEGER,call_id TEXT,tool TEXT,status TEXT); CREATE TABLE events(session_id TEXT,kind TEXT,payload TEXT);"
        )
        connection.execute("INSERT INTO runs VALUES('run','completed')")
        connection.execute("INSERT INTO tool_calls VALUES('run',3,'old-write','write_file','started')")
        connection.execute(
            "INSERT INTO events VALUES(?,?,?)",
            (
                "run",
                "tool_reconciled",
                json.dumps(
                    {
                        "turn_seq": 3,
                        "call_id": "old-write",
                        "original_effect": "unknown",
                        "continuation_basis": "current_file_matches_requested_write",
                    }
                ),
            ),
        )
    result = module._read_activity(tmp_path)
    assert result["status"] == "readable"
    assert result["pending_tool_calls"] == []
    assert result["reconciled_tool_calls"][0]["call_id"] == "old-write"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM tool_calls").fetchone()[0] == "started"


def test_main_keeps_actual_host_debug_diagnostic_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from polaris.cells.events.fact_stream.public.service import configure_debug_tracing

    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    original_launch = module.launch

    class DiagnosticSupervisor(FakeSupervisor):
        def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
            configure_debug_tracing(True)
            return super().start_instance(request)

    supervisor = DiagnosticSupervisor()
    monkeypatch.setattr(
        module,
        "launch",
        lambda options: original_launch(
            options, supervisor=supervisor, http_factory=lambda _binding: FakeHTTP(inputs.workspace)
        ),
    )
    try:
        assert module.main([*_arguments(inputs, "launch"), "--internal-test"]) == 0
    finally:
        with redirect_stdout(sys.stderr):
            configure_debug_tracing(False)
    output = capsys.readouterr()
    assert json.loads(output.out)["harness_status"] == "LAUNCHED"
    assert "polaris.debug_trace" in output.err


def test_accepted_projection_needs_declared_check_results_and_readonly_artifacts(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    http = FakeHTTP(inputs.workspace)
    module.launch(inputs, supervisor=FakeSupervisor(), http_factory=lambda _binding: http)
    http.snapshot["checks"] = []
    result = module.observe(inputs.evidence_dir / "binding.json", http_factory=lambda _binding: http)
    assert result["platform"]["project_outcome"] == "accepted"
    assert result["harness_status"] != "OBSERVED_ACCEPTED"


def test_current_owner_accepted_is_observed_without_local_qa_or_gate_execution(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    http = FakeHTTP(inputs.workspace)
    module.launch(inputs, supervisor=FakeSupervisor(), http_factory=lambda _binding: http)
    # Explicit fake platform artifact and real wire-shaped ToolObservation
    # records; this maps the harness only and proves no model quality.
    inputs.workspace.mkdir()
    (inputs.workspace / "produced.txt").write_text("fake platform-produced artifact", encoding="utf-8")
    http.snapshot["checks"] = [
        {
            "call_id": "verification-1",
            "tool": "execute_command",
            "ok": True,
            "content": "fake verifier output",
            "exit_code": 0,
            "interrupted": False,
        },
        {
            "call_id": "verification-2",
            "tool": "execute_command",
            "ok": True,
            "content": "fake verifier output",
            "exit_code": 0,
            "interrupted": False,
        },
    ]
    result = module.observe(inputs.evidence_dir / "binding.json", http_factory=lambda _binding: http)
    assert result["harness_status"] == "OBSERVED_ACCEPTED"
    assert result["platform"]["qa_verdict"] == "approved"
    assert result["platform"]["project_outcome"] == "accepted"
    assert result["artifact_inventory"]["is_platform_verdict"] is False
    assert result["measurements"]["harness_gate_commands"] == 0
    assert result["measurements"]["harness_target_edits"] == 0


def test_noncanonical_instance_ids_are_rejected_before_spawn(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = FakeHTTP(inputs.workspace)
    for instance_id in ("internal_test", "a" * 81, "a--b", "a-"):
        with pytest.raises(ValueError, match="canonical"):
            module.launch(
                replace(inputs, instance_id=instance_id),
                supervisor=supervisor,
                http_factory=lambda _binding: http,
            )
        assert not supervisor.starts and not http.calls
    assert module.build_launch_plan(replace(inputs, instance_id="a" * 80))["instance_id"] == "a" * 80


class LostStartReplySupervisor(FakeSupervisor):
    """Synthetic postcommit return loss; no actual instance or process effects."""

    def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
        super().start_instance(request)
        raise OSError("synthetic registry commit succeeded; return projection failed")


class LostCreateReplyHTTP(FakeHTTP):
    """Synthetic durable creation followed by lost HTTP response; no provider."""

    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.identity: dict[str, Any] = {}
        self.lookup_error: Exception | None = None

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "POST" and path == "/v2/engineering/runs":
            assert payload is not None
            self.calls.append((method, path, payload))
            self.identity = {
                "request_key": payload["request_key"],
                "instruction_sha256": hashlib.sha256(payload["instruction"].encode("utf-8")).hexdigest(),
                "workflow": payload["workflow"],
                "required_checks": list(payload["required_checks"]),
                "permission_profile_id": payload["permission_profile_id"],
            }
            from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
            from polaris.cells.roles.runtime.public.contracts.engineering_run import (
                PermissionScope,
                RoleRunCommand,
                RunLimits,
            )

            self.workspace.mkdir(parents=True, exist_ok=True)
            store = EngineeringSessionStore(self.workspace)
            store.create_run(
                RoleRunCommand(
                    "server-run",
                    "director",
                    payload["instruction"],
                    self.workspace,
                    payload["workflow"],
                    tuple(payload["required_checks"]),
                ),
                scope=PermissionScope((self.workspace,), (), frozenset({"read_file"}), False),
                limits=RunLimits(1900000000.0, 10000, 2000),
                engine_version="engineering_v2",
                request_key=payload["request_key"],
                permission_profile_id=payload["permission_profile_id"],
            )
            store.update_run("server-run", status="completed", verification="passed")
            store.append_event(
                "server-run",
                kind="run_ended",
                role="director",
                payload={"status": "completed", "release_allowed": True, "drain_reporting_error": None},
            )
            raise TimeoutError("synthetic durable create succeeded; HTTP response lost")
        if method == "GET" and path.startswith("/v2/engineering/requests/"):
            self.calls.append((method, path, payload))
            if self.lookup_error:
                raise self.lookup_error
            return {**self.snapshot, "request_identity": dict(self.identity)}
        return super().request(method, path, payload)


def test_waiting_observer_recovers_owned_pre_submit_instance_without_waiting_for_an_unattempted_request(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = LostStartReplySupervisor()
    module.launch(inputs, supervisor=supervisor, now=lambda: 1000.0)
    opened: list[dict[str, Any]] = []
    slept: list[float] = []

    def no_wait(seconds: float) -> None:
        slept.append(seconds)
        raise AssertionError("an unattempted request cannot progress by waiting")

    def http_factory(binding: dict[str, Any]) -> FakeHTTP:
        opened.append(binding)
        return FakeHTTP(inputs.workspace)

    result = module.observe(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=http_factory,
        wait=True,
        now=lambda: 1001.0,
        sleep=no_wait,
    )
    assert result["harness_status"] == "UNKNOWN"
    assert result["request_attempted"] is False
    assert opened == [] and slept == []
    assert len(supervisor.starts) == 1 and supervisor.stops == []


def test_owned_instance_is_recovered_after_lost_start_reply_and_stopped_without_formal_post(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = LostStartReplySupervisor()
    http = FakeHTTP(inputs.workspace)
    launched = module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    binding_file = inputs.evidence_dir / "binding.json"
    initial = json.loads(binding_file.read_text(encoding="utf-8"))
    assert initial["instance"] == {}
    result = module.stop(
        binding_file,
        supervisor=supervisor,
        http_factory=lambda _binding: http,
        activity_reader=lambda _workspace: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _pid: False,
    )
    assert launched["harness_status"] == "UNKNOWN"
    assert result["harness_status"] == "STOPPED"
    assert result["resources"]["request_attempted"] is False
    assert len(supervisor.starts) == 1 and supervisor.stops == ["acceptance-case-1"]
    assert http.calls == [], "pre-submit cleanup must not create, query or guess an engineering run"
    recovered = json.loads(binding_file.read_text(encoding="utf-8"))
    assert recovered["instance"]["instance_id"] == "acceptance-case-1"
    assert recovered["request_attempted"] is False
    assert recovered["failed_phase"] == "instance_start_in_flight"
    assert recovered["run_id"] is None
    assert not inputs.workspace.exists()


@pytest.mark.parametrize(
    "activity",
    [
        {"status": "unknown"},
        {"status": "readable", "active_runs": [{"session_id": "other-live"}], "pending_tool_calls": []},
        {"status": "readable", "active_runs": [], "pending_tool_calls": [{"call_id": "effect-unknown"}]},
    ],
)
def test_pre_submit_cleanup_still_requires_readable_idle_canonical_activity(
    tmp_path: Path, activity: dict[str, Any]
) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = LostStartReplySupervisor()
    module.launch(inputs, supervisor=supervisor)
    http = FakeHTTP(inputs.workspace)
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=lambda _binding: http,
        activity_reader=lambda _workspace: activity,
    )
    assert result["harness_status"] == "UNKNOWN"
    assert supervisor.stops == []
    assert http.calls == []


@pytest.mark.parametrize("operation", ["observe", "cancel", "stop"])
def test_lost_create_reply_recovers_exact_request_before_observe_cancel_or_stop(tmp_path: Path, operation: str) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = LostCreateReplyHTTP(inputs.workspace)
    assert (
        module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)["harness_status"] == "UNKNOWN"
    )
    binding_file = inputs.evidence_dir / "binding.json"
    before = json.loads(binding_file.read_text(encoding="utf-8"))
    kwargs: dict[str, Any] = {"http_factory": lambda _binding: http}
    if operation == "stop":
        kwargs.update(
            supervisor=supervisor,
            activity_reader=lambda _workspace: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
            process_probe=lambda _pid: False,
        )
    result = getattr(module, operation)(binding_file, **kwargs)
    recovered = json.loads(binding_file.read_text(encoding="utf-8"))
    assert recovered["run_id"] == "server-run"
    assert recovered["request_key"] == before["request_key"]
    assert recovered["overall_deadline_epoch"] == before["overall_deadline_epoch"]
    if operation != "stop":
        lookup = http.calls[1]
        assert lookup[0] == "GET"
        assert urlparse(lookup[1]).path == "/v2/engineering/requests/" + before["request_key"]
        assert parse_qs(urlparse(lookup[1]).query) == {"workspace": [str(inputs.workspace)]}
    else:
        assert len(http.calls) == 1, "resource settlement must not depend on artifact HTTP"
    assert sum(method == "POST" and path == "/v2/engineering/runs" for method, path, _ in http.calls) == 1
    assert not any("/runs/None" in path for _method, path, _body in http.calls)
    if operation == "stop":
        assert result["harness_status"] == "STOPPED" and supervisor.stops == ["acceptance-case-1"]
    elif operation == "cancel":
        assert result["harness_status"] == "CANCELLED" and supervisor.stops == []
    else:
        assert result["platform"]["status"] == "completed" and supervisor.stops == []


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("request_key", "other-request"),
        ("instruction_sha256", "0" * 64),
        ("workflow", "collaborative"),
        ("required_checks", []),
        ("permission_profile_id", "other-profile"),
    ],
)
def test_request_lookup_rejects_unrelated_intent_without_mutation_or_binding(
    tmp_path: Path, field: str, wrong: Any
) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = LostCreateReplyHTTP(inputs.workspace)
    module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    http.identity[field] = wrong
    result = module.cancel(inputs.evidence_dir / "binding.json", http_factory=lambda _binding: http)
    assert result["harness_status"] == "UNKNOWN"
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    assert binding["run_id"] is None
    assert len(http.calls) == 2 and http.calls[1][0] == "GET"
    assert supervisor.stops == []


def test_absent_lookup_keeps_inflight_request_unknown_and_never_stops_or_reposts(tmp_path: Path) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = LostCreateReplyHTTP(inputs.workspace)
    module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    http.lookup_error = LookupError("synthetic lookup404; request may still commit")
    (inputs.workspace / ".polaris/runtime/engineering/sessions.sqlite3").unlink()
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=lambda _binding: http,
        activity_reader=lambda _workspace: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
    )
    assert result["harness_status"] == "UNKNOWN"
    assert not supervisor.stops
    assert len(http.calls) == 1, "missing canonical identity refuses without replay or HTTP inference"


@pytest.mark.parametrize("changed", ["polaris_root", "endpoint", "shared_backend", "owner"])
def test_stop_refuses_changed_source_endpoint_or_shared_backend_despite_same_nonce(
    tmp_path: Path, changed: str
) -> None:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = FakeHTTP(inputs.workspace)
    module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    from polaris.cells.roles.runtime.tests.test_engineering_resource_state import create

    store = create(inputs.workspace, session_id="server-run")
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE runs SET request_key=?", (binding["request_key"],))

    if changed == "polaris_root":
        supervisor.record["polaris_root"] = str(tmp_path / "other-source")
    elif changed == "endpoint":
        supervisor.record.update(backend_port=50124, backend_url="http://127.0.0.1:50124")
    elif changed == "owner":
        supervisor.record["metadata"]["acceptance_owner"] = "foreign-owner"
    else:
        supervisor.record["metadata"] = {
            **supervisor.record["metadata"],
            "backend_binding": "shared_backend_workspace_switch",
        }
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=lambda _binding: http,
        activity_reader=lambda _workspace: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _pid: False,
    )
    assert result["harness_status"] == "UNKNOWN"
    assert supervisor.stops == []


def test_terminal_resource_settlement_bypasses_unavailable_http(tmp_path: Path) -> None:
    from polaris.cells.roles.runtime.tests.test_engineering_resource_state import create

    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    http = FakeHTTP(inputs.workspace)
    module.launch(inputs, supervisor=supervisor, http_factory=lambda _binding: http)
    store = create(inputs.workspace, session_id="server-run")
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE runs SET request_key=?", (binding["request_key"],))

    def unavailable(_binding):
        raise TimeoutError("fresh artifact HTTP unavailable")

    observed = module.observe(
        inputs.evidence_dir / "binding.json",
        wait=True,
        http_factory=unavailable,
        supervisor=supervisor,
        sleep=lambda _: pytest.fail("terminal resource must reach cleanup"),
    )
    assert observed["harness_status"] == "UNKNOWN"
    assert observed["platform"]["status"] == "completed"
    assert observed["resource_state"]["safe_to_stop"] is True
    assert observed["delivery_proven"] is False
    stopped = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=unavailable,
        process_probe=lambda _: False,
    )
    assert stopped["harness_status"] == "STOPPED"
    assert stopped["acceptance_unchanged_by_cleanup"] is True
    assert store.load_run("server-run", workspace=inputs.workspace)["verification"] == "passed"

    settlement = supervisor.settlements[0]
    assert settlement is not None
    assert settlement["schema_version"] == "engineering.resource_settlement.v1"
    assert set(settlement) == {
        "schema_version",
        "instance",
        "run_id",
        "request_key",
        "request_attempted",
        "resource_namespace_identity",
    }
    assert settlement["instance"]["instance_id"] == inputs.instance_id


def test_known_run_with_different_committed_request_cannot_authorize_stop(tmp_path: Path) -> None:
    from polaris.cells.roles.runtime.tests.test_engineering_resource_state import create

    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    module.launch(inputs, supervisor=supervisor, http_factory=lambda _: FakeHTTP(inputs.workspace))
    create(inputs.workspace, session_id="server-run")  # Same run text, different immutable request key.
    result = module.stop(inputs.evidence_dir / "binding.json", supervisor=supervisor, process_probe=lambda _: False)
    assert result["harness_status"] == "UNKNOWN"
    assert supervisor.stops == []


@pytest.mark.parametrize(
    "raw_usage,reported_subtotal",
    [
        # Same raw counters as event 1705 / run 6d51da0262c241038fd624920378875d;
        # only finite loopback replay, never another actual model-run receipt.
        pytest.param(
            {
                "input_tokens": 816,
                "output_tokens": 725,
                "cache_read_input_tokens": 143597,
                "cache_creation_input_tokens": 0,
            },
            {"prompt_tokens": 144413, "completion_tokens": 725, "total_tokens": 145138, "cached_tokens": 143597},
            id="observed-cache-read",
        ),
        pytest.param(
            {"input_tokens": 816, "output_tokens": 725},
            {"prompt_tokens": 816, "completion_tokens": 725, "total_tokens": 1541},
            id="cache-unreported",
        ),
        pytest.param(
            {"input_tokens": 816, "output_tokens": 725, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            {"prompt_tokens": 816, "completion_tokens": 725, "total_tokens": 1541, "cached_tokens": 0},
            id="cache-explicit-zero",
        ),
    ],
)
async def test_cold_cli_usage_uses_actual_provider_and_durable_attempt_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripted_provider: ScriptedProvider,
    raw_usage: dict[str, int],
    reported_subtotal: dict[str, int],
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
    from polaris.cells.roles.runtime.tests.test_engineering_context_audit import _record_native_usage_run

    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    session_id, _response, sealed = await _record_native_usage_run(tmp_path, monkeypatch, scripted_provider, raw_usage)
    snapshot = EngineeringRunService(tmp_path).snapshot(session_id, workspace=tmp_path)
    metrics = module._measurements(snapshot)
    assert metrics["observed_dispatch_attempts"] == 1
    assert metrics["event_coverage_status"] == "complete"
    assert len(metrics["materialized_snapshot_refs"]) == 2
    assert metrics["usage_known_subtotal"] == {"reported": reported_subtotal, "estimated": {}}
    assert metrics["observed_attempts"][0]["raw_provider_usage"] == raw_usage
    assert metrics["unknown_usage_attempts"] == 0
    assert metrics["physical_http_sends"] is None
    assert metrics["physical_http_sends_status"] == "unknown"
    assert metrics["bill"] is None and metrics["bill_status"] == "unknown"
    # Reopening both consumers cannot accumulate phases, snapshots or old reads.
    assert module._measurements(EngineeringRunService(tmp_path).snapshot(session_id, workspace=tmp_path)) == metrics
    assert all(path.read_bytes() == original for path, original in sealed.items())


def _stop_diagnostic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, FakeSupervisor]:
    module = importlib.import_module("polaris.delivery.cli.engineering_acceptance")
    inputs = _inputs(module, tmp_path)
    supervisor = FakeSupervisor()
    module.launch(inputs, supervisor=supervisor, http_factory=lambda _: FakeHTTP(inputs.workspace))
    monkeypatch.setattr(
        module,
        "_resource_state",
        lambda _: {"safe_to_stop": True, "run_status": "completed", "namespace_identity": "synthetic"},
    )
    return module, inputs, supervisor


@pytest.mark.parametrize(
    "stage,dispatched,returned",
    [
        ("recover_instance", False, False),
        ("canonical_read", False, False),
        ("admission_health", False, False),
        ("settlement_prepare", False, False),
        ("owner_stop", True, False),
        ("closure_health", True, True),
        ("closure_confirmation", True, True),
        ("report_write", True, True),
    ],
)
def test_stop_diagnostic_phase_and_progress_without_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    dispatched: bool,
    returned: bool,
) -> None:
    module, inputs, supervisor = _stop_diagnostic_setup(tmp_path, monkeypatch)
    secret = "synthetic-private-stop-diagnostic"
    private_url = "https://example.invalid/private?token=" + secret
    failure = RuntimeError("injected detail " + private_url)
    health_calls = 0
    record_calls = 0
    original_health = supervisor.health
    original_stop = supervisor.stop_instance
    original_record = module._record

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise failure

    def health(instance_id: str) -> dict[str, Any]:
        nonlocal health_calls
        health_calls += 1
        if (stage == "admission_health" and health_calls == 1) or (stage == "closure_health" and health_calls == 2):
            raise failure
        return original_health(instance_id)

    def stop_instance(instance_id: str, *, engineering_settlement: dict[str, Any] | None = None) -> dict[str, Any]:
        result = original_stop(instance_id, engineering_settlement=engineering_settlement)
        if stage == "owner_stop":
            raise failure  # The fake memory effect occurred, but the call did not return.
        return result

    def record(binding: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        nonlocal record_calls
        record_calls += 1
        if stage == "report_write" and record_calls == 1:
            raise failure
        return original_record(binding, report)

    def activity(_workspace: Path) -> dict[str, Any]:
        if stage == "canonical_read":
            raise failure
        return {"status": "readable", "active_runs": [], "pending_tool_calls": []}

    def probe(_pid: int) -> bool:
        if stage == "closure_confirmation":
            raise failure
        return False

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("unexpected real supervisor, process, resource or HTTP dependency")

    monkeypatch.setattr(supervisor, "health", health)
    monkeypatch.setattr(supervisor, "stop_instance", stop_instance)
    monkeypatch.setattr(module, "_record", record)
    monkeypatch.setattr(module, "_supervisor", forbidden)
    monkeypatch.setattr(module, "_process_alive", forbidden)
    monkeypatch.setattr(module, "query_engineering_resource_state", forbidden)
    if stage == "recover_instance":
        monkeypatch.setattr(module, "_recover_instance", fail)
    if stage == "settlement_prepare":
        # Model a missing binding field after unchanged admission checks.
        binding = module._read_binding(inputs.evidence_dir / "binding.json")
        binding.pop("request_key")
        monkeypatch.setattr(module, "_read_binding", lambda _: binding)
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=forbidden,
        activity_reader=activity,
        process_probe=probe,
    )
    # These assertions are RED against frozen EE2; no Source fix is needed to run them.
    assert result["failure_stage"] == stage
    assert result["stop_dispatch_started"] is dispatched
    assert result["stop_call_returned"] is returned
    assert result["error_code"] == "unclassified_stop_failure"
    assert result["harness_status"] == "UNKNOWN" and result["exit_code"] == 2
    assert result["stop_replayed"] is False
    assert "resources" not in result
    assert supervisor.stops == ([inputs.instance_id] if dispatched else [])
    emitted = json.dumps(result) + (inputs.evidence_dir / "report.json").read_text(encoding="utf-8")
    emitted += "".join(p.read_text(encoding="utf-8") for p in (inputs.evidence_dir / "operations").glob("*.json"))
    assert secret not in emitted and private_url not in emitted


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("base", "instance_registry_error"),
        ("read", "instance_registry_read_error"),
        ("corrupt", "instance_registry_corrupt"),
        ("spoofed", "unclassified_stop_failure"),
    ],
)
def test_stop_diagnostic_allowlists_error_types_not_dynamic_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, expected: str
) -> None:
    from polaris.cells.instances.public.service import InstanceRegistryError, RegistryCorruptionError, RegistryReadError

    module, inputs, supervisor = _stop_diagnostic_setup(tmp_path, monkeypatch)
    secret = "synthetic-error-detail-must-not-escape"
    if kind == "spoofed":

        class SpoofedError(RuntimeError):
            code = secret

        error: Exception = SpoofedError(secret)
    else:
        error_type = {"base": InstanceRegistryError, "read": RegistryReadError, "corrupt": RegistryCorruptionError}[
            kind
        ]
        registry_error = error_type(tmp_path / secret, reason=secret, detail="https://example.invalid/" + secret)
        registry_error.code = secret  # Dynamic code is not an allowlisted diagnostic.
        error = registry_error

    def health(_instance_id: str) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(supervisor, "health", health)
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        activity_reader=lambda _: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _: False,
    )
    assert result["error_code"] == expected
    assert result["failure_stage"] == "admission_health"
    assert result["stop_dispatch_started"] is False and result["stop_call_returned"] is False
    assert result["harness_status"] == "UNKNOWN" and result["stop_replayed"] is False
    assert not supervisor.stops
    emitted = json.dumps(result) + (inputs.evidence_dir / "report.json").read_text(encoding="utf-8")
    emitted += "".join(p.read_text(encoding="utf-8") for p in (inputs.evidence_dir / "operations").glob("*.json"))
    assert secret not in emitted


def test_stop_diagnostic_preserves_baseexception_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, inputs, supervisor = _stop_diagnostic_setup(tmp_path, monkeypatch)

    class GuardAbort(BaseException):
        pass

    calls = 0
    original_stop = supervisor.stop_instance

    def stop_instance(instance_id: str, *, engineering_settlement: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        original_stop(instance_id, engineering_settlement=engineering_settlement)
        raise GuardAbort("synthetic guard")

    monkeypatch.setattr(supervisor, "stop_instance", stop_instance)
    report_before = (inputs.evidence_dir / "report.json").read_bytes()
    operations_before = set((inputs.evidence_dir / "operations").glob("*.json"))
    with pytest.raises(GuardAbort):
        module.stop(
            inputs.evidence_dir / "binding.json",
            supervisor=supervisor,
            activity_reader=lambda _: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
            process_probe=lambda _: False,
        )
    assert calls == 1 and supervisor.stops == [inputs.instance_id]
    assert (inputs.evidence_dir / "report.json").read_bytes() == report_before
    assert set((inputs.evidence_dir / "operations").glob("*.json")) == operations_before
