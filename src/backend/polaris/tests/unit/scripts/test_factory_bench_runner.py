"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from polaris.tests.unit.scripts._factory_bench_runner_audit_helpers import (
    _guard_runner_external_io,
    _ok_run_ledger_projection,
    _record,
)
from scripts.factory_bench import run_factory_bench as bench
from scripts.factory_bench._bench_lib import (
    artifacts as bench_artifacts,
    cli as bench_cli,
    session as bench_session,
)
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
)
from scripts.factory_bench.test_bench_to_backend_integration import (
    _assert_retired_consumer,
    _consumer_batch,
    _consumer_case,
)


@pytest.fixture(autouse=True)
def _isolate_runner_io(monkeypatch: Any, tmp_path: Path) -> None:
    _guard_runner_external_io(monkeypatch, tmp_path)
    monkeypatch.setenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", "observed")
    monkeypatch.setattr(bench_cli, "persist_real_run_gate_ledger", lambda *_args, **_kwargs: {"ok": True})
    bench.configure_bench_backend("", "", "")


def test_chain_failure_overrides_static_artifact_checks() -> None:
    record = _record(
        chain_state="fail",
        chain_results={"qa_ran": False, "qa_passed": False},
        run_ledger_projection={
            **_ok_run_ledger_projection(),
            "gates": [
                {
                    "name": "qa_verdict",
                    "stage": "qa",
                    "ok": False,
                    "capability_ok": True,
                    "content_id": "qa-failed-content",
                    "append_id": "qa-failed-append",
                }
            ],
        },
    )

    apply_factory_bench_gates(record, chain={"exit_code": 1})

    assert record["static_checks_passed"] is True
    assert record["all_checks_passed"] is False
    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["canonical_execution"]["ok"] is False
    assert record["canonical_projection"]["execution"]["ok"] is False
    assert record["canonical_projection"]["execution"]["reason_code"] == "qa_verdict_failed"


