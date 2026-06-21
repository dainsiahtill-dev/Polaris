"""Tests for audit diagnostics — stable regression signals from factory-bench audit artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.audit_diagnostics import (
    DIAGNOSTIC_SCHEMA_VERSION,
    diagnose_from_paths,
    diagnose_project,
    diagnose_run,
    extract_director_convergence_diagnostics,
    extract_director_route_diagnostics,
    extract_failure_mode,
    extract_failure_taxonomy,
    extract_git_command_audit,
    extract_qa_diagnostics,
    extract_real_run_diagnostics,
    extract_stage_failure,
    is_git_safe_command,
    load_factory_audits_json,
    load_per_project_audits,
)


def _r3_sample_record(**overrides: Any) -> dict[str, Any]:
    """Minimal R3-style audit record with all diagnostic fields populated."""
    base: dict[str, Any] = {
        "schema_version": "factory-audit/1",
        "project_id": "L1-01",
        "level": 1,
        "domain": "creative",
        "title": "Glowing Insect Garden Simulator",
        "code_file_count": 5,
        "code_files": ["index.html", "style.css", "main.js", "game.js", "utils.js"],
        "doc_files": ["README.md"],
        "artifacts": {"plan": ["rt:contracts/plan.md"], "blueprint": [], "verdict": []},
        "has_plan_doc": True,
        "has_blueprint_doc": False,
        "has_qa_verdict": False,
        "checks": [
            {"check": "html", "ok": True, "detail": "html page present: index.html"},
            {"check": "js_syntax", "ok": True, "detail": "3 js files pass node --check"},
            {"check": "min_files:3", "ok": True, "detail": "5 code files (need >= 3)"},
        ],
        "all_checks_passed": False,
        "chain_state": "partial",
        "chain_results": {
            "qa_ran": False,
            "qa_passed": False,
            "qa_reason": "director failures present",
            "director": {"total": 3, "successes": 1, "failures": 2, "blocked": 0},
            "exit_class": "director_partial",
        },
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: entrypoint_smoke",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "5 generated code file(s)"},
                "environment_prepared": {"ok": True, "detail": "npm available; no dependency install required"},
                "build_test_lint_ran": {"ok": True, "detail": "node --check passed"},
                "entrypoint_smoke": {
                    "ok": False,
                    "detail": "npm run start timed out or failed",
                    "kind": "npm_start",
                },
            },
            "commands": [
                {"command": ["node", "--check", "main.js"], "ok": True, "returncode": 0, "phase": "build_test_lint"},
                {
                    "command": ["npm", "run", "start"],
                    "ok": False,
                    "returncode": 1,
                    "timeout": False,
                    "stderr_tail": "Error: listen EADDRINUSE: address already in use :::3000",
                    "stdout_tail": "",
                    "phase": "entrypoint",
                    "script": "start",
                },
            ],
            "entrypoint": {
                "ok": False,
                "kind": "npm_start",
                "detail": "npm run start timed out or failed",
            },
        },
        "llm_route_audit": {
            "ok": False,
            "roles": {
                "pm": {
                    "ok": True,
                    "configured": [{"role": "pm", "provider_id": "kimi", "model": "kimi-latest"}],
                    "observed_count": 2,
                    "observed_bindings": ["kimi|kimi-latest"],
                    "missing_bindings": [],
                    "family_ok": True,
                    "multi_route_ok": True,
                },
                "director": {
                    "ok": False,
                    "configured": [
                        {"role": "director", "provider_id": "qwen", "model": "qwen3.6-27b"},
                        {"role": "director", "provider_id": "qwen", "model": "qwen3.6-7b"},
                    ],
                    "observed_count": 1,
                    "observed_bindings": ["qwen|qwen3.6-27b"],
                    "missing_bindings": ["qwen|qwen3.6-7b"],
                    "family_ok": True,
                    "multi_route_ok": False,
                },
                "chief_engineer": {
                    "ok": True,
                    "configured": [{"role": "chief_engineer", "provider_id": "kimi", "model": "kimi-latest"}],
                    "observed_count": 1,
                    "observed_bindings": ["kimi|kimi-latest"],
                    "missing_bindings": [],
                    "family_ok": True,
                    "multi_route_ok": True,
                },
                "qa": {
                    "ok": True,
                    "configured": [{"role": "qa", "provider_id": "minimax", "model": "minimax-latest"}],
                    "observed_count": 1,
                    "observed_bindings": ["minimax|minimax-latest"],
                    "missing_bindings": [],
                    "family_ok": True,
                    "multi_route_ok": True,
                },
            },
            "events_observed": 8,
            "events_rejected": 1,
            "terminal_events_observed": 4,
            "summary": "LLM route audit failed: director",
        },
        "failure_taxonomy": {
            "ok": False,
            "category": "director_tool_execution",
            "root_cause_signature": "director_tool_execution:real_run_gate.entrypoint_smoke",
            "reasons": [
                "gate:real_run_gate=real run gate failed: entrypoint_smoke",
                "gate:integration_qa_passed=qa_ran=False qa_passed=False",
            ],
            "evidence": ["real run gate failed: entrypoint_smoke"],
        },
        "wrong_product_suspect": False,
        "brief_goal_overlap": 0.42,
    }
    base.update(overrides)
    return base


# --- extract_director_route_diagnostics ---


def test_director_route_extracts_configured_observed_missing() -> None:
    record = _r3_sample_record()
    diag = extract_director_route_diagnostics(record)

    assert diag["has_audit"] is True
    assert diag["ok"] is False
    assert "director" in diag["roles"]
    director = diag["roles"]["director"]
    assert director["ok"] is False
    assert director["configured_count"] == 2
    assert director["observed_count"] == 1
    assert "qwen|qwen3.6-7b" in director["missing_bindings"]
    assert director["multi_route_ok"] is False


def test_director_route_all_roles_present() -> None:
    record = _r3_sample_record()
    diag = extract_director_route_diagnostics(record)

    assert set(diag["roles"].keys()) == {"pm", "director", "chief_engineer", "qa"}


def test_director_route_missing_audit_returns_fail_closed() -> None:
    record: dict[str, Any] = {"llm_route_audit": None}
    diag = extract_director_route_diagnostics(record)

    assert diag["has_audit"] is False
    assert diag["ok"] is False


def test_director_route_empty_record_returns_fail_closed() -> None:
    diag = extract_director_route_diagnostics({})

    assert diag["has_audit"] is False
    assert diag["ok"] is False


def test_director_route_passing_all_roles() -> None:
    record = _r3_sample_record(
        llm_route_audit={
            "ok": True,
            "roles": {
                "director": {
                    "ok": True,
                    "configured": [{"role": "director", "provider_id": "qwen", "model": "qwen3.6-27b"}],
                    "observed_count": 1,
                    "observed_bindings": ["qwen|qwen3.6-27b"],
                    "missing_bindings": [],
                    "family_ok": True,
                    "multi_route_ok": True,
                },
            },
            "events_observed": 4,
            "events_rejected": 0,
            "terminal_events_observed": 4,
            "summary": "LLM route audit passed",
        }
    )
    diag = extract_director_route_diagnostics(record)

    assert diag["ok"] is True
    assert diag["roles"]["director"]["ok"] is True


# --- extract_real_run_diagnostics ---


def test_real_run_extracts_failed_command_tail() -> None:
    record = _r3_sample_record()
    diag = extract_real_run_diagnostics(record)

    assert diag["has_gate"] is True
    assert diag["ok"] is False
    assert "entrypoint_smoke" in diag["failing_requirements"]
    assert len(diag["failed_commands"]) == 1
    failed = diag["failed_commands"][0]
    assert failed["command"] == ["npm", "run", "start"]
    assert "EADDRINUSE" in failed["stderr_tail"]
    assert failed["phase"] == "entrypoint"


def test_real_run_missing_gate_returns_fail_closed() -> None:
    diag = extract_real_run_diagnostics({})

    assert diag["has_gate"] is False
    assert diag["ok"] is False
    assert diag["failed_commands"] == []


def test_real_run_passing_gate() -> None:
    record = _r3_sample_record(
        real_run_gate={
            "ok": True,
            "summary": "real run gate passed",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "5 files"},
                "entrypoint_smoke": {"ok": True, "detail": "passed"},
            },
            "commands": [],
            "entrypoint": {"ok": True, "kind": "web_static", "detail": "passed"},
        }
    )
    diag = extract_real_run_diagnostics(record)

    assert diag["ok"] is True
    assert diag["failing_requirements"] == []
    assert diag["failed_commands"] == []


def test_real_run_timeout_command_captured() -> None:
    record = _r3_sample_record(
        real_run_gate={
            "ok": False,
            "summary": "real run gate failed: entrypoint_smoke",
            "requirements": {
                "entrypoint_smoke": {"ok": False, "detail": "timed out"},
            },
            "commands": [
                {
                    "command": ["python", "app.py"],
                    "ok": False,
                    "returncode": None,
                    "timeout": True,
                    "stderr_tail": "",
                    "stdout_tail": "Starting server...",
                    "phase": "entrypoint",
                },
            ],
            "entrypoint": {"ok": False, "kind": "python_cli", "detail": "timed out"},
        }
    )
    diag = extract_real_run_diagnostics(record)

    assert diag["ok"] is False
    assert len(diag["failed_commands"]) == 1
    assert diag["failed_commands"][0]["timeout"] is True
    assert diag["failed_commands"][0]["returncode"] is None


# --- extract_stage_failure ---


def test_stage_failure_extracts_gate_and_check_failures() -> None:
    record = _r3_sample_record(
        factory_gates=[
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: entrypoint_smoke"},
        ],
    )
    diag = extract_stage_failure(record)

    assert diag["chain_state"] == "partial"
    assert diag["chain_exit_class"] == "director_partial"
    assert diag["qa_ran"] is False
    assert diag["qa_passed"] is False
    assert diag["director_failures"] == 2
    assert diag["director_blocked"] == 0
    assert len(diag["gate_failures"]) == 3
    gate_names = [g["gate"] for g in diag["gate_failures"]]
    assert "integration_qa_passed" in gate_names
    assert "chain_clean" in gate_names
    assert "real_run_gate" in gate_names


def test_stage_failure_clean_chain() -> None:
    record = _r3_sample_record(
        chain_state="clean",
        chain_results={
            "qa_ran": True,
            "qa_passed": True,
            "qa_reason": "ok",
            "exit_class": "clean",
            "director": {"total": 3, "successes": 3, "failures": 0, "blocked": 0},
        },
        factory_gates=[
            {"gate": "chain_clean", "ok": True, "detail": "clean"},
            {"gate": "integration_qa_passed", "ok": True, "detail": "ok"},
        ],
    )
    diag = extract_stage_failure(record)

    assert diag["chain_state"] == "clean"
    assert diag["qa_passed"] is True
    assert diag["gate_failures"] == []


def test_stage_failure_empty_record() -> None:
    diag = extract_stage_failure({})

    assert diag["chain_state"] == ""
    assert diag["qa_ran"] is None
    assert diag["gate_failures"] == []
    assert diag["check_failures"] == []


# --- extract_qa_diagnostics ---


def test_qa_extracts_artifact_presence() -> None:
    record = _r3_sample_record()
    diag = extract_qa_diagnostics(record)

    assert diag["has_plan_doc"] is True
    assert diag["has_blueprint_doc"] is False
    assert diag["has_qa_verdict"] is False
    assert diag["wrong_product_suspect"] is False
    assert diag["brief_goal_overlap"] == 0.42


def test_qa_wrong_product_flag() -> None:
    record = _r3_sample_record(
        wrong_product_suspect=True,
        wrong_product_match="L2-07 calculator",
    )
    diag = extract_qa_diagnostics(record)

    assert diag["wrong_product_suspect"] is True
    assert "calculator" in diag["wrong_product_match"]


def test_qa_empty_record() -> None:
    diag = extract_qa_diagnostics({})

    assert diag["has_plan_doc"] is False
    assert diag["wrong_product_suspect"] is False
    assert diag["qa_blocked"] is False
    assert diag["qa_blocked_stage"] == ""
    assert diag["qa_failure_reason"] == ""
    assert diag["qa_artifact_path"] == ""


def test_qa_blocked_from_chain_results() -> None:
    """extract_qa_diagnostics must surface qa_blocked from chain_results."""
    record = {
        "has_qa_verdict": True,
        "chain_results": {
            "qa_blocked": True,
            "qa_blocked_stage": "director",
            "qa_failure_reason": "Director status: failed",
        },
        "qa_artifact_path": "runtime/results/integration_qa.result.json",
    }
    diag = extract_qa_diagnostics(record)

    assert diag["qa_blocked"] is True
    assert diag["qa_blocked_stage"] == "director"
    assert diag["qa_failure_reason"] == "Director status: failed"
    assert diag["qa_artifact_path"] == "runtime/results/integration_qa.result.json"
    assert diag["has_qa_verdict"] is True


def test_qa_blocked_from_record_level() -> None:
    """extract_qa_diagnostics must also read qa_blocked from record level."""
    record = {
        "has_qa_verdict": True,
        "qa_blocked": True,
        "qa_blocked_stage": "director",
        "qa_failure_reason": "Director status: timeout",
    }
    diag = extract_qa_diagnostics(record)

    assert diag["qa_blocked"] is True
    assert diag["qa_blocked_stage"] == "director"
    assert diag["qa_failure_reason"] == "Director status: timeout"


def test_stage_failure_includes_qa_blocked() -> None:
    """extract_stage_failure must include qa_blocked fields."""
    record = {
        "chain_results": {
            "qa_ran": False,
            "qa_blocked": True,
            "qa_blocked_stage": "director",
            "qa_failure_reason": "Director status: timeout",
        },
    }
    diag = extract_stage_failure(record)

    assert diag["qa_blocked"] is True
    assert diag["qa_blocked_stage"] == "director"
    assert diag["qa_failure_reason"] == "Director status: timeout"


# --- extract_failure_taxonomy ---


def test_taxonomy_extracts_category_and_signature() -> None:
    record = _r3_sample_record()
    diag = extract_failure_taxonomy(record)

    assert diag["has_taxonomy"] is True
    assert diag["ok"] is False
    assert diag["category"] == "director_tool_execution"
    assert "director_tool_execution" in diag["root_cause_signature"]
    assert len(diag["reasons"]) >= 1


def test_taxonomy_passing_record() -> None:
    record = _r3_sample_record(
        all_checks_passed=True,
        failure_taxonomy={
            "ok": True,
            "category": "",
            "root_cause_signature": "pass",
            "reasons": [],
            "evidence": [],
        },
    )
    diag = extract_failure_taxonomy(record)

    assert diag["ok"] is True
    assert diag["category"] == ""
    assert diag["root_cause_signature"] == "pass"


def test_taxonomy_missing_returns_unclassified_for_failed() -> None:
    record: dict[str, Any] = {"all_checks_passed": False}
    diag = extract_failure_taxonomy(record)

    assert diag["has_taxonomy"] is False
    assert diag["ok"] is False
    assert diag["root_cause_signature"] == "unclassified"


def test_taxonomy_missing_returns_pass_for_passed() -> None:
    record: dict[str, Any] = {"all_checks_passed": True}
    diag = extract_failure_taxonomy(record)

    assert diag["has_taxonomy"] is False
    assert diag["ok"] is True
    assert diag["root_cause_signature"] == "pass"


# --- diagnose_project ---


def test_diagnose_project_full_record() -> None:
    record = _r3_sample_record()
    diag = diagnose_project(record)

    assert diag["schema_version"] == DIAGNOSTIC_SCHEMA_VERSION
    assert diag["project_id"] == "L1-01"
    assert diag["level"] == 1
    assert diag["all_checks_passed"] is False
    assert diag["code_file_count"] == 5
    assert diag["director_route"]["ok"] is False
    assert diag["real_run"]["ok"] is False
    assert diag["stage_failure"]["chain_state"] == "partial"
    assert diag["qa"]["has_plan_doc"] is True
    assert diag["failure_taxonomy"]["category"] == "director_tool_execution"


def test_diagnose_project_minimal_record() -> None:
    record: dict[str, Any] = {
        "project_id": "L5-30",
        "level": 5,
        "all_checks_passed": True,
        "code_file_count": 10,
    }
    diag = diagnose_project(record)

    assert diag["project_id"] == "L5-30"
    assert diag["all_checks_passed"] is True
    assert diag["director_route"]["has_audit"] is False
    assert diag["real_run"]["has_gate"] is False


# --- diagnose_run ---


def test_diagnose_run_aggregates_categories() -> None:
    records = [
        _r3_sample_record(project_id="L1-01"),
        _r3_sample_record(
            project_id="L2-07",
            all_checks_passed=True,
            failure_taxonomy={
                "ok": True,
                "category": "",
                "root_cause_signature": "pass",
                "reasons": [],
                "evidence": [],
            },
        ),
    ]
    factory_audits: dict[str, Any] = {"total": 2, "all_checks_passed": 1}
    report = diagnose_run(factory_audits, records)

    assert report["run_summary"]["total"] == 2
    assert report["run_summary"]["passed"] == 1
    assert report["run_summary"]["failed"] == 1
    assert len(report["projects"]) == 2
    assert "director_tool_execution" in report["failure_categories"]
    assert report["director_route_failures"] == 2


def test_diagnose_run_empty() -> None:
    report = diagnose_run({}, [])

    assert report["run_summary"]["total"] == 0
    assert report["run_summary"]["passed"] == 0
    assert report["projects"] == []


# --- load_per_project_audits ---


def test_load_per_project_audits_from_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "audits" / "run-abc"
    run_dir.mkdir(parents=True)
    (run_dir / "L1-01.audit.json").write_text(
        json.dumps({"project_id": "L1-01", "all_checks_passed": True}),
        encoding="utf-8",
    )
    (run_dir / "L2-07.audit.json").write_text(
        json.dumps({"project_id": "L2-07", "all_checks_passed": False}),
        encoding="utf-8",
    )

    records = load_per_project_audits(run_dir)

    assert len(records) == 2
    assert records[0]["project_id"] == "L1-01"
    assert records[1]["project_id"] == "L2-07"


def test_load_per_project_audits_missing_dir(tmp_path: Path) -> None:
    records = load_per_project_audits(tmp_path / "nonexistent")

    assert records == []


def test_load_per_project_audits_skips_malformed_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "audits" / "run-bad"
    run_dir.mkdir(parents=True)
    (run_dir / "good.audit.json").write_text(
        json.dumps({"project_id": "good"}),
        encoding="utf-8",
    )
    (run_dir / "bad.audit.json").write_text("NOT JSON{{{", encoding="utf-8")

    records = load_per_project_audits(run_dir)

    assert len(records) == 1
    assert records[0]["project_id"] == "good"


# --- load_factory_audits_json ---


def test_load_factory_audits_json(tmp_path: Path) -> None:
    path = tmp_path / "factory_audits.json"
    path.write_text(
        json.dumps({"total": 10, "all_checks_passed": 7}),
        encoding="utf-8",
    )

    data = load_factory_audits_json(path)

    assert data["total"] == 10
    assert data["all_checks_passed"] == 7


def test_load_factory_audits_json_missing_file(tmp_path: Path) -> None:
    data = load_factory_audits_json(tmp_path / "nonexistent.json")

    assert data == {}


# --- diagnose_from_paths ---


def test_diagnose_from_paths_full(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audits" / "run-123"
    audit_dir.mkdir(parents=True)
    (audit_dir / "L1-01.audit.json").write_text(
        json.dumps(_r3_sample_record()),
        encoding="utf-8",
    )
    factory_path = tmp_path / "factory_audits.json"
    factory_path.write_text(
        json.dumps({"total": 1, "all_checks_passed": 0}),
        encoding="utf-8",
    )

    report = diagnose_from_paths(factory_path, audit_dir)

    assert report["schema_version"] == DIAGNOSTIC_SCHEMA_VERSION
    assert report["run_summary"]["total"] == 1
    assert len(report["projects"]) == 1
    assert report["projects"][0]["project_id"] == "L1-01"


def test_diagnose_from_paths_missing_both(tmp_path: Path) -> None:
    report = diagnose_from_paths(None, None)

    assert report["run_summary"]["total"] == 0
    assert report["projects"] == []


def test_diagnose_from_paths_nonexistent_paths(tmp_path: Path) -> None:
    report = diagnose_from_paths(
        tmp_path / "no_such_file.json",
        tmp_path / "no_such_dir",
    )

    assert report["run_summary"]["total"] == 0


# --- extract_git_command_audit ---


def test_git_command_audit_detects_git_stash() -> None:
    record = {"command": "git stash push -m 'saving changes'"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert "git stash" in diag["findings"][0]["command"]
    assert diag["findings"][0]["category"] == "stash_manipulation"


def test_git_command_audit_detects_git_stash_pop() -> None:
    record = {"command": "git stash pop"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert "git stash" in diag["findings"][0]["command"]


def test_git_command_audit_detects_git_stash_apply() -> None:
    record = {"command": "git stash apply stash@{0}"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1


def test_git_command_audit_detects_git_stash_drop() -> None:
    record = {"command": "git stash drop stash@{0}"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1


def test_git_command_audit_detects_git_stash_clear() -> None:
    record = {"command": "git stash clear"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1


def test_git_command_audit_detects_git_reset() -> None:
    record = {"command": "git reset --hard HEAD~1"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert "git reset" in diag["findings"][0]["command"]
    assert diag["findings"][0]["category"] == "history_rewrite"


def test_git_command_audit_detects_git_checkout() -> None:
    record = {"command": "git checkout main"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert "git checkout" in diag["findings"][0]["command"]
    assert diag["findings"][0]["category"] == "branch_switch"


def test_git_command_audit_detects_git_restore() -> None:
    record = {"command": "git restore --staged ."}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert "git restore" in diag["findings"][0]["command"]
    assert diag["findings"][0]["category"] == "file_discard"


def test_git_command_audit_detects_git_clean() -> None:
    record = {"command": "git clean -fd"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert "git clean" in diag["findings"][0]["command"]
    assert diag["findings"][0]["category"] == "file_discard"


def test_git_command_audit_detects_git_switch() -> None:
    record = {"command": "git switch main"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert "git switch" in diag["findings"][0]["command"]
    assert diag["findings"][0]["category"] == "branch_switch"


def test_git_command_audit_detects_git_branch_delete() -> None:
    record = {"command": "git branch -D feature-branch"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert diag["findings"][0]["category"] == "branch_delete"


def test_git_command_audit_detects_git_branch_lowercase_d() -> None:
    record = {"command": "git branch -d old-branch"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1


def test_git_command_audit_detects_git_worktree_remove() -> None:
    record = {"command": "git worktree remove ../worktree-path"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert diag["findings"][0]["category"] == "worktree_mutation"


def test_git_command_audit_detects_git_worktree_prune() -> None:
    record = {"command": "git worktree prune"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1


def test_git_command_audit_detects_git_rebase_abort() -> None:
    record = {"command": "git rebase --abort"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert diag["findings"][0]["category"] == "rebase_abort"


def test_git_command_audit_detects_git_push_force() -> None:
    record = {"command": "git push --force origin main"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert diag["findings"][0]["category"] == "force_push"


def test_git_command_audit_detects_git_commit_amend() -> None:
    record = {"command": "git commit --amend -m 'fix message'"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert diag["findings"][0]["severity"] == "P0"
    assert diag["findings"][0]["category"] == "history_rewrite"


def test_git_command_audit_detects_rm_rf_git() -> None:
    record = {"command": "rm -rf .git"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] >= 1
    assert any(f["category"] == "repo_destruction" for f in diag["findings"])


def test_git_command_audit_no_findings_for_safe_commands() -> None:
    for cmd in [
        "git status",
        "git diff",
        "git diff --staged",
        "git log --oneline",
        "git show HEAD",
        "git branch",
        "git branch -a",
        "git tag",
        "git remote -v",
        "git describe --tags",
        "git rev-parse HEAD",
        "git ls-files",
        "git stash list",
        "git stash show",
        "git blame file.py",
        "git config --get user.name",
        "git symbolic-ref HEAD",
        "git merge-base HEAD main",
    ]:
        record = {"command": cmd}
        diag = extract_git_command_audit(record)
        assert diag["has_findings"] is False, f"Expected safe but got findings for: {cmd}"
        assert diag["finding_count"] == 0
        assert diag["severity"] == "none"


def test_git_command_audit_no_findings_for_non_git_commands() -> None:
    record = {"command": "npm install"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is False
    assert diag["finding_count"] == 0
    assert diag["severity"] == "none"


def test_git_command_audit_empty_record() -> None:
    diag = extract_git_command_audit({})

    assert diag["has_findings"] is False
    assert diag["finding_count"] == 0
    assert diag["severity"] == "none"


def test_git_command_audit_multiple_commands() -> None:
    record = {
        "commands": [
            {"command": "git stash"},
            {"command": "git reset --hard"},
            {"command": "git checkout feature"},
        ]
    }
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 3


def test_git_command_audit_jsonl_content() -> None:
    record = {"jsonl_content": '{"command": "git stash pop"}\n{"command": "git status"}'}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1
    assert "git stash" in diag["findings"][0]["command"]


def test_git_command_audit_jsonl_with_all_new_patterns() -> None:
    jsonl_lines = [
        '{"command": "git restore ."}',
        '{"command": "git clean -fd"}',
        '{"command": "git switch feature"}',
        '{"command": "git branch -D old"}',
        '{"command": "git worktree remove ../wt"}',
        '{"command": "git rebase --abort"}',
        '{"command": "git push --force origin main"}',
        '{"command": "git commit --amend"}',
        '{"command": "rm -rf .git"}',
        '{"command": "git log --oneline"}',  # safe
    ]
    record = {"jsonl_content": "\n".join(jsonl_lines)}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 9


def test_git_command_audit_plain_text_jsonl_lines() -> None:
    """Plain text lines (not JSON) containing dangerous git commands are detected."""
    record = {"jsonl_content": "running: git stash push\nnext: git status\nthen: git reset --soft HEAD~1"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 2


def test_git_command_audit_mixed_safe_and_dangerous() -> None:
    """Commands list mixing safe and dangerous git commands."""
    record = {
        "commands": [
            {"command": "git status"},
            {"command": "git diff"},
            {"command": "git stash push -m 'wip'"},
            {"command": "git log --oneline -5"},
            {"command": "git reset --hard HEAD~1"},
        ]
    }
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 2


def test_git_command_audit_cmd_field() -> None:
    """The 'cmd' field is also scanned."""
    record = {"cmd": "git restore --worktree ."}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 1


def test_git_command_audit_case_insensitive() -> None:
    """Dangerous commands are detected regardless of case."""
    record = {"command": "GIT STASH POP"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True


def test_git_command_audit_stash_list_is_safe() -> None:
    """git stash list is a read-only query, not dangerous."""
    record = {"command": "git stash list"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is False


def test_git_command_audit_stash_show_is_safe() -> None:
    """git stash show is a read-only query, not dangerous."""
    record = {"command": "git stash show --stat"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is False


def test_git_command_audit_branch_list_is_safe() -> None:
    """git branch without -d/-D is safe."""
    record = {"command": "git branch -a --contains HEAD"}
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is False


def test_git_command_audit_commands_string_array() -> None:
    """commands as list[str] detects dangerous git commands."""
    record: dict[str, Any] = {
        "commands": [
            "git stash pop",
            "git status",
            "git reset --hard HEAD~1",
            "git diff",
        ]
    }
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 2
    commands_found = [f["command"] for f in diag["findings"]]
    assert any("git stash pop" in c for c in commands_found)
    assert any("git reset" in c for c in commands_found)


def test_git_command_audit_mixed_array_of_dict_and_str() -> None:
    """commands as mixed list[dict|str] detects all dangerous git commands."""
    record: dict[str, Any] = {
        "commands": [
            {"command": "git restore --staged ."},
            "git stash clear",
            {"command": "git status"},
            "git branch -D feature-x",
            "git log --oneline",
        ]
    }
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 3
    categories = {f["category"] for f in diag["findings"]}
    assert "file_discard" in categories
    assert "stash_manipulation" in categories
    assert "branch_delete" in categories


def test_git_command_audit_events_string_array() -> None:
    """events as list[str] detects dangerous git commands."""
    record: dict[str, Any] = {
        "events": [
            "git clean -fd",
            "git push --force origin main",
            "git status",
        ]
    }
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 2


def test_git_command_audit_execution_log_string_array() -> None:
    """execution_log as list[str] detects dangerous git commands."""
    record: dict[str, Any] = {
        "execution_log": [
            "git switch feature-branch",
            "git commit --amend -m 'fix'",
            "git diff",
        ]
    }
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert diag["finding_count"] == 2


def test_git_command_audit_string_array_safe_only() -> None:
    """commands as list[str] with only safe commands returns no findings."""
    record: dict[str, Any] = {
        "commands": [
            "git status",
            "git diff",
            "git log --oneline",
            "git show HEAD",
            "git branch",
        ]
    }
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is False
    assert diag["finding_count"] == 0
    assert diag["severity"] == "none"


def test_git_command_audit_string_array_rm_rf_git() -> None:
    """rm -rf .git in string array is detected as repo_destruction."""
    record: dict[str, Any] = {
        "commands": [
            "rm -rf .git",
            "git status",
        ]
    }
    diag = extract_git_command_audit(record)

    assert diag["has_findings"] is True
    assert any(f["category"] == "repo_destruction" for f in diag["findings"])


def test_git_command_audit_categories() -> None:
    """Each dangerous command maps to the expected category."""
    cases = [
        ("git stash push", "stash_manipulation"),
        ("git stash pop", "stash_manipulation"),
        ("git reset --soft HEAD~1", "history_rewrite"),
        ("git checkout feature", "branch_switch"),
        ("git restore .", "file_discard"),
        ("git clean -fd", "file_discard"),
        ("git switch main", "branch_switch"),
        ("git branch -D x", "branch_delete"),
        ("git worktree remove ../x", "worktree_mutation"),
        ("git rebase --abort", "rebase_abort"),
        ("git push --force origin main", "force_push"),
        ("git commit --amend", "history_rewrite"),
    ]
    for cmd, expected_category in cases:
        record = {"command": cmd}
        diag = extract_git_command_audit(record)
        assert diag["has_findings"] is True, f"Expected findings for: {cmd}"
        assert diag["findings"][0]["category"] == expected_category, (
            f"Expected category {expected_category!r} for {cmd!r}, got {diag['findings'][0]['category']!r}"
        )


# --- is_git_safe_command ---


def test_is_git_safe_command_true() -> None:
    for cmd in ["git status", "git diff", "git log", "git show HEAD", "git branch", "git stash list"]:
        assert is_git_safe_command(cmd) is True, f"Expected safe: {cmd}"


def test_is_git_safe_command_false() -> None:
    for cmd in ["git stash", "git reset --hard", "git checkout main", "git restore .", "git clean -fd"]:
        assert is_git_safe_command(cmd) is False, f"Expected not safe: {cmd}"


def test_is_git_safe_command_non_git() -> None:
    assert is_git_safe_command("npm install") is False


# --- diagnose_project git integration ---


def test_diagnose_project_includes_git_audit() -> None:
    record = _r3_sample_record(command="git stash push -m 'wip'")
    diag = diagnose_project(record)

    assert "git_command_audit" in diag
    assert diag["git_command_audit"]["has_findings"] is True
    assert diag["git_command_violation"] is True


def test_diagnose_project_no_git_violation() -> None:
    record = _r3_sample_record(command="git status")
    diag = diagnose_project(record)

    assert diag["git_command_audit"]["has_findings"] is False
    assert diag["git_command_violation"] is False


def test_diagnose_run_aggregates_git_violations() -> None:
    records = [
        _r3_sample_record(project_id="L1-01", command="git stash push"),
        _r3_sample_record(project_id="L1-02", command="git status"),
        _r3_sample_record(project_id="L1-03", command="git reset --hard HEAD~1"),
    ]
    factory_audits: dict[str, Any] = {"total": 3, "all_checks_passed": 0}
    report = diagnose_run(factory_audits, records)

    assert report["git_command_violations"] == 2


# --- extract_failure_mode ---


def test_failure_mode_execution_missing() -> None:
    record = {
        "all_checks_passed": False,
        "llm_route_audit": {"ok": False},
        "real_run_gate": {"ok": False},
        "code_file_count": 0,
        "artifacts": {},
        "chain_state": "fail",
    }
    diag = extract_failure_mode(record)

    assert diag["failure_mode"] == "execution_missing"
    assert diag["is_failure"] is True
    assert diag["has_director_execution"] is False
    assert diag["has_materialized_changes"] is False
    assert diag["has_evidence_artifacts"] is False


def test_failure_mode_materialization_failure() -> None:
    record = {
        "all_checks_passed": False,
        "llm_route_audit": {"ok": True},
        "real_run_gate": {"ok": True},
        "code_file_count": 0,
        "artifacts": {},
        "chain_state": "partial",
    }
    diag = extract_failure_mode(record)

    assert diag["failure_mode"] == "materialization_failure"
    assert diag["is_failure"] is True
    assert diag["has_director_execution"] is True
    assert diag["has_materialized_changes"] is False


def test_failure_mode_evidence_loss() -> None:
    record = {
        "all_checks_passed": False,
        "llm_route_audit": {"ok": True},
        "real_run_gate": {"ok": True},
        "code_file_count": 5,
        "artifacts": {},
        "chain_state": "partial",
    }
    diag = extract_failure_mode(record)

    assert diag["failure_mode"] == "evidence_loss"
    assert diag["is_failure"] is True
    assert diag["has_director_execution"] is True
    assert diag["has_materialized_changes"] is True
    assert diag["has_evidence_artifacts"] is False


def test_failure_mode_stage_failure() -> None:
    record = {
        "all_checks_passed": False,
        "llm_route_audit": {"ok": True},
        "real_run_gate": {"ok": True},
        "code_file_count": 5,
        "artifacts": {"plan": ["plan.md"], "blueprint": [], "verdict": []},
        "chain_state": "fail",
        "chain_results": {"qa_ran": False, "qa_passed": False},
        "has_qa_verdict": False,
    }
    diag = extract_failure_mode(record)

    assert diag["failure_mode"] == "stage_failure"
    assert diag["is_failure"] is True
    assert len(diag["stage_failures"]) > 0


def test_failure_mode_passing_record() -> None:
    record = {
        "all_checks_passed": True,
        "llm_route_audit": {"ok": True},
        "real_run_gate": {"ok": True},
        "code_file_count": 5,
        "artifacts": {"plan": ["plan.md"], "blueprint": [], "verdict": []},
    }
    diag = extract_failure_mode(record)

    assert diag["failure_mode"] == "none"
    assert diag["is_failure"] is False


def test_failure_mode_empty_record() -> None:
    diag = extract_failure_mode({})

    assert diag["failure_mode"] == "execution_missing"
    assert diag["is_failure"] is True


def test_failure_mode_evidence_details() -> None:
    record = {
        "all_checks_passed": False,
        "llm_route_audit": {"ok": True},
        "real_run_gate": {"ok": True},
        "code_file_count": 5,
        "artifacts": {"plan": [], "blueprint": [], "verdict": []},
        "chain_state": "partial",
    }
    diag = extract_failure_mode(record)

    assert diag["failure_mode"] == "evidence_loss"
    assert "execution_and_changes_but_no_artifacts" in diag["evidence"]


# --- extract_director_convergence_diagnostics ---


def test_director_convergence_extracts_explicit_block() -> None:
    record = _r3_sample_record(
        director_convergence={
            "qa_ran": False,
            "blocking_phase": "director_dispatch",
            "taskboard_initial": {"total": 3, "claimed": 0, "completed": 0, "failed": 0, "blocked": 0},
            "taskboard_final": {"total": 3, "claimed": 2, "completed": 1, "failed": 1, "blocked": 0},
            "missing_delivery_targets": ["quality_gate"],
            "per_binding_task_status": [
                {"task_id": "TASK-1", "status": "completed", "events": ["task_started", "task_completed"]},
                {"task_id": "TASK-2", "status": "failed", "events": ["task_started", "task_failed"]},
            ],
            "director_summary": {"total": 3, "successes": 1, "failures": 2, "blocked": 0},
        },
    )
    diag = extract_director_convergence_diagnostics(record)

    assert diag["has_convergence_data"] is True
    assert diag["qa_ran"] is False
    assert diag["blocking_phase"] == "director_dispatch"
    assert diag["taskboard_final"]["completed"] == 1
    assert diag["taskboard_final"]["failed"] == 1
    assert diag["missing_delivery_targets"] == ["quality_gate"]
    assert len(diag["per_binding_task_status"]) == 2
    assert diag["director_summary"]["total"] == 3


def test_director_convergence_fallback_from_chain_results() -> None:
    record = _r3_sample_record()
    diag = extract_director_convergence_diagnostics(record)

    assert diag["has_convergence_data"] is True
    assert diag["qa_ran"] is False
    assert diag["taskboard_final"]["total"] == 3
    assert diag["taskboard_final"]["failures"] == 2
    assert diag["director_summary"]["total"] == 3


def test_director_convergence_empty_record() -> None:
    diag = extract_director_convergence_diagnostics({})

    assert diag["has_convergence_data"] is False
    assert diag["qa_ran"] is False
    assert diag["missing_delivery_targets"] == []
    assert diag["per_binding_task_status"] == []


def test_director_convergence_qa_ran_returns_data() -> None:
    record = _r3_sample_record(
        chain_results={"qa_ran": True, "qa_passed": True, "exit_class": "clean"},
        director_convergence={
            "qa_ran": True,
            "blocking_phase": "",
            "taskboard_initial": {},
            "taskboard_final": {},
            "missing_delivery_targets": [],
            "per_binding_task_status": [],
            "director_summary": None,
        },
    )
    diag = extract_director_convergence_diagnostics(record)

    assert diag["has_convergence_data"] is True
    assert diag["qa_ran"] is True
    assert diag["blocking_phase"] == ""


def test_diagnose_project_includes_convergence() -> None:
    record = _r3_sample_record()
    diag = diagnose_project(record)

    assert "director_convergence" in diag
    assert diag["director_convergence"]["has_convergence_data"] is True


def test_director_convergence_missing_targets_present_in_diagnostic() -> None:
    """Regression: director_partial must surface missing delivery targets, not just qa_ran=False."""
    record = _r3_sample_record(
        chain_results={
            "qa_ran": False,
            "qa_passed": False,
            "qa_reason": "director failures present",
            "director": {"total": 5, "successes": 2, "failures": 3, "blocked": 0},
            "exit_class": "director_partial",
        },
        director_convergence={
            "qa_ran": False,
            "blocking_phase": "director_dispatch",
            "taskboard_initial": {"total": 5, "claimed": 0, "completed": 0, "failed": 0, "blocked": 0},
            "taskboard_final": {"total": 5, "claimed": 5, "completed": 2, "failed": 3, "blocked": 0},
            "missing_delivery_targets": ["quality_gate"],
            "per_binding_task_status": [
                {"task_id": "T1", "status": "completed"},
                {"task_id": "T2", "status": "completed"},
                {"task_id": "T3", "status": "failed"},
                {"task_id": "T4", "status": "failed"},
                {"task_id": "T5", "status": "failed"},
            ],
            "director_summary": {"total": 5, "successes": 2, "failures": 3, "blocked": 0},
        },
    )
    diag = extract_director_convergence_diagnostics(record)

    assert diag["qa_ran"] is False
    assert diag["missing_delivery_targets"] == ["quality_gate"]
    assert len(diag["per_binding_task_status"]) == 5
    failed_tasks = [t for t in diag["per_binding_task_status"] if t["status"] == "failed"]
    assert len(failed_tasks) == 3


# --- extract_real_run_diagnostics declared source targets ---


def test_real_run_extracts_declared_source_targets() -> None:
    record = _r3_sample_record(
        declared_source_targets=["src/index.ts", "src/utils.ts"],
        declared_source_target_count=2,
        missing_declared_source_targets=["src/utils.ts"],
        missing_declared_source_target_count=1,
        pm_plan_missing_source_targets=False,
    )
    diag = extract_real_run_diagnostics(record)

    assert diag["has_gate"] is True
    assert diag["declared_source_targets"]["declared_count"] == 2
    assert diag["declared_source_targets"]["missing_count"] == 1
    assert "src/utils.ts" in diag["declared_source_targets"]["missing_targets"]
    assert diag["declared_source_targets"]["pm_plan_missing_source_targets"] is False


def test_real_run_pm_plan_missing_source_targets_signal() -> None:
    record = _r3_sample_record(
        declared_source_targets=[],
        declared_source_target_count=0,
        missing_declared_source_targets=[],
        missing_declared_source_target_count=0,
        pm_plan_missing_source_targets=True,
    )
    diag = extract_real_run_diagnostics(record)

    assert diag["declared_source_targets"]["declared_count"] == 0
    assert diag["declared_source_targets"]["pm_plan_missing_source_targets"] is True


def test_real_run_no_declared_targets_no_plan() -> None:
    record = _r3_sample_record()
    diag = extract_real_run_diagnostics(record)

    assert diag["declared_source_targets"]["declared_count"] == 0
    assert diag["declared_source_targets"]["missing_count"] == 0
    assert diag["declared_source_targets"]["pm_plan_missing_source_targets"] is False


def test_real_run_missing_gate_returns_declared_targets_defaults() -> None:
    diag = extract_real_run_diagnostics({})

    assert diag["has_gate"] is False
    assert diag["declared_source_targets"]["declared_count"] == 0
    assert diag["declared_source_targets"]["missing_count"] == 0
    assert diag["declared_source_targets"]["missing_targets"] == []
    assert diag["declared_source_targets"]["pm_plan_missing_source_targets"] is False
