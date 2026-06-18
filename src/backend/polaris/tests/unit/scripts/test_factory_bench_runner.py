"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from scripts.factory_bench import run_factory_bench as bench
from scripts.factory_bench.run_factory_bench import (
    apply_factory_bench_gates,
    map_factory_run_to_chain_results,
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


def test_clean_chain_preserves_static_pass() -> None:
    record = _record()

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert record["static_checks_passed"] is True
    assert record["all_checks_passed"] is True
    assert all(gate["ok"] for gate in record["factory_gates"])


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


def test_map_completed_qa_failed_is_hard_failed() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle = {
        "gates": [{"gate_name": "quality_gate", "passed": False, "message": "lint errors"}],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "hard_failed"
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
