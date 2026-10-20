"""Fake clients prove harness mapping only, never real model/instance acceptance."""

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

import pytest


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

    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        self.stops.append(instance_id)
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
    assert body["workflow"] == "collaborative" and body["role"] == "director"
    assert body["required_checks"] == ["verify-original-entry", "verify-original-rules"]
    assert inputs.requirements_file.read_text(encoding="utf-8") in body["instruction"]
    assert not ({"scope", "enabled_tools", "network_allowed", "require_qa", "limits"} & body.keys())
    assert not inputs.workspace.exists(), "harness wrote the target instead of delegating Supervisor"
    binding = json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))
    assert binding["overall_deadline_epoch"] == 7000.0
    assert "engineering_run=server-run" in binding["launch_url"]
    assert "synthetic-instance-token" not in json.dumps(result)


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
    http.snapshot.update(status="running", project_outcome="not_accepted")
    result = module.stop(
        inputs.evidence_dir / "binding.json",
        supervisor=supervisor,
        http_factory=lambda _binding: http,
        activity_reader=lambda _workspace: {"status": "readable", "active_runs": [], "pending_tool_calls": []},
        process_probe=lambda _pid: False,
    )
    assert result["harness_status"] == "UNKNOWN" and not supervisor.stops
    http.snapshot.update(status="cancelled")
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
    assert result["platform"]["project_outcome"] == "not_accepted"


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
    arguments[arguments.index("--timeout") + 1] = "540"
    assert module.main(arguments) == 2


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
                json.dumps({"turn_seq": 3, "call_id": "old-write", "original_effect": "unknown"}),
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
