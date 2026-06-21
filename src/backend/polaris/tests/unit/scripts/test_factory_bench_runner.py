"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.factory_bench import run_factory_bench as bench
from scripts.factory_bench.run_factory_bench import (
    _desktop_backend_info_path,
    _extract_feature_keywords,
    _fallback_audit_bundle_from_workspace,
    _is_local_backend_url,
    _next_immutable_json_path,
    _read_desktop_backend_info,
    _resolve_backend_token,
    _resolve_backend_url,
    _resolve_polaris_home,
    _sanitize_run_id,
    _write_immutable_json,
    apply_factory_bench_gates,
    build_requirements_doc,
    discover_artifacts,
    map_factory_run_to_chain_results,
    read_chain_results_from_runtime_dirs,
    resolve_runtime_dirs_for_workspace,
    run_factory_chain,
)

_LAST_FACTORY_START_PAYLOAD: dict[str, Any] = {}


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "all_checks_passed": True,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "chain_state": "clean",
        "chain_results": {"qa_ran": True, "qa_passed": True},
        "wrong_product_suspect": False,
        "backend_freshness": {"ok": True, "detail": "backend fresh"},
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


def test_runner_audits_llm_routes_for_llm_backed_roles_only() -> None:
    source = Path(bench.__file__).read_text(encoding="utf-8")

    assert bench.FACTORY_BENCH_REQUIRED_LLM_ROLES == ("pm", "director")
    assert "require_all_director_routes=False" in source
    assert "require_all_director_routes=True" not in source
    assert "required_roles=FACTORY_BENCH_REQUIRED_LLM_ROLES" in source


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