def test_runner_audits_llm_routes_for_llm_backed_roles_only(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Fixed-route preflight retired; direct request preserves actual-role projection without invented PM/CE invocation."""
    inputs, report, http, _ = _consumer_case(tmp_path)
    assert bench.FACTORY_BENCH_REQUIRED_LLM_ROLES == ("pm", "chief_engineer", "director", "qa")
    request = json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8"))
    assert request["role"] == "director" and request["workflow"] == "direct"
    assert report["observation"]["platform"]["active_roles"] == ["director"]
    assert all("/v2/factory/" not in path and "llm/status" not in path for _, path, _ in http.calls)


def test_missing_qa_verdict_and_wrong_product_are_fail_closed() -> None:
    record = _record(
        has_qa_verdict=False,
        wrong_product_suspect=True,
    )

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert record["all_checks_passed"] is False
    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert "qa_verdict_artifact_present" not in gates
    assert record["canonical_projection"]["qa"]["name"] == "qa_verdict"
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
    monkeypatch.setattr(bench_artifacts, "_RUNTIME_PROJECT_BASES", (runtime_base_a, runtime_base_b))

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
    monkeypatch.setattr(bench_artifacts, "_RUNTIME_PROJECT_BASES", (runtime_base,))

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


# --- map_factory_run_to_chain_results ---


def test_map_completed_qa_artifact_is_non_authoritative() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {
        "gates": [{"gate_name": "quality_gate", "passed": True, "message": "all good"}],
        "summary_json": {"director": {"total": 10, "successes": 8, "failures": 1, "blocked": 1}},
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["source"] == "legacy_artifact"
    assert result["authoritative"] is False
    assert result["degraded"] is True
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["director"] == {"total": 10, "successes": 8, "failures": 1, "blocked": 1}


def test_map_failed_qa_artifact_cannot_set_exit_class() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle = {
        "gates": [{"gate_name": "quality_gate", "passed": False, "message": "lint errors"}],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["qa_reason"] == ""


def test_map_runtime_phase_cannot_authorize_qa() -> None:
    run_status = {"status": "failed", "phase": "qa_gate"}
    audit_bundle = {
        "gates": [{"gate_name": "quality_gate", "passed": False, "message": "tests failed"}],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None


def test_map_director_summary_is_display_only() -> None:
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "summary_json": {"director": {"total": 5, "successes": 2, "failures": 3, "blocked": 0}},
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["director"] == {"total": 5, "successes": 2, "failures": 3, "blocked": 0}


def test_map_does_not_fall_back_to_run_status_gates() -> None:
    run_status: dict[str, Any] = {
        "status": "completed",
        "phase": "",
        "gates": [{"gate_name": "quality_gate", "passed": True, "message": "ok"}],
    }
    audit_bundle: dict[str, Any] = {}
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["qa_reason"] == ""


def test_map_does_not_scan_events_tail_for_director() -> None:
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "events_tail": [
            {"stage": "other", "result": {"total": 99}},
            {"stage": "director_dispatch", "result": {"total": 7, "successes": 3, "failures": 4}},
        ],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["director"] == {"total": None, "successes": None, "failures": None, "blocked": None}


def test_map_does_not_parse_summary_json_string() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {
        "gates": [{"gate_name": "quality_gate", "passed": True}],
        "summary_json": '{"director": {"total": 3, "successes": 3}}',
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["director"] == {"total": None, "successes": None, "failures": None, "blocked": None}


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
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
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

    monkeypatch.setattr(bench_session, "_push_bench_session_to_backend", _fake_push)

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

    monkeypatch.setattr(bench_session, "_push_bench_session_to_backend", _fake_push)

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


def test_run_chain_preserves_serial_director_workflow_by_default(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Subprocess serial protocol retired; default single Director formal request preserved."""
    _assert_retired_consumer(tmp_path, writer="run_chain")
    inputs, _, _, _ = _consumer_case(tmp_path / "formal")
    request = json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8"))
    assert request["workflow"] == "direct" and request["role"] == "director"


def test_run_chain_can_enable_parallel_director_workflow(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Legacy parallel subprocess writer rejects before target or log effects."""
    _assert_retired_consumer(tmp_path, writer="run_chain", director_workflow_execution_mode="parallel")


def test_run_chain_task_market_driver_plans_then_dispatches_market(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Task Market dispatch no longer authorizes new engineering writes."""
    _assert_retired_consumer(tmp_path, writer="run_chain", director_dispatch_driver="task_market")


def test_main_task_market_driver_uses_http_factory_chain_without_legacy_fallback(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Migrated consumer: Retired Task Market option fails before effects instead of falling back to Factory."""
    base = tmp_path / "batch"
    assert bench_cli.main(["--director-dispatch-driver", "task_market", "--work-dir", str(base)]) == 2
    assert not base.exists()


def test_main_marks_backend_session_failed_when_run_aborts(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Partial launch stays external failure/unknown cleanup; no old bench-session product write."""
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, fail_start=True)
    record = summary["records"][0]
    assert code != 0 and record["launch"]["harness_status"] == "UNKNOWN"
    assert record["resources_closed"] is False and record["catalog_quality"] == "UNKNOWN"
    assert traces[0][1].calls == []
    assert not Path(record["workspace"]).exists()


def test_main_rejects_unknown_explicit_project_ids(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Keep rejection of partial explicit catalog selection through current real CLI."""
    base = tmp_path / "batch"
    assert bench_cli.main(["--dry-run", "--project-ids", "L1-01,NO-SUCH", "--work-dir", str(base)]) == 2
    assert not base.exists()


def test_main_defaults_to_l1_through_l12_catalog(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Preserve all120/L1-L12 selection; dry-run cannot become delivery evidence or allocate targets."""
    base = tmp_path / "batch"
    assert bench_cli.main(["--dry-run", "--work-dir", str(base)]) == 0
    summary = json.loads(next((base / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    assert summary["catalog_total"] == summary["selected_count"] == len(summary["records"]) == 120
    assert {r["original_project"]["level"] for r in summary["records"]} == set(range(1, 13))
    assert all(r["harness_status"] == "NOT_RUN" for r in summary["records"])
    assert not (base / "workspaces").exists()


def test_main_default_max_failed_zero_does_not_early_stop(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Preserve no default early stop after closed rejection; unknown resource fence remains separate."""
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, states=("rejected", "unknown"))
    assert code == 1 and len(traces) == len(summary["records"]) == 2
    assert [r["project_id"] for r in summary["records"]] == ["L1-01", "L1-02"]
    assert all(r["resources_closed"] for r in summary["records"])
    assert summary["records"][0]["observation"]["platform"]["verification"] == "failed"


# --- run_factory_chain (API path) ---


def test_run_factory_chain_success(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Actual shared adapter observes current acceptance, preserves goal/checks and cleanup without Factory verdict."""
    inputs, report, http, supervisor = _consumer_case(tmp_path, state="accepted", workflow="collaborative")
    request = json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8"))
    assert request["instruction"].endswith(inputs.requirements_file.read_text(encoding="utf-8"))
    assert request["workflow"] == "collaborative" and request["role"] == "director"
    assert request["required_checks"] == []
    assert report["observation"]["harness_status"] == "OBSERVED_ACCEPTED" and report["exit_code"] == 0
    assert report["cleanup"]["harness_status"] == "STOPPED"
    assert report["delivery_proven"] is False and report["catalog_quality"] == "UNKNOWN"
    assert [p for m, p, _ in http.calls if m == "POST"] == ["/v2/engineering/runs"]
    assert supervisor.stops == [inputs.instance_id]


def test_run_factory_chain_start_failure(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Actual adapter persists failed launch before HTTP/target operation; no implicit retry."""
    inputs, report, http, _ = _consumer_case(tmp_path, fail_start=True)
    assert report["launch"]["harness_status"] == "UNKNOWN"
    assert report["exit_code"] != 0 and report["resources_closed"] is False
    assert http.calls == [] and not inputs.workspace.exists()
    assert json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))["phase"] == "instance_unknown"


def test_run_factory_chain_event_wait_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Keep bounded wait, exactly owned cancellation and confirmed cleanup through formal API."""
    inputs, report, http, _ = _consumer_case(tmp_path, state="running")
    assert report["observation"]["harness_status"] == "TIMEOUT" and report["exit_code"] == 124
    assert report["final_observation"]["platform"]["status"] == "cancelled"
    assert report["cleanup"]["harness_status"] == "STOPPED"
    posts = [p for m, p, _ in http.calls if m == "POST"]
    assert posts == ["/v2/engineering/runs", "/v2/engineering/runs/consumer-l1-01/cancel"]
    assert not inputs.workspace.exists()


def test_run_factory_chain_failed_status(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Current failed evidence/QA stay failure, not missing; no silent repair or retry."""
    _, report, http, _ = _consumer_case(tmp_path, state="rejected")
    assert report["observation"]["harness_status"] == "REJECTED" and report["exit_code"] == 1
    platform = report["observation"]["platform"]
    assert platform["qa_verdict"] == "rejected" and platform["verification"] == "failed"
    assert platform["checks"][0]["exit_code"] == 1
    assert report["cleanup"]["harness_status"] == "STOPPED"
    assert len([c for c in http.calls if c[0] == "POST"]) == 1


def test_run_factory_chain_on_stage_change_callback(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Scoped persisted snapshots replace stage callback; completion never implies acceptance."""
    inputs, report, _, _ = _consumer_case(tmp_path, stages=True, workflow="collaborative")
    snapshots = [json.loads(p.read_text(encoding="utf-8")) for p in (inputs.evidence_dir / "snapshots").glob("*.json")]
    assert any(s["active_roles"] == ["chief_engineer"] and s["status"] == "running" for s in snapshots)
    assert any(s["active_roles"] == [] and s["status"] == "completed" for s in snapshots)
    assert report["observation"]["platform"]["last_seq"] == 2
    assert report["observation"]["platform"]["project_outcome"] == "not_accepted"


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
    """Migrated consumer: One batch identity preserves separate per-workspace run and request identities."""
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, states=("accepted", "accepted"))
    assert code == 0 and len(summary["records"]) == 2
    assert len({r["workspace"] for r in summary["records"]}) == 2
    requests = [
        json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8")) for inputs, _, _ in traces
    ]
    assert len({r["request_key"] for r in requests}) == 2
    assert len({r["observation"]["platform"]["run_id"] for r in summary["records"]}) == 2
    assert all(summary["batch_id"] in str(inputs.evidence_dir) for inputs, _, _ in traces)


def test_main_audit_path_points_to_conflict_when_same_id_reused(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Repeated case keeps prior audit bytes immutable through separate fresh batch reports."""
    base = tmp_path / "batch"
    args = ["--dry-run", "--case", "L1-01", "--work-dir", str(base)]
    assert bench_cli.main(args) == 0
    first = next((base / "reports").glob("*/summary.json"))
    original = first.read_bytes()
    assert bench_cli.main(args) == 0
    paths = list((base / "reports").glob("*/summary.json"))
    assert len(paths) == 2 and first.read_bytes() == original
    assert len({json.loads(p.read_text(encoding="utf-8"))["batch_id"] for p in paths}) == 2
    assert not (base / "workspaces").exists()


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
        bench_session,
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
        bench_cli,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url(explicit="http://192.168.1.1:8080")
    assert result == "http://192.168.1.1:8080"


def test_resolve_backend_url_env_overrides_desktop(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNELONE_BACKEND_URL", "http://env-host:1111")
    monkeypatch.setattr(
        bench_cli,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url()
    assert result == "http://env-host:1111"


def test_resolve_backend_url_missing_desktop_json_returns_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_url()
    assert result == "http://127.0.0.1:49977"


def test_resolve_backend_url_malformed_desktop_json_returns_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
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
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token()
    assert result == "desktop-tok-abc"


def test_resolve_backend_token_explicit_overrides_desktop(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        bench_cli,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token(explicit="explicit-tok")
    assert result == "explicit-tok"


def test_resolve_backend_token_env_overrides_desktop(monkeypatch: Any) -> None:
    monkeypatch.setenv("FACTORY_BENCH_BACKEND_TOKEN", "env-tok")
    monkeypatch.setattr(
        bench_cli,
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
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == "polaris-local-dev"


def test_resolve_backend_token_malformed_desktop_json_returns_local_dev_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == "polaris-local-dev"


def test_resolve_backend_token_missing_remote_token_returns_empty(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.setenv("KERNELONE_BACKEND_URL", "http://10.0.0.1:49977")
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
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
    assert "目录拓扑由 Chief Engineer 决定" in doc
    assert "不规定唯一目录范式" in doc
    assert "写成唯一合法结构" in doc
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
    assert "必须覆盖 CE 蓝图声明的业务源码与入口" in doc
    assert "不能只包含配置/脚手架" in doc


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
    assert "目录拓扑由 Chief Engineer 决定" in doc
    assert "不规定唯一目录范式" in doc
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
    # Tests remain mandatory; only CE may choose their directory topology.
    assert "至少一个可执行测试/检查文件" in doc
    assert "不能只包含配置/脚手架" in doc
    assert "目录拓扑由 Chief Engineer 决定" in doc


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
    """Migrated consumer: Read failure preserves unknown/timeout; no artifact fallback may grant authority."""
    inputs, report, http, _ = _consumer_case(tmp_path, state="observation_error")
    assert report["observation"]["harness_status"] == "TIMEOUT"
    assert report["final_observation"]["platform"]["status"] == "cancelled"
    assert report["catalog_quality"] == "UNKNOWN" and report["exit_code"] != 0
    assert all("audit-bundle" not in path and "/v2/factory/" not in path for _, path, _ in http.calls)
    assert not inputs.workspace.exists()
