"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.factory_bench import run_factory_bench as bench
from scripts.factory_bench.run_factory_bench import (
    apply_factory_bench_gates,
    discover_artifacts,
    map_factory_run_to_chain_results,
    read_chain_results_from_runtime_dirs,
    resolve_runtime_dirs_for_workspace,
    run_factory_chain,
)


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "all_checks_passed": True,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "chain_state": "clean",
        "chain_results": {"qa_ran": True, "qa_passed": True},
        "wrong_product_suspect": False,
    }
    record.update(overrides)
    return record


def test_chain_failure_overrides_static_artifact_checks() -> None:
    record = _record(
        chain_state="fail",
        chain_results={"qa_ran": False, "qa_passed": False},
    )

    apply_factory_bench_gates(record, chain={"exit_code": 1})

    assert record["static_checks_passed"] is True
    assert record["all_checks_passed"] is False
    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["chain_clean"]["ok"] is False
    assert gates["integration_qa_passed"]["ok"] is False


def test_missing_qa_verdict_and_wrong_product_are_fail_closed() -> None:
    record = _record(
        has_qa_verdict=False,
        wrong_product_suspect=True,
    )

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert record["all_checks_passed"] is False
    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["qa_verdict_artifact_present"]["ok"] is False
    assert gates["wrong_product_guard"]["ok"] is False


def test_discover_artifacts_accepts_current_qa_report_verdicts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    workspace_qa = workspace / ".polaris" / "qa"
    runtime_qa = runtime / "qa"
    workspace_qa.mkdir(parents=True)
    runtime_qa.mkdir(parents=True)
    (workspace_qa / "latest.report.json").write_text(
        json.dumps({"verdict": "PASS", "passed": True}),
        encoding="utf-8",
    )
    (workspace_qa / "empty.report.json").write_text(
        json.dumps({"notes": "not a verdict"}),
        encoding="utf-8",
    )
    (runtime_qa / "report.json").write_text(
        json.dumps({"passed": True}),
        encoding="utf-8",
    )

    artifacts = discover_artifacts(workspace, runtime)

    assert artifacts["verdict"] == [
        "rt:qa/report.json",
        "ws:.polaris/qa/latest.report.json",
    ]