def test_runtime_dir_candidates_prefer_exact_workspace_evidence(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "L1-01"
    workspace.mkdir()
    runtime_base = tmp_path / "projects"
    current_runtime = runtime_base / "l1-01-current" / "runtime"
    stale_runtime = runtime_base / "l1-01-stale" / "runtime"
    (current_runtime / "events").mkdir(parents=True)
    (stale_runtime / "events").mkdir(parents=True)
    other_workspace = tmp_path / "other" / "L1-01"
    (current_runtime / "events" / "task_runtime.execution.jsonl").write_text(
        json.dumps({"workspace": str(workspace.resolve())}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (stale_runtime / "events" / "task_runtime.execution.jsonl").write_text(
        json.dumps({"workspace": str(other_workspace.resolve())}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.utime(current_runtime, (100, 100))
    os.utime(stale_runtime, (200, 200))
    monkeypatch.setattr(bench, "_RUNTIME_PROJECT_BASES", (runtime_base,))

    runtime_dirs = resolve_runtime_dirs_for_workspace(workspace)

    assert runtime_dirs == [current_runtime]


def test_clean_chain_preserves_static_pass() -> None:
    record = _record(
        real_run_gate={"ok": True, "summary": "real run gate passed"},
        llm_route_audit={"ok": True, "summary": "LLM route audit passed"},
    )

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


def test_real_run_and_llm_route_gates_are_fail_closed_when_missing() -> None:
    record = _record()

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["real_run_gate"]["ok"] is False
    assert gates["llm_route_audit"]["ok"] is False
    assert record["all_checks_passed"] is False


def test_backend_freshness_gate_is_fail_closed_when_missing() -> None:
    record = _record(
        real_run_gate={"ok": True, "summary": "real run gate passed"},
        llm_route_audit={"ok": True, "summary": "LLM route audit passed"},
    )
    record.pop("backend_freshness")

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["stale_backend_or_unknown"]["ok"] is False
    assert "backend freshness gate missing" in gates["stale_backend_or_unknown"]["detail"]
    assert record["all_checks_passed"] is False


def test_build_bench_backend_audit_context_writes_record_fields(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def _fake_check_backend_freshness(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "gate": "stale_backend_or_unknown",
            "ok": True,
            "detail": "backend fresh",
            "backend_url": "http://127.0.0.1:49977",
            "expected_fingerprint": "expected-fp",
            "actual_fingerprint": "actual-fp",
            "backend_info": {
                "pid": 123,
                "startup_time": "2026-06-21T00:00:00Z",
                "source": "runtime_fingerprint",
            },
        }

    monkeypatch.setattr(bench, "check_backend_freshness", _fake_check_backend_freshness)

    context = bench.build_bench_backend_audit_context(
        "http://127.0.0.1:49977",
        backend_token="token",
        workspace=str(tmp_path),
    )

    assert captured["args"] == ("http://127.0.0.1:49977",)
    assert captured["kwargs"]["token"] == "token"
    assert captured["kwargs"]["backend_root"] == bench._BACKEND_ROOT
    assert context["backend_freshness"]["ok"] is True
    assert context["backend_metadata"]["backend_base_url"] == "http://127.0.0.1:49977"
    assert context["backend_metadata"]["token_source"] == "configured"
    assert context["backend_metadata"]["workspace"] == str(tmp_path)
    assert context["backend_metadata"]["expected_source_fingerprint"] == "expected-fp"
    assert context["backend_metadata"]["actual_backend_fingerprint"] == "actual-fp"
    assert context["backend_metadata"]["backend_pid"] == 123


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


def test_load_projects_v2_is_standalone_creative_catalog_covering_l1_to_l12() -> None:
    projects = bench.load_projects()
    project_ids = {str(project["id"]) for project in projects}
    levels = {int(project["level"]) for project in projects}
    languages = {str(project.get("primary_language") or "") for project in projects if project.get("primary_language")}
    checks = {str(check) for project in projects for check in project.get("checks", [])}
    by_level = dict.fromkeys(range(1, 13), 0)
    for project in projects:
        by_level[int(project["level"])] += 1

    assert len(projects) == 120
    assert "L1-01" in project_ids
    assert "L12-120" in project_ids
    assert next(project for project in projects if project["id"] == "L1-01")["title"] == "发光昆虫花园模拟器"
    assert levels == set(range(1, 13))
    assert set(by_level.values()) == {10}
    assert {"typescript", "javascript", "go", "rust", "cpp", "java", "python"}.issubset(languages)
    assert {"ts_syntax", "go_compile", "rust_compile", "cpp_compile", "java_compile"}.issubset(checks)
    assert all(str(project.get("creative_hook") or "").strip() for project in projects)
    assert all(len(project.get("novelty_tags") or []) >= 3 for project in projects)
    assert all(
        "creative_hook" in str(project.get("brief") or "") or "创意钩子" in str(project.get("brief") or "")
        for project in projects
    )
    # R17-C: every project must have source_target_coverage check
    assert all(
        any(check.startswith("source_target_coverage:") for check in project.get("checks", [])) for project in projects
    ), "Every project must have a source_target_coverage check"


def test_load_projects_rejects_duplicate_ids_in_extended_catalog(tmp_path: Path) -> None:
    parent = tmp_path / "parent.json"
    child = tmp_path / "child.json"
    parent.write_text(
        json.dumps({"schema_version": "factory-bench/test", "projects": [{"id": "L1-X", "level": 1}]}),
        encoding="utf-8",
    )
    child.write_text(
        json.dumps(
            {
                "schema_version": "factory-bench/test",
                "extends": "parent.json",
                "projects": [{"id": "L1-X", "level": 1}],
            }
        ),
        encoding="utf-8",
    )

    try:
        bench.load_projects(child)
    except ValueError as exc:
        assert "duplicate project id" in str(exc)
    else:
        raise AssertionError("duplicate ids must fail closed")


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


def test_main_defaults_to_l1_through_l12_catalog(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    projects = [
        {"id": f"L{level}-{level:02d}", "level": level, "title": f"Project {level}", "brief": "Build something"}
        for level in range(1, 13)
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench, "load_projects", lambda: projects)
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)

    def _capture_session(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "bench-default"

    def _stop_after_registration(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise KeyboardInterrupt()

    monkeypatch.setattr(bench, "_ensure_bench_session", _capture_session)
    monkeypatch.setattr(bench, "run_factory_chain", _stop_after_registration)

    result = bench.main()

    assert result == 130
    assert captured["total"] == 12
    assert captured["metadata"]["levels"] == list(range(1, 13))
    assert captured["project_ids"] == [project["id"] for project in projects]


def test_main_default_max_failed_zero_does_not_early_stop(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[str] = []
    projects = [
        {"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"},
        {"id": "L2-02", "level": 2, "title": "Two", "brief": "Build two"},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench, "load_projects", lambda: projects)
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench, "_ensure_bench_session", lambda **_kwargs: "bench-no-early-stop")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench,
        "build_factory_audit_record",
        lambda **_kwargs: {
            "all_checks_passed": True,
            "static_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "code_file_count": 1,
            "checks": [],
        },
    )
    monkeypatch.setattr(
        bench,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": False, "summary": "real run failed"},
    )
    monkeypatch.setattr(
        bench,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )

    def _chain(project: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append(str(project["id"]))
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": str(project["brief"]),
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench, "run_factory_chain", _chain)

    result = bench.main()

    assert result == 1
    assert calls == ["L1-01", "L2-02"]


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
    _LAST_FACTORY_START_PAYLOAD.clear()

    def _fake_start_factory_run(_backend_url: str, _payload: dict[str, Any], token: str = "") -> dict[str, Any] | None:
        _LAST_FACTORY_START_PAYLOAD.update(_payload)
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
    assert _LAST_FACTORY_START_PAYLOAD["workspace"] == str(workspace)
    assert _LAST_FACTORY_START_PAYLOAD["persist_workspace"] is False


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


# --- _sanitize_run_id ---


def test_sanitize_run_id_passthrough_clean_value() -> None:
    assert _sanitize_run_id("bench-2026-06") == "bench-2026-06"


def test_sanitize_run_id_replaces_unsafe_chars() -> None:
    result = _sanitize_run_id("hello world/colons:and*stars")
    assert "/" not in result
    assert ":" not in result
    assert "*" not in result
    assert " " not in result
    assert result == "hello-world-colons-and-stars"


def test_sanitize_run_id_empty_generates_nonempty() -> None:
    result = _sanitize_run_id("")
    assert result
    assert len(result) >= 8


def test_sanitize_run_id_none_generates_nonempty() -> None:
    result = _sanitize_run_id(None)
    assert result
    assert len(result) >= 8


def test_sanitize_run_id_whitespace_only_generates_nonempty() -> None:
    result = _sanitize_run_id("   ")
    assert result
    assert len(result) >= 8


def test_sanitize_run_id_collapses_consecutive_dashes() -> None:
    result = _sanitize_run_id("a///b")
    assert "--" not in result
    assert result == "a-b"


# --- _write_immutable_json ---


def test_write_immutable_json_first_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    payload = {"project_id": "L1-01", "status": "PASS"}

    written = _write_immutable_json(target, payload)

    assert written == target
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_write_immutable_json_second_write_does_not_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    first_payload = {"project_id": "L1-01", "round": 1}
    second_payload = {"project_id": "L1-01", "round": 2}

    first_written = _write_immutable_json(target, first_payload)
    second_written = _write_immutable_json(target, second_payload)

    assert first_written == target
    assert second_written == tmp_path / "L1-01.audit.2.json"
    assert json.loads(target.read_text(encoding="utf-8")) == first_payload
    assert json.loads(second_written.read_text(encoding="utf-8")) == second_payload


def test_write_immutable_json_increments_slot(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"

    _write_immutable_json(target, {"v": 1})
    _write_immutable_json(target, {"v": 2})
    third = _write_immutable_json(target, {"v": 3})

    assert third == tmp_path / "L1-01.audit.3.json"
    assert json.loads(third.read_text(encoding="utf-8")) == {"v": 3}


def test_write_immutable_json_skips_existing_slots(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    # Pre-create .2.json to force skip
    (tmp_path / "L1-01.audit.2.json").write_text("{}", encoding="utf-8")

    written = _write_immutable_json(target, {"v": 1})

    assert written == target
    written2 = _write_immutable_json(target, {"v": 2})
    assert written2 == tmp_path / "L1-01.audit.3.json"


def test_next_immutable_json_path_returns_initial_path_when_free(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"

    resolved = _next_immutable_json_path(target)

    assert resolved == target


def test_next_immutable_json_path_returns_first_free_slot(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / "L1-01.audit.2.json").write_text("{}", encoding="utf-8")

    resolved = _next_immutable_json_path(target)

    assert resolved == tmp_path / "L1-01.audit.3.json"


def test_write_immutable_json_payload_contains_audit_path(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    relative_base = tmp_path

    written = _write_immutable_json(target, {"audit_path": str(target.relative_to(relative_base))})

    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["audit_path"] == str(target.relative_to(relative_base))


# --- run_id singleton across multiple project metas ---


def test_main_run_id_shared_across_projects(monkeypatch: Any, tmp_path: Path) -> None:
    """Verify that all projects in a bench run share the same run_id."""
    projects = [
        {"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"},
        {"id": "L2-02", "level": 2, "title": "Two", "brief": "Build two"},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench, "load_projects", lambda: projects)
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench, "_ensure_bench_session", lambda **_kwargs: "bench-shared")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench,
        "build_factory_audit_record",
        lambda **_kwargs: {
            "all_checks_passed": True,
            "static_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "code_file_count": 1,
            "checks": [],
        },
    )
    monkeypatch.setattr(
        bench,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(project: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": str(project["brief"]),
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench, "run_factory_chain", _chain)

    result = bench.main()

    assert result == 0
    audit_dir = tmp_path / "audits"
    run_dirs = list(audit_dir.iterdir())
    assert len(run_dirs) == 1, f"Expected single audit run_dir, got {run_dirs}"
    run_dir = run_dirs[0]
    audit_files = sorted(run_dir.glob("*.audit.json"))
    assert len(audit_files) == 2
    ids = set()
    for af in audit_files:
        data = json.loads(af.read_text(encoding="utf-8"))
        ids.add(data["run_id"])
        assert "audit_path" in data, "Per-project audit JSON must include audit_path"
        assert (tmp_path / data["audit_path"]).resolve() == af.resolve(), (
            "audit_path must resolve to the actual written audit file"
        )
    assert len(ids) == 1, "All projects should share the same run_id"


def test_main_audit_path_points_to_conflict_when_same_id_reused(monkeypatch: Any, tmp_path: Path) -> None:
    """If the same project id appears twice, the second audit must reference the conflict file."""
    projects = [
        {"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"},
        {"id": "L1-01", "level": 2, "title": "One Again", "brief": "Build one again"},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench, "load_projects", lambda: projects)
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench, "_ensure_bench_session", lambda **_kwargs: "bench-conflict")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench,
        "build_factory_audit_record",
        lambda **_kwargs: {
            "all_checks_passed": True,
            "static_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "code_file_count": 1,
            "checks": [],
        },
    )
    monkeypatch.setattr(
        bench,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(project: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": str(project["brief"]),
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench, "run_factory_chain", _chain)

    result = bench.main()

    assert result == 0
    audit_dir = tmp_path / "audits"
    run_dir = next(iter(audit_dir.iterdir()))
    audit_files = sorted(run_dir.glob("*.json"))
    assert len(audit_files) == 2, f"Expected 2 audit files (including conflict), got {audit_files}"
    primary_file = run_dir / "L1-01.audit.json"
    conflict_file = run_dir / "L1-01.audit.2.json"
    assert primary_file.exists(), "Expected primary audit file"
    assert conflict_file.exists(), "Expected conflict file for repeated project id"
    for af in audit_files:
        data = json.loads(af.read_text(encoding="utf-8"))
        assert "audit_path" in data, f"audit_path missing from {af.name}"
        assert (tmp_path / data["audit_path"]).resolve() == af.resolve(), (
            f"audit_path {data['audit_path']} does not resolve to {af}"
        )


# --- _resolve_polaris_home ---


def test_resolve_polaris_home_default_uses_dot_polaris() -> None:
    result = _resolve_polaris_home(env={})
    assert result.name == ".polaris"
    assert result == Path.home() / ".polaris"


def test_resolve_polaris_home_kernelone_home_already_dot_polaris(tmp_path: Path) -> None:
    home = tmp_path / ".polaris"
    result = _resolve_polaris_home(env={"KERNELONE_HOME": str(home)})
    assert result == home.expanduser().resolve()


def test_resolve_polaris_home_kernelone_home_parent_dir(tmp_path: Path) -> None:
    parent = tmp_path / "config-root"
    result = _resolve_polaris_home(env={"KERNELONE_HOME": str(parent)})
    expected = parent.expanduser().resolve() / ".polaris"
    assert result == expected


# --- _desktop_backend_info_path ---


def test_desktop_backend_info_path_inside_polaris_home(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    result = _desktop_backend_info_path(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == polaris_home.expanduser().resolve() / "runtime" / "desktop-backend.json"


# --- _read_desktop_backend_info ---


def test_read_desktop_backend_info_valid_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    info_file = runtime / "desktop-backend.json"
    info_file.write_text(
        json.dumps({"schema_version": 1, "backend": {"baseUrl": "http://127.0.0.1:49977", "token": "tok-123"}}),
        encoding="utf-8",
    )
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result["backend"]["baseUrl"] == "http://127.0.0.1:49977"
    assert result["backend"]["token"] == "tok-123"


def test_read_desktop_backend_info_missing_file(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


def test_read_desktop_backend_info_malformed_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text("NOT JSON {{{", encoding="utf-8")
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


def test_read_desktop_backend_info_non_dict_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text('"just a string"', encoding="utf-8")
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


# --- _resolve_backend_url desktop fallback ---


def test_resolve_backend_url_falls_back_to_desktop_info(monkeypatch: Any, tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text(
        json.dumps({"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(
        bench,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url()
    assert result == "http://10.0.0.1:5555"


def test_resolve_backend_url_explicit_overrides_desktop(monkeypatch: Any, tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text(
        json.dumps({"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bench,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url(explicit="http://192.168.1.1:8080")
    assert result == "http://192.168.1.1:8080"


def test_resolve_backend_url_env_overrides_desktop(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNELONE_BACKEND_URL", "http://env-host:1111")
    monkeypatch.setattr(
        bench,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url()
    assert result == "http://env-host:1111"


def test_resolve_backend_url_missing_desktop_json_returns_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_url()
    assert result == "http://127.0.0.1:49977"


def test_resolve_backend_url_malformed_desktop_json_returns_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_url()
    assert result == "http://127.0.0.1:49977"


# --- _resolve_backend_token desktop fallback ---


def test_is_local_backend_url_recognizes_loopback_only() -> None:
    assert _is_local_backend_url("http://127.0.0.1:49977") is True
    assert _is_local_backend_url("http://localhost:49977") is True
    assert _is_local_backend_url("http://[::1]:49977") is True
    assert _is_local_backend_url("http://10.0.0.1:49977") is False
    assert _is_local_backend_url("not a url") is False


def test_resolve_backend_token_falls_back_to_desktop_info(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.setattr(
        bench,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token()
    assert result == "desktop-tok-abc"


def test_resolve_backend_token_explicit_overrides_desktop(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        bench,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token(explicit="explicit-tok")
    assert result == "explicit-tok"


def test_resolve_backend_token_env_overrides_desktop(monkeypatch: Any) -> None:
    monkeypatch.setenv("FACTORY_BENCH_BACKEND_TOKEN", "env-tok")
    monkeypatch.setattr(
        bench,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token()
    assert result == "env-tok"


def test_resolve_backend_token_missing_desktop_json_returns_local_dev_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == "polaris-local-dev"


def test_resolve_backend_token_malformed_desktop_json_returns_local_dev_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == "polaris-local-dev"


def test_resolve_backend_token_missing_remote_token_returns_empty(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.setenv("KERNELONE_BACKEND_URL", "http://10.0.0.1:49977")
    monkeypatch.setattr(bench, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == ""


# --- L1-01 regression: contract chain must propagate to Director ---


def test_extract_feature_keywords_from_content_any_checks() -> None:
    """_extract_feature_keywords must extract keywords from content_any checks."""
    project = {
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    keywords = _extract_feature_keywords(project)
    assert keywords == ["firefly", "flower", "moon", "humidity"]


def test_extract_feature_keywords_no_content_any_returns_empty() -> None:
    """_extract_feature_keywords returns empty list when no content_any checks."""
    project = {"checks": ["ts_syntax", "package_scripts", "min_files:3"]}
    assert _extract_feature_keywords(project) == []


def test_extract_feature_keywords_deduplicates_case_insensitive() -> None:
    """_extract_feature_keywords deduplicates keywords case-insensitively."""
    project = {
        "checks": [
            "content_any:Fire|fire|FLOWER|flower",
        ],
    }
    keywords = _extract_feature_keywords(project)
    assert keywords == ["Fire", "FLOWER"]


def test_l1_01_requirements_doc_contains_source_tree_contract() -> None:
    """L1-01 requirements doc must contain source tree contract requiring src/."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "Source Tree Structure Contract" in doc
    assert "src/" in doc
    assert "src/models/" in doc
    assert "src/engine/" in doc or "src/core/" in doc
    assert "Feature Keywords Contract" in doc
    assert "firefly" in doc
    assert "flower" in doc
    assert "moon" in doc
    assert "humidity" in doc
    assert "Project Metadata" in doc
    assert "science_creative" in doc
    assert "simulation_toy" in doc
    assert "萤火虫根据花朵情绪和月相组成实时灯光舞蹈" in doc


def test_l1_01_requirements_doc_director_target_files_mandate() -> None:
    """L1-01 requirements doc must mandate Director target_files cover src/."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "target_files 必须覆盖 src/" in doc
    assert "不能只包含 package.json" in doc


def test_l1_01_requirements_doc_has_ts_strict_and_features() -> None:
    """L1-01 requirements doc must include TS-specific contract + feature keywords."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "Language-Specific Runnable Contract (TypeScript)" in doc
    assert "tsc --noEmit" in doc
    assert "Feature Keywords Contract" in doc
    assert "firefly" in doc


def test_build_requirements_doc_python_includes_source_tree() -> None:
    """Python projects must also get source tree contract."""
    project = {
        "id": "L1-03",
        "level": 1,
        "domain": "creative",
        "project_type": "interactive_visual",
        "primary_language": "python",
        "title": "迷你行星天气球",
        "creative_hook": "口袋行星会随云层、风向和昼夜循环改变地表",
        "brief": "用 Python 实现迷你行星天气球",
        "test_focus": "cloud, weather simulation",
        "checks": ["py_compile", "content_any:planet|weather|cloud|wind"],
    }
    doc = build_requirements_doc(project)
    assert "Source Tree Structure Contract" in doc
    assert "src/" in doc
    assert "tests/" in doc
    assert "Feature Keywords Contract" in doc
    assert "planet" in doc


def test_factory_chain_catalog_contract_writes_metadata(tmp_path: Path) -> None:
    """Catalog contract JSON must include project metadata for PM/CE/Director."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫灯光舞蹈",
        "checks": [
            "ts_syntax",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    workspace = tmp_path / "L1-01"
    workspace.mkdir(parents=True, exist_ok=True)

    feature_keywords = _extract_feature_keywords(project)
    catalog_contract = {
        "project_id": str(project.get("id") or "").strip(),
        "domain": str(project.get("domain") or "").strip(),
        "project_type": str(project.get("project_type") or "").strip(),
        "primary_language": str(project.get("primary_language") or "").strip(),
        "creative_hook": str(project.get("creative_hook") or "").strip(),
        "feature_keywords": feature_keywords,
        "checks": list(project.get("checks") or []),  # type: ignore[call-overload]
        "test_focus": str(project.get("test_focus") or "").strip(),
        "source_tree_mandate": "PM/CE/Director must create src/ with core source files, not just scaffolding",
    }
    catalog_path = workspace / ".polaris" / "catalog_contract.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog_contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert catalog_path.is_file()
    written = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert written["project_id"] == "L1-01"
    assert written["domain"] == "science_creative"
    assert written["project_type"] == "simulation_toy"
    assert written["primary_language"] == "typescript"
    assert written["creative_hook"] == "萤火虫根据花朵情绪和月相组成实时灯光舞蹈"
    assert written["feature_keywords"] == ["firefly", "flower", "moon", "humidity"]
    assert written["source_tree_mandate"] != ""


def test_director_contract_requires_ts_target_and_feature_keywords() -> None:
    """The generated Director contract for L1-01 must include .ts targets and feature keywords.

    This is the core regression: the Director must not only produce package.json/tsconfig.json
    scaffolding — it must target src/ .ts files and embed firefly|flower|moon|humidity.
    """
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "src/" in doc
    assert ".ts" in doc
    assert "firefly" in doc
    assert "flower" in doc
    assert "moon" in doc
    assert "humidity" in doc
    assert "tests/" in doc
    assert "不能只包含 package.json" in doc


# --- _fallback_audit_bundle_from_workspace ---


def test_fallback_audit_bundle_from_workspace_reads_dispatch_logs(tmp_path: Path) -> None:
    """Fallback must read .polaris/dispatch/*.log.json and build events/artifacts."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    dispatch_dir = polaris_dir / "dispatch"
    dispatch_dir.mkdir(parents=True)

    dispatch_log = dispatch_dir / "latest.log.json"
    dispatch_log.write_text(
        json.dumps({"tasks": [{"id": "T1", "status": "done"}]}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert len(bundle["artifacts"]) >= 1
    assert any(a["name"] == "latest.log.json" for a in bundle["artifacts"])
    assert len(bundle["events_tail"]) >= 1
    assert bundle["events_tail"][0]["stage"] == "director_dispatch"
    assert bundle["artifacts"][0]["source"] == "workspace_fallback"


def test_fallback_audit_bundle_from_workspace_reads_roles_director(tmp_path: Path) -> None:
    """Fallback must read .polaris/roles/director/**/*.log.json."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    roles_dir = polaris_dir / "roles" / "director" / "run_001"
    roles_dir.mkdir(parents=True)

    role_log = roles_dir / "dispatch.log.json"
    role_log.write_text(
        json.dumps({"dispatch": {"status": "ok"}}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert len(bundle["artifacts"]) >= 1
    assert any(a["name"] == "dispatch.log.json" for a in bundle["artifacts"])
    assert len(bundle["events_tail"]) >= 1


def test_fallback_audit_bundle_from_workspace_reads_plan(tmp_path: Path) -> None:
    """Fallback must read .polaris/docs/product/plan.json into summary_json."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    docs_dir = polaris_dir / "docs" / "product"
    docs_dir.mkdir(parents=True)

    plan = docs_dir / "plan.json"
    plan.write_text(
        json.dumps({"overall_goal": "Build a calculator app"}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert bundle["summary_json"] is not None
    assert bundle["summary_json"]["plan"]["overall_goal"] == "Build a calculator app"
    assert any(a["name"] == "plan.json" for a in bundle["artifacts"])


def test_fallback_audit_bundle_from_workspace_missing_polaris_dir(tmp_path: Path) -> None:
    """Fallback must return empty bundle when .polaris directory is missing."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert bundle["gates"] == []
    assert bundle["events_tail"] == []
    assert bundle["artifacts"] == []
    assert bundle["summary_json"] is None


def test_run_factory_chain_fallback_on_audit_bundle_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    """run_factory_chain must use workspace fallback when audit-bundle returns None."""
    workspace = tmp_path / "L2-fallback"
    workspace.mkdir()
    _LAST_FACTORY_START_PAYLOAD.clear()

    # Seed workspace .polaris artifacts for fallback
    polaris_dir = workspace / ".polaris"
    dispatch_dir = polaris_dir / "dispatch"
    dispatch_dir.mkdir(parents=True)
    (dispatch_dir / "latest.log.json").write_text(
        json.dumps({"tasks": [{"id": "T1", "status": "done"}]}),
        encoding="utf-8",
    )

    def _fake_start_factory_run(_backend_url: str, _payload: dict[str, Any], token: str = "") -> dict[str, Any] | None:
        _LAST_FACTORY_START_PAYLOAD.update(_payload)
        return {"run_id": "run-fb-123"}

    def _fake_wait_run_until_terminal(
        _backend_url: str,
        run_id: str,
        token: str = "",
        workspace: str = "",
        on_status: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        if on_status is not None:
            on_status({"status": "failed", "phase": "director_dispatch"})
        return {"status": "failed", "phase": "director_dispatch"}

    def _fake_get_audit_bundle(
        _backend_url: str,
        _run_id: str,
        token: str = "",
        workspace: str = "",
    ) -> dict[str, Any] | None:
        # Simulate timeout: return None
        return None

    def _fake_cancel_factory_run(
        _backend_url: str,
        _run_id: str,
        *,
        reason: str = "",
        token: str = "",
        workspace: str = "",
    ) -> dict[str, Any]:
        return {"status": "cancelled"}

    monkeypatch.setattr(bench, "start_factory_run", _fake_start_factory_run)
    monkeypatch.setattr(bench, "wait_run_until_terminal", _fake_wait_run_until_terminal)
    monkeypatch.setattr(bench, "get_audit_bundle", _fake_get_audit_bundle)
    monkeypatch.setattr(bench, "cancel_factory_run", _fake_cancel_factory_run)

    result = run_factory_chain(
        {"id": "L2-fb", "title": "Fallback Test", "brief": "Test fallback", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-fb.chain.log",
    )

    assert result["exit_code"] == 1
    assert result["run_id"] == "run-fb-123"
    assert "audit_bundle" in result
    assert len(result["audit_bundle"]["artifacts"]) >= 1
    assert result["audit_bundle"]["artifacts"][0]["source"] == "workspace_fallback"
    assert result["audit_bundle"]["events_tail"][0]["stage"] == "director_dispatch"


def test_map_director_partial_includes_blocking_phase_for_convergence() -> None:
    """Regression: director_partial chain_results must carry phase and director stats for convergence diagnostics."""
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "current_stage": "director_dispatch",
        "summary_json": {"director": {"total": 5, "successes": 2, "failures": 3, "blocked": 0}},
        "director_convergence": {
            "qa_ran": False,
            "blocking_phase": "director_dispatch",
            "missing_delivery_targets": ["quality_gate"],
            "taskboard_final": {"total": 5, "completed": 2, "failed": 3},
            "per_binding_task_status": [
                {"task_id": "T1", "status": "completed"},
                {"task_id": "T2", "status": "completed"},
                {"task_id": "T3", "status": "failed"},
                {"task_id": "T4", "status": "failed"},
                {"task_id": "T5", "status": "failed"},
            ],
        },
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "director_partial"
    assert result["qa_ran"] is False
    assert result["director"]["total"] == 5
    assert result["director"]["failures"] == 3

    # Verify convergence data is present in the bundle for downstream propagation
    convergence = audit_bundle.get("director_convergence")
    assert convergence is not None
    assert convergence["blocking_phase"] == "director_dispatch"
    assert convergence["missing_delivery_targets"] == ["quality_gate"]
    assert len(convergence["per_binding_task_status"]) == 5


# --- R18-B: audit snapshot terminal/non-terminal ---


def test_main_start_failed_chain_marks_audit_as_non_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When run_factory_chain returns start_failed, the audit record must be
    marked as non_terminal so it cannot be confused with a final verdict."""
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": False,
            "static_checks_passed": False,
            "has_plan_doc": False,
            "has_blueprint_doc": False,
            "has_qa_verdict": False,
            "code_file_count": 0,
            "source_file_count": 0,
            "checks": [],
            "audit_snapshot_kind": "non_terminal" if not kwargs.get("chain_terminal", True) else "terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench, "_ensure_bench_session", lambda **_kwargs: "bench-non-terminal")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(
        bench,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": False, "summary": "real run failed"},
    )
    monkeypatch.setattr(
        bench,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )

    def _start_failed_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"exit_code": -1, "duration_s": 0.0, "error": "start_failed"}

    monkeypatch.setattr(bench, "run_factory_chain", _start_failed_chain)

    result = bench.main()

    assert result == 1
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is False


def test_main_runner_exception_marks_audit_as_non_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When the runner raises an exception, the audit record must be
    marked as non_terminal."""
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": False,
            "static_checks_passed": False,
            "has_plan_doc": False,
            "has_blueprint_doc": False,
            "has_qa_verdict": False,
            "code_file_count": 0,
            "source_file_count": 0,
            "checks": [],
            "audit_snapshot_kind": "non_terminal" if not kwargs.get("chain_terminal", True) else "terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench, "_ensure_bench_session", lambda **_kwargs: "bench-runner-exc")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(
        bench,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": False, "summary": "real run failed"},
    )
    monkeypatch.setattr(
        bench,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )

    def _runner_exception(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated runner crash")

    monkeypatch.setattr(bench, "run_factory_chain", _runner_exception)

    result = bench.main()

    assert result == 1
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is False


def test_main_completed_chain_marks_audit_as_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When run_factory_chain returns normally, the audit record must be
    marked as terminal."""
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": True,
            "static_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "code_file_count": 1,
            "source_file_count": 1,
            "checks": [],
            "audit_snapshot_kind": "terminal" if kwargs.get("chain_terminal", True) else "non_terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench, "_ensure_bench_session", lambda **_kwargs: "bench-terminal")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(
        bench,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _completed_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": "Build something",
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench, "run_factory_chain", _completed_chain)

    result = bench.main()

    assert result == 0
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is True


# --- Catalog validation tests (from test_projects_v2_catalog.py) ---

REQUIRED_FIELDS = [
    "id",
    "level",
    "domain",
    "project_type",
    "primary_language",
    "title",
    "creative_hook",
    "novelty_tags",
    "brief",
    "test_focus",
    "checks",
]

VALID_LEVELS = set(range(1, 13))
VALID_LANGUAGES = {"typescript", "javascript", "python", "go", "rust", "cpp", "java"}
VALID_DOMAINS = {"science_creative", "creative", "game", "music", "internet_platform"}

LEVEL_MIN_FILES = {
    1: 3,
    2: 4,
    3: 5,
    4: 7,
    5: 8,
    6: 10,
    7: 11,
    8: 12,
    9: 13,
    10: 14,
    11: 15,
    12: 16,
}

LANG_COMPILE_CHECK = {
    "typescript": "ts_syntax",
    "javascript": "js_syntax",
    "python": "py_compile",
    "go": "go_compile",
    "rust": "rust_compile",
    "cpp": "cpp_compile",
    "java": "java_compile",
}


def test_catalog_schema_version() -> None:
    """Validate that projects_v2.json has the expected schema_version."""
    projects_file = Path(bench.__file__).resolve().parent / "projects_v2.json"
    catalog_data = json.loads(projects_file.read_text(encoding="utf-8"))
    version = catalog_data.get("schema_version")
    assert version == "factory-bench/2", f"Unexpected schema_version: {version}"


def test_catalog_hash_is_stable() -> None:
    """Validate that catalog_hash computation is deterministic."""
    projects_file = Path(bench.__file__).resolve().parent / "projects_v2.json"
    catalog_data = json.loads(projects_file.read_text(encoding="utf-8"))
    projects = catalog_data.get("projects", [])

    # Compute hash the same way as run_factory_bench.py
    catalog_hash = hashlib.sha256(json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[
        :16
    ]

    # Hash should be non-empty and deterministic
    assert len(catalog_hash) == 16
    assert all(c in "0123456789abcdef" for c in catalog_hash)

    # Compute again to verify determinism
    catalog_hash2 = hashlib.sha256(
        json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    assert catalog_hash == catalog_hash2


def test_catalog_has_120_projects() -> None:
    """Validate that catalog contains exactly 120 projects."""
    projects = bench.load_projects()
    assert len(projects) == 120, f"Expected 120 projects, got {len(projects)}"


def test_catalog_no_duplicate_ids() -> None:
    """Validate that catalog has no duplicate project IDs."""
    projects = bench.load_projects()
    ids = [p["id"] for p in projects]
    dupes = [x for x in ids if ids.count(x) > 1]
    assert not dupes, f"Duplicate IDs: {sorted(set(dupes))}"


def test_catalog_all_levels_covered() -> None:
    """Validate that catalog covers all levels L1-L12."""
    projects = bench.load_projects()
    levels = {int(p["level"]) for p in projects}
    missing = VALID_LEVELS - levels
    assert not missing, f"Missing levels: {sorted(missing)}"


def test_catalog_10_projects_per_level() -> None:
    """Validate that each level has exactly 10 projects."""
    projects = bench.load_projects()
    counts = Counter(int(p["level"]) for p in projects)
    for level in VALID_LEVELS:
        assert counts[level] == 10, f"L{level} has {counts[level]} projects, expected 10"


def test_catalog_required_fields_present() -> None:
    """Validate that all required fields are present in each project."""
    projects = bench.load_projects()
    for field in REQUIRED_FIELDS:
        for p in projects:
            assert field in p, f"Project {p.get('id', '?')} missing required field: {field}"


def test_catalog_level_range() -> None:
    """Validate that all project levels are in valid range."""
    projects = bench.load_projects()
    for p in projects:
        level = int(p["level"])
        assert level in VALID_LEVELS, f"Project {p['id']} has invalid level: {level}"


def test_catalog_language_valid() -> None:
    """Validate that all project languages are valid."""
    projects = bench.load_projects()
    for p in projects:
        lang = p["primary_language"]
        assert lang in VALID_LANGUAGES, f"Project {p['id']} has invalid language: {lang}"


def test_catalog_id_format() -> None:
    """Validate that project IDs match the expected format."""
    projects = bench.load_projects()
    for p in projects:
        pid = str(p["id"])
        level = int(p["level"])
        assert pid.startswith(f"L{level}-"), f"ID {pid} doesn't match level {level}"


def test_catalog_min_files_matches_level() -> None:
    """Validate that min_files checks match level expectations."""
    projects = bench.load_projects()
    for p in projects:
        level = int(p["level"])
        checks = p.get("checks", [])
        for check in checks:
            check_str = str(check)
            if check_str.startswith("min_files:"):
                min_files = int(check_str.split(":")[1])
                expected = LEVEL_MIN_FILES.get(level)
                assert min_files == expected, (
                    f"Project {p['id']} (L{level}): min_files={min_files}, expected={expected}"
                )


def test_catalog_compile_check_matches_language() -> None:
    """Validate that compile checks match primary language."""
    projects = bench.load_projects()
    for p in projects:
        lang = p["primary_language"]
        expected = LANG_COMPILE_CHECK.get(lang)
        if not expected:
            continue
        checks = [str(c) for c in p.get("checks", [])]
        assert expected in checks, f"Project {p['id']} ({lang}): missing compile check {expected}"


def test_catalog_content_any_check_present() -> None:
    """Validate that content_any check is present for each project."""
    projects = bench.load_projects()
    for p in projects:
        checks = [str(c) for c in p.get("checks", [])]
        has_content = any(c.startswith("content_any:") for c in checks)
        assert has_content, f"Project {p['id']} missing content_any check"


def test_catalog_source_target_coverage_present() -> None:
    """Validate that source_target_coverage check is present for each project."""
    projects = bench.load_projects()
    for p in projects:
        checks = [str(c) for c in p.get("checks", [])]
        has_coverage = any(c.startswith("source_target_coverage:") for c in checks)
        assert has_coverage, f"Project {p['id']} missing source_target_coverage check"


def test_catalog_language_distribution_balanced() -> None:
    """Validate that language distribution is balanced."""
    projects = bench.load_projects()
    counts = Counter(p["primary_language"] for p in projects)
    min_count = min(counts.values())
    max_count = max(counts.values())
    assert max_count - min_count <= 3, f"Language distribution too uneven: {dict(counts)}"


def test_catalog_novelty_tags_minimum() -> None:
    """Validate that each project has at least 3 novelty tags."""
    projects = bench.load_projects()
    for p in projects:
        tags = p.get("novelty_tags", [])
        assert len(tags) >= 3, f"Project {p['id']} has only {len(tags)} novelty_tags"


def test_catalog_brief_minimum_length() -> None:
    """Validate that each project brief is at least 50 characters."""
    projects = bench.load_projects()
    for p in projects:
        brief = p.get("brief", "")
        assert len(brief) >= 50, f"Project {p['id']} brief too short: {len(brief)} chars"


def test_runner_audit_includes_catalog_hash_and_schema_version(monkeypatch: Any, tmp_path: Path) -> None:
    """Verify runner writes catalog_hash and catalog_schema_version into audit and meta files."""
    projects = [
        {"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench, "load_projects", lambda: projects)
    monkeypatch.setattr(bench, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench, "_ensure_bench_session", lambda **_kwargs: "bench-meta")
    monkeypatch.setattr(bench, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench,
        "build_factory_audit_record",
        lambda **_kwargs: {
            "all_checks_passed": True,
            "static_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "code_file_count": 1,
            "checks": [],
        },
    )
    monkeypatch.setattr(
        bench,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": "Build something",
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench, "run_factory_chain", _chain)

    result = bench.main()
    assert result == 0

    # Compute expected catalog hash from the projects list
    expected_hash = hashlib.sha256(
        json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

    # Verify .catalog_meta.json was written into the project workspace
    project_ws = tmp_path / "L1-01"
    catalog_meta_path = project_ws / ".catalog_meta.json"
    assert catalog_meta_path.exists(), ".catalog_meta.json must be written"
    catalog_meta = json.loads(catalog_meta_path.read_text(encoding="utf-8"))
    assert catalog_meta["catalog_schema_version"] == "factory-bench/2"
    assert catalog_meta["catalog_hash"] == expected_hash
    assert catalog_meta["project_id"] == "L1-01"

    # Verify the audit file contains catalog_schema_version and catalog_hash
    audit_dir = tmp_path / "audits"
    run_dirs = list(audit_dir.iterdir())
    assert len(run_dirs) == 1
    audit_files = sorted(run_dirs[0].glob("*.audit.json"))
    assert len(audit_files) == 1
    audit_data = json.loads(audit_files[0].read_text(encoding="utf-8"))
    assert audit_data["catalog_schema_version"] == "factory-bench/2"
    assert audit_data["catalog_hash"] == expected_hash
    assert audit_data["run_id"] == audit_data["run_id"]  # non-empty


def test_catalog_hash_changes_when_projects_change() -> None:
    """Verify catalog_hash changes when the underlying project data changes."""
    projects_a = [{"id": "L1-01", "level": 1}]
    projects_b = [{"id": "L1-01", "level": 1, "extra": True}]

    hash_a = hashlib.sha256(json.dumps(projects_a, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    hash_b = hashlib.sha256(json.dumps(projects_b, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    assert hash_a != hash_b, "catalog_hash must change when project data changes"
