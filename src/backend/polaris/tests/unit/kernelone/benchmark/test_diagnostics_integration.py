"""Tests verifying audit_diagnostics is integrated into factory-bench audit output.

Ensures per-project audit JSON and batch summary both contain diagnostics
with expected fields, without altering PASS/FAIL verdicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.audit_diagnostics import (
    DIAGNOSTIC_SCHEMA_VERSION,
    diagnose_project,
    diagnose_run,
)
from polaris.kernelone.benchmark.factory_audit import (
    aggregate_factory_audits,
    build_factory_audit_record,
)


def _project_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "project"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (ws / "index.html").write_text("<html><body>hi</body></html>\n", encoding="utf-8")
    (ws / "README.md").write_text("# Project\n", encoding="utf-8")
    return ws


def _sample_record(tmp_path: Path, *, project_id: str = "L1-01", level: int = 1) -> dict[str, Any]:
    ws = _project_workspace(tmp_path)
    project = {
        "id": project_id,
        "level": level,
        "domain": "creative",
        "title": "Test Project",
        "checks": ["py_compile", "html", "min_files:2"],
    }
    return build_factory_audit_record(
        project=project,
        workspace=str(ws),
        artifact_globs={"plan": ["docs/plan.md"], "blueprint": [], "verdict": []},
    )


def _enrich_record(record: dict[str, Any]) -> dict[str, Any]:
    """Add fields that the runner normally populates (chain_results, gates, etc.)."""
    record["chain_state"] = "clean"
    record["chain_results"] = {
        "qa_ran": True,
        "qa_passed": True,
        "qa_reason": "ok",
        "director": {"total": 3, "successes": 3, "failures": 0, "blocked": 0},
        "exit_class": "clean",
    }
    record["factory_gates"] = [
        {"gate": "chain_clean", "ok": True, "detail": "clean"},
        {"gate": "integration_qa_passed", "ok": True, "detail": "ok"},
    ]
    record["real_run_gate"] = {
        "ok": True,
        "summary": "real run gate passed",
        "requirements": {},
        "commands": [],
        "entrypoint": {"ok": True, "kind": "web_static", "detail": "passed"},
    }
    record["llm_route_audit"] = {
        "ok": True,
        "roles": {
            "pm": {
                "ok": True,
                "configured": [{"role": "pm", "provider_id": "kimi", "model": "kimi-latest"}],
                "observed_count": 1,
                "observed_bindings": ["kimi|kimi-latest"],
                "missing_bindings": [],
                "family_ok": True,
                "multi_route_ok": True,
            },
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
    record["failure_taxonomy"] = {
        "ok": True,
        "category": "",
        "root_cause_signature": "pass",
        "reasons": [],
        "evidence": [],
    }
    record["wrong_product_suspect"] = False
    record["wrong_product_match"] = ""
    record["brief_goal_overlap"] = 0.5
    record["all_checks_passed"] = True
    return record


# --- Per-project audit diagnostics ---


def test_per_project_audit_contains_diagnostics(tmp_path: Path) -> None:
    record = _enrich_record(_sample_record(tmp_path))
    diag = diagnose_project(record)

    assert diag["schema_version"] == DIAGNOSTIC_SCHEMA_VERSION
    assert diag["project_id"] == "L1-01"
    assert "director_route" in diag
    assert "real_run" in diag
    assert "stage_failure" in diag
    assert "qa" in diag
    assert "failure_taxonomy" in diag


def test_per_project_diagnostics_director_route_fields(tmp_path: Path) -> None:
    record = _enrich_record(_sample_record(tmp_path))
    diag = diagnose_project(record)

    dr = diag["director_route"]
    assert dr["has_audit"] is True
    assert dr["ok"] is True
    assert "pm" in dr["roles"]
    assert "director" in dr["roles"]


def test_per_project_diagnostics_real_run_fields(tmp_path: Path) -> None:
    record = _enrich_record(_sample_record(tmp_path))
    diag = diagnose_project(record)

    rr = diag["real_run"]
    assert rr["has_gate"] is True
    assert rr["ok"] is True
    assert isinstance(rr["failed_commands"], list)


def test_per_project_diagnostics_qa_verdict_fields(tmp_path: Path) -> None:
    record = _enrich_record(_sample_record(tmp_path))
    diag = diagnose_project(record)

    qa = diag["qa"]
    assert qa["has_plan_doc"] is True
    assert qa["has_qa_verdict"] is False


def test_per_project_diagnostics_failure_taxonomy_fields(tmp_path: Path) -> None:
    record = _enrich_record(_sample_record(tmp_path))
    diag = diagnose_project(record)

    ft = diag["failure_taxonomy"]
    assert ft["has_taxonomy"] is True
    assert ft["ok"] is True
    assert ft["root_cause_signature"] == "pass"


def test_per_project_diagnostics_does_not_alter_verdict(tmp_path: Path) -> None:
    record = _enrich_record(_sample_record(tmp_path))
    original_pass = record["all_checks_passed"]
    diag = diagnose_project(record)

    assert diag["all_checks_passed"] == original_pass


# --- Batch summary diagnostics ---


def test_batch_summary_contains_diagnostics(tmp_path: Path) -> None:
    records = [
        _enrich_record(_sample_record(tmp_path / "p1", project_id="L1-01")),
        _enrich_record(_sample_record(tmp_path / "p2", project_id="L2-07")),
    ]
    aggregate = aggregate_factory_audits(records)
    batch_diag = diagnose_run({"aggregate": aggregate}, records)

    assert batch_diag["schema_version"] == DIAGNOSTIC_SCHEMA_VERSION
    assert "run_summary" in batch_diag
    assert "director_route_failures" in batch_diag
    assert "real_run_failures" in batch_diag
    assert "failure_categories" in batch_diag
    assert "root_cause_signatures" in batch_diag
    assert "projects" in batch_diag


def test_batch_diagnostics_run_summary_counts(tmp_path: Path) -> None:
    records = [
        _enrich_record(_sample_record(tmp_path / "p1", project_id="L1-01")),
        _enrich_record(_sample_record(tmp_path / "p2", project_id="L2-07")),
    ]
    aggregate = aggregate_factory_audits(records)
    batch_diag = diagnose_run({"aggregate": aggregate}, records)

    rs = batch_diag["run_summary"]
    assert rs["total"] == 2
    assert rs["passed"] == 2
    assert rs["failed"] == 0


def test_batch_diagnostics_per_project_entries(tmp_path: Path) -> None:
    records = [
        _enrich_record(_sample_record(tmp_path / "p1", project_id="L1-01")),
        _enrich_record(_sample_record(tmp_path / "p2", project_id="L2-07")),
    ]
    aggregate = aggregate_factory_audits(records)
    batch_diag = diagnose_run({"aggregate": aggregate}, records)

    assert len(batch_diag["projects"]) == 2
    pids = {p["project_id"] for p in batch_diag["projects"]}
    assert pids == {"L1-01", "L2-07"}


def test_batch_diagnostics_director_route_failures_count(tmp_path: Path) -> None:
    """When all routes pass, director_route_failures should be 0."""
    records = [_enrich_record(_sample_record(tmp_path / "p1", project_id="L1-01"))]
    aggregate = aggregate_factory_audits(records)
    batch_diag = diagnose_run({"aggregate": aggregate}, records)

    assert batch_diag["director_route_failures"] == 0


def test_batch_diagnostics_failure_categories_empty_on_pass(tmp_path: Path) -> None:
    records = [_enrich_record(_sample_record(tmp_path / "p1", project_id="L1-01"))]
    aggregate = aggregate_factory_audits(records)
    batch_diag = diagnose_run({"aggregate": aggregate}, records)

    assert batch_diag["failure_categories"] == {}
    assert batch_diag["root_cause_signatures"] == {}


def test_batch_diagnostics_does_not_alter_verdict(tmp_path: Path) -> None:
    records = [
        _enrich_record(_sample_record(tmp_path / "p1", project_id="L1-01")),
    ]
    aggregate = aggregate_factory_audits(records)
    original_total = aggregate["total"]
    original_passed = aggregate["all_checks_passed"]

    batch_diag = diagnose_run({"aggregate": aggregate}, records)

    assert batch_diag["run_summary"]["total"] == original_total
    assert batch_diag["run_summary"]["passed"] == original_passed


def test_batch_diagnostics_with_failing_project(tmp_path: Path) -> None:
    good = _enrich_record(_sample_record(tmp_path / "p1", project_id="L1-01"))
    bad = _enrich_record(_sample_record(tmp_path / "p2", project_id="L2-07"))
    bad["all_checks_passed"] = False
    bad["failure_taxonomy"] = {
        "ok": False,
        "category": "director_tool_execution",
        "root_cause_signature": "director_tool_execution:real_run_gate.entrypoint_smoke",
        "reasons": ["gate:real_run_gate=failed"],
        "evidence": ["entrypoint smoke failed"],
    }

    records = [good, bad]
    aggregate = aggregate_factory_audits(records)
    batch_diag = diagnose_run({"aggregate": aggregate}, records)

    assert batch_diag["run_summary"]["passed"] == 1
    assert batch_diag["run_summary"]["failed"] == 1
    assert "director_tool_execution" in batch_diag["failure_categories"]


# --- JSON serialization roundtrip ---


def test_per_project_diagnostics_json_serializable(tmp_path: Path) -> None:
    record = _enrich_record(_sample_record(tmp_path))
    diag = diagnose_project(record)

    serialized = json.dumps(diag, ensure_ascii=False)
    deserialized = json.loads(serialized)

    assert deserialized["schema_version"] == DIAGNOSTIC_SCHEMA_VERSION
    assert deserialized["project_id"] == "L1-01"


def test_batch_diagnostics_json_serializable(tmp_path: Path) -> None:
    records = [_enrich_record(_sample_record(tmp_path / "p1", project_id="L1-01"))]
    aggregate = aggregate_factory_audits(records)
    batch_diag = diagnose_run({"aggregate": aggregate}, records)

    serialized = json.dumps(batch_diag, ensure_ascii=False)
    deserialized = json.loads(serialized)

    assert deserialized["schema_version"] == DIAGNOSTIC_SCHEMA_VERSION
    assert deserialized["run_summary"]["total"] == 1