def test_runtime_dir_candidates_merge_artifacts_and_chain_results(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "L1-01"
    workspace.mkdir()
    runtime_base_a = tmp_path / "ramdisk-projects"
    runtime_base_b = tmp_path / "cache-projects"
    runtime_a = runtime_base_a / "l1-01-aaa" / "runtime"
    runtime_b = runtime_base_b / "l1-01-bbb" / "runtime"
    (runtime_a / "contracts").mkdir(parents=True)
    (runtime_b / "results").mkdir(parents=True)
    (runtime_a / "contracts" / "pm_tasks.contract.json").write_text(
        json.dumps({"overall_goal": "Build calculator"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (runtime_a / "contracts" / "plan.md").write_text("plan", encoding="utf-8")
    (runtime_b / "results" / "director.result.json").write_text(
        json.dumps({"total": 2, "successes": 1, "failures": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    (runtime_b / "results" / "integration_qa.result.json").write_text(
        json.dumps({"ran": True, "passed": False, "reason": "director failures"}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.utime(runtime_a, (100, 100))
    os.utime(runtime_b, (200, 200))
    monkeypatch.setattr(bench, "_RUNTIME_PROJECT_BASES", (runtime_base_a, runtime_base_b))

    runtime_dirs = resolve_runtime_dirs_for_workspace(workspace)
    artifacts = discover_artifacts(workspace, runtime_dirs)
    chain_results = read_chain_results_from_runtime_dirs(runtime_dirs)

    assert runtime_dirs == [runtime_b, runtime_a]
    assert artifacts["plan"] == ["rt:l1-01-aaa/contracts/plan.md", "rt:l1-01-aaa/contracts/pm_tasks.contract.json"]
    assert chain_results["contract_goal"] == "Build calculator"
    assert chain_results["qa_ran"] is True
    assert chain_results["director"] == {"total": 2, "successes": 1, "failures": 1}


def test_clean_chain_preserves_static_pass() -> None:
    record = _record()

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert record["static_checks_passed"] is True
    assert record["all_checks_passed"] is True
    assert all(gate["ok"] for gate in record["factory_gates"])


def test_real_run_and_llm_route_gates_are_fail_closed_when_present() -> None:
    record = _record(
        real_run_gate={"ok": True, "summary": "real run gate passed"},
        llm_route_audit={"ok": False, "summary": "LLM route audit failed: director"},
    )

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["real_run_gate"]["ok"] is True
    assert gates["llm_route_audit"]["ok"] is False
    assert record["all_checks_passed"] is False


# --- map_factory_run_to_chain_results ---


def test_map_completed_qa_passed_is_clean() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {
        "gates": [{"gate_name": "quality_gate", "passed": True, "message": "all good"}],
        "summary_json": {"director": {"total": 10, "successes": 8, "failures": 1, "blocked": 1}},
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "clean"
    assert result["qa_ran"] is True
    assert result["qa_passed"] is True
    assert result["qa_reason"] == "all good"
    assert result["director"] == {"total": 10, "successes": 8, "failures": 1, "blocked": 1}


def test_map_completed_qa_failed_is_qa_failed() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle = {
        "gates": [{"gate_name": "quality_gate", "passed": False, "message": "lint errors"}],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "qa_failed"
    assert result["qa_ran"] is True
    assert result["qa_passed"] is False
    assert result["qa_reason"] == "lint errors"


def test_map_failed_qa_gate_phase_is_qa_failed() -> None:
    run_status = {"status": "failed", "phase": "qa_gate"}
    audit_bundle = {
        "gates": [{"gate_name": "quality_gate", "passed": False, "message": "tests failed"}],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "qa_failed"
    assert result["qa_ran"] is True
    assert result["qa_passed"] is False


def test_map_failed_non_qa_phase_is_director_partial() -> None:
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "summary_json": {"director": {"total": 5, "successes": 2, "failures": 3, "blocked": 0}},
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "director_partial"
    assert result["qa_ran"] is False
    assert result["qa_passed"] is False
    assert result["director"] == {"total": 5, "successes": 2, "failures": 3, "blocked": 0}


def test_map_falls_back_to_run_status_gates() -> None:
    run_status: dict[str, Any] = {
        "status": "completed",
        "phase": "",
        "gates": [{"gate_name": "quality_gate", "passed": True, "message": "ok"}],
    }
    audit_bundle: dict[str, Any] = {}
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "clean"
    assert result["qa_ran"] is True
    assert result["qa_passed"] is True
    assert result["qa_reason"] == "ok"


def test_map_falls_back_to_events_tail_for_director() -> None:
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "events_tail": [
            {"stage": "other", "result": {"total": 99}},
            {"stage": "director_dispatch", "result": {"total": 7, "successes": 3, "failures": 4}},
        ],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "director_partial"
    assert result["director"] == {"total": 7, "successes": 3, "failures": 4, "blocked": None}


def test_map_summary_json_string_parsing() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {
        "gates": [{"gate_name": "quality_gate", "passed": True}],
        "summary_json": '{"director": {"total": 3, "successes": 3}}',
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["director"] == {"total": 3, "successes": 3, "failures": None, "blocked": None}


def test_map_summary_json_invalid_string_defaults() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {
        "gates": [{"gate_name": "quality_gate", "passed": True}],
        "summary_json": "not-json",
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["director"] == {"total": None, "successes": None, "failures": None, "blocked": None}


def test_map_no_qa_gate_defaults() -> None:
    run_status = {"status": "failed", "phase": "build"}
    audit_bundle: dict[str, Any] = {}
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "director_partial"
    assert result["qa_ran"] is False
    assert result["qa_passed"] is False
    assert result["qa_reason"] == ""


def test_map_contract_goal_always_empty() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {"gates": [{"gate_name": "quality_gate", "passed": True}]}
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["contract_goal"] == ""


def test_explicit_bench_session_id_is_registered(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_push(**kwargs: Any) -> str:
        captured.update(kwargs)
        return str(kwargs["session_id"])

    monkeypatch.setattr(bench, "_push_bench_session_to_backend", _fake_push)

    session_id = bench._ensure_bench_session(
        backend_url="http://127.0.0.1:49977",
        work_dir="/tmp/bench",
        project_ids=["L1-01"],
        total=1,
        metadata={"levels": [1]},
        requested_session_id="bench-explicit",
        token="secret",
    )

    assert session_id == "bench-explicit"
    assert captured["session_id"] == "bench-explicit"
    assert captured["backend_url"] == "http://127.0.0.1:49977"
    assert captured["token"] == "secret"


def test_bench_session_registration_uses_backend_assigned_id(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_push(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "bench-generated"

    monkeypatch.setattr(bench, "_push_bench_session_to_backend", _fake_push)

    session_id = bench._ensure_bench_session(
        backend_url="http://127.0.0.1:49977",
        work_dir="/tmp/bench",
        project_ids=["L1-01"],
        total=1,
    )

    assert session_id == "bench-generated"
    assert captured["session_id"] is None


def test_bench_record_counts_do_not_mark_pending_projects_failed() -> None:
    records = [
        {"all_checks_passed": False},
        {"all_checks_passed": True},
    ]

    counts = bench._bench_record_counts(records, total=12)

    assert counts == {
        "total": 12,
        "attempted": 2,
        "passed": 1,
        "failed": 1,
        "pending": 10,
    }


def _capture_run_chain_command(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    director_workflow_execution_mode: str | None = None,
    director_dispatch_driver: str | None = None,
) -> list[list[str]]:
    workspace = tmp_path / "L6-31"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench.subprocess, "run", _fake_run)
    kwargs: dict[str, Any] = {}
    if director_workflow_execution_mode is not None:
        kwargs["director_workflow_execution_mode"] = director_workflow_execution_mode
    if director_dispatch_driver is not None:
        kwargs["director_dispatch_driver"] = director_dispatch_driver
    bench.run_chain(
        {"id": "L6-31", "title": "Kanban", "brief": "Build Kanban", "test_focus": "runtime"},
        workspace,
        timeout_s=30,
        log_path=tmp_path / "L6-31.chain.log",
        **kwargs,
    )
    return captured


def test_run_chain_preserves_serial_director_workflow_by_default(monkeypatch: Any, tmp_path: Path) -> None:
    commands = _capture_run_chain_command(monkeypatch, tmp_path)

    assert len(commands) == 1
    cmd = commands[0]
    mode_index = cmd.index("--director-workflow-execution-mode")
    assert cmd[mode_index + 1] == "serial"


def test_run_chain_can_enable_parallel_director_workflow(monkeypatch: Any, tmp_path: Path) -> None:
    commands = _capture_run_chain_command(
        monkeypatch,
        tmp_path,
        director_workflow_execution_mode="parallel",
    )

    assert len(commands) == 1
    cmd = commands[0]
    mode_index = cmd.index("--director-workflow-execution-mode")
    assert cmd[mode_index + 1] == "parallel"


def test_run_chain_task_market_driver_plans_then_dispatches_market(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    commands = _capture_run_chain_command(
        monkeypatch,
        tmp_path,
        director_workflow_execution_mode="parallel",
        director_dispatch_driver="task-market",
    )

    assert len(commands) == 2
    planning_cmd, market_cmd = commands
    assert "--run-director" not in planning_cmd
    assert "--director-workflow-execution-mode" not in planning_cmd
    assert "run_market_chain.py" in market_cmd[1]
    assert "--fresh-market" in market_cmd


def test_main_task_market_driver_uses_legacy_chain_without_explicit_flag(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01",
            "--work-dir",
            str(tmp_path),
            "--director-dispatch-driver",
            "task-market",
        ],
    )
    monkeypatch.setattr(
        bench,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Known", "brief": "Build something"}],
    )
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench, "_push_bench_session_to_backend", lambda **_kwargs: "bench-task-market")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)

    def _legacy_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append("legacy")
        raise KeyboardInterrupt()

    def _http_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append("http")
        raise AssertionError("task-market dispatch must not use the HTTP factory runner path")

    monkeypatch.setattr(bench, "run_chain", _legacy_chain)
    monkeypatch.setattr(bench, "run_factory_chain", _http_chain)

    result = bench.main()

    assert result == 130
    assert calls == ["legacy"]


def test_main_marks_backend_session_failed_when_run_aborts(monkeypatch: Any, tmp_path: Path) -> None:
    completed: list[dict[str, Any]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01",
            "--work-dir",
            str(tmp_path),
            "--max-failed",
            "3",
        ],
    )
    monkeypatch.setattr(
        bench,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Abort case", "brief": "Build something"}],
    )
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench, "_push_bench_session_to_backend", lambda **_kwargs: "bench-abort")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_progress_to_backend", lambda **_kwargs: True)

    def _capture_complete(**kwargs: Any) -> bool:
        completed.append(kwargs)
        return True

    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", _capture_complete)

    def _abort(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated runner abort")

    monkeypatch.setattr(bench, "run_factory_chain", _abort)

    result = bench.main()

    assert result == 1
    assert completed, "bench session should be marked terminal on runner abort"
    assert completed[-1]["session_id"] == "bench-abort"
    assert completed[-1]["success"] is False
    assert completed[-1]["summary"]["failed"] == 1
    assert completed[-1]["summary"]["error"] == "simulated runner abort"


def test_main_rejects_unknown_explicit_project_ids(
    monkeypatch: Any,
    tmp_path: Path,
    capsys: Any,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01,L2-01",
            "--work-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(
        bench,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Known", "brief": "Build something"}],
    )

    def _unexpected_session(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("unknown explicit ids must fail before creating a bench session")

    monkeypatch.setattr(bench, "_ensure_bench_session", _unexpected_session)

    result = bench.main()

    captured = capsys.readouterr()
    assert result == 1
    assert "unknown project id(s): L2-01" in captured.out
    assert "refusing to run partial explicit selection" in captured.out


# --- run_factory_chain (API path) ---


def _setup_run_factory_chain_mocks(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    start_response: dict[str, Any] | None,
    terminal_status: dict[str, Any] | None,
    audit_bundle: dict[str, Any] | None,
) -> Path:
    workspace = tmp_path / "L2-07"
    workspace.mkdir()
    expected_workspace = str(workspace)

    def _fake_start_factory_run(_backend_url: str, _payload: dict[str, Any], token: str = "") -> dict[str, Any] | None:
        return start_response

    def _fake_wait_run_until_terminal(
        _backend_url: str,
        run_id: str,
        token: str = "",
        workspace: str = "",
        on_status: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        assert workspace == expected_workspace
        if on_status is not None and terminal_status is not None:
            on_status(terminal_status)
        return terminal_status

    def _fake_get_audit_bundle(
        _backend_url: str,
        _run_id: str,
        token: str = "",
        workspace: str = "",
    ) -> dict[str, Any] | None:
        assert workspace == expected_workspace
        return audit_bundle

    def _fake_cancel_factory_run(
        _backend_url: str,
        _run_id: str,
        *,
        reason: str = "",
        token: str = "",
        workspace: str = "",
    ) -> dict[str, Any]:
        assert reason
        assert workspace == expected_workspace
        return {"status": "cancelled"}

    monkeypatch.setattr(bench, "start_factory_run", _fake_start_factory_run)
    monkeypatch.setattr(bench, "wait_run_until_terminal", _fake_wait_run_until_terminal)
    monkeypatch.setattr(bench, "get_audit_bundle", _fake_get_audit_bundle)
    monkeypatch.setattr(bench, "cancel_factory_run", _fake_cancel_factory_run)

    return workspace


def test_run_factory_chain_success(monkeypatch: Any, tmp_path: Path) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-123"},
        terminal_status={"status": "completed", "phase": "qa_gate"},
        audit_bundle={
            "gates": [{"gate_name": "quality_gate", "passed": True, "message": "ok"}],
            "summary_json": {"director": {"total": 5, "successes": 5, "failures": 0, "blocked": 0}},
        },
    )

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
    )

    assert result["exit_code"] == 0
    assert result["run_id"] == "run-123"
    assert result["chain_results"]["exit_class"] == "clean"
    assert result["chain_results"]["qa_passed"] is True
    assert result["chain_results"]["director"] == {
        "total": 5,
        "successes": 5,
        "failures": 0,
        "blocked": 0,
    }
    assert "audit_bundle" in result


def test_run_factory_chain_start_failure(monkeypatch: Any, tmp_path: Path) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response=None,
        terminal_status=None,
        audit_bundle=None,
    )

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
    )

    assert result["exit_code"] == -1
    assert result["error"] == "start_failed"


def test_run_factory_chain_event_wait_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-456"},
        terminal_status=None,
        audit_bundle=None,
    )

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
    )

    assert result["exit_code"] == -1
    assert result["run_id"] == "run-456"
    assert result["error"] == "event_wait_timeout"


def test_run_factory_chain_failed_status(monkeypatch: Any, tmp_path: Path) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-789"},
        terminal_status={"status": "failed", "phase": "director_dispatch"},
        audit_bundle={
            "gates": [],
            "summary_json": {"director": {"total": 3, "successes": 1, "failures": 2}},
        },
    )

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
    )

    assert result["exit_code"] == 1
    assert result["chain_results"]["exit_class"] == "director_partial"


def test_run_factory_chain_on_stage_change_callback(monkeypatch: Any, tmp_path: Path) -> None:
    callbacks: list[tuple[str, dict[str, Any]]] = []

    def _on_stage_change(status: str, status_dict: dict[str, Any]) -> None:
        callbacks.append((status, status_dict))

    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-cb"},
        terminal_status={"status": "completed", "phase": "qa_gate"},
        audit_bundle={
            "gates": [{"gate_name": "quality_gate", "passed": True}],
        },
    )

    run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
        on_stage_change=_on_stage_change,
    )

    assert len(callbacks) == 1
    assert callbacks[0][0] == "completed"
