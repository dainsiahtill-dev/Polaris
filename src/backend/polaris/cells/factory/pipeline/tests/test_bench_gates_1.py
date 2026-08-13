from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.verifier_policy.public import (
    UpdateVerifierPolicyCommandV1,
    update_verifier_policy,
)
from polaris.cells.factory.pipeline.internal import bench_gates
from polaris.cells.factory.pipeline.internal.bench_gates import (
    _collect_go_local_imports,
    _command_serves_build_output,
    _discover_go_package_dirs,
    _go_command,
    _go_version_of,
    _infer_go_module_name,
    _normalize_go_imports,
    _primary_source_language,
    _read_go_mod_module,
    _repair_go_import_subpath,
    _resolve_polaris_roots_runtime_dir,
    _script_depends_on_build_output,
    aggregate_goal_audit,
    apply_factory_bench_failure_taxonomy,
    build_llm_route_audit,
    build_real_run_gate,
    classify_factory_bench_failure,
    collect_llm_events,
)


def _real_llm_event(
    role: str,
    provider_id: str,
    model: str,
    binding_id: str = "",
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": "llm_call_end",
        "role": role,
        "provider_id": provider_id,
        "model": model,
        "source": "llm",
        "terminal": True,
        "invocation": True,
    }
    if binding_id:
        event["binding_id"] = binding_id
    return event


def _canonical_run_ledger_projection(
    *,
    task_boundary_failures: list[dict[str, Any]] | None = None,
    tool_lifecycle_failures: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the minimal authoritative projection used by taxonomy tests."""

    boundary_failures = [dict(item) for item in task_boundary_failures or []]
    lifecycle_failures = {str(task_key): dict(item) for task_key, item in (tool_lifecycle_failures or {}).items()}
    return {
        "schema_version": 1,
        "source": "run_ledger",
        "ok": not boundary_failures and not lifecycle_failures,
        "integrity_ok": not lifecycle_failures,
        "outcome_ok": not boundary_failures,
        "gate_count": 1,
        "gates": [
            {
                "name": "qa_verdict",
                "stage": "qa",
                "ok": True,
                "summary": "QA passed",
                "content_id": "qa-content-1",
                "append_id": "qa-append-1",
                "capability_ok": True,
            }
        ],
        "capability": {"ok": True, "issues": []},
        "evidence_policy": {
            "ok": True,
            "integrity_ok": True,
            "outcome_ok": True,
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
        "task_boundary": {
            "ok": not boundary_failures,
            "verdict_count": max(1, len(boundary_failures)),
            "failed": boundary_failures,
            "latest": boundary_failures[-1] if boundary_failures else {"ok": True, "status": "completed_verified"},
        },
        "tool_lifecycle": {
            "ok": not lifecycle_failures,
            "unresolved_by_task": lifecycle_failures,
        },
    }


def _canonical_task_runtime_projection(
    *,
    authoritative: bool = True,
) -> dict[str, Any]:
    return {
        "schema_version": "task_runtime.observable_task_rows_authority.v1",
        "source": "task_runtime.execution_fact",
        "authoritative": authoritative,
        "degraded": not authoritative,
        "row_count": 1,
        "rows": [
            {
                "task_id": "TASK-1",
                "status": "completed",
                "execution_state": "completed",
                "fact_event_seq": 1,
                "source": "task_runtime.execution_fact",
                "status_source": "task_runtime.execution_fact",
            }
        ],
        "readiness": {"ready": authoritative, "blocking_reasons": []},
    }




def test_canonical_projection_requires_final_qa_event_not_legacy_artifact() -> None:
    ledger = _canonical_run_ledger_projection()
    ledger["gates"] = [
        {
            "name": "qa_exception",
            "stage": "qa",
            "ok": False,
            "summary": "legacy QA exception artifact",
            "content_id": "qa-content-legacy",
            "append_id": "qa-append-legacy",
            "capability_ok": True,
        }
    ]
    record = {
        "has_qa_verdict": True,
        "run_ledger_projection": ledger,
        "task_runtime_projection": _canonical_task_runtime_projection(),
    }

    projection = bench_gates.build_canonical_bench_projection(record)

    assert projection["qa"]["authoritative"] is False
    assert projection["execution"]["ok"] is False
    assert projection["execution"]["reason_code"] == "qa_verdict_missing"
    assert projection["legacy_artifacts"]["has_qa_verdict"] is True
    assert projection["legacy_artifacts"]["authoritative"] is False


def test_canonical_pass_does_not_require_legacy_qa_artifact() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": True,
        "has_qa_verdict": False,
        "run_ledger_projection": _canonical_run_ledger_projection(),
        "task_runtime_projection": _canonical_task_runtime_projection(),
    }
    record["canonical_projection"] = bench_gates.build_canonical_bench_projection(record)

    taxonomy = classify_factory_bench_failure(record)

    assert record["canonical_projection"]["execution"]["ok"] is True
    assert taxonomy["ok"] is True
    assert taxonomy["authoritative"] is True
    assert taxonomy["source"] == "canonical_projection"


def test_canonical_projection_requires_task_runtime_fact_authority() -> None:
    record = {
        "run_ledger_projection": _canonical_run_ledger_projection(),
        "task_runtime_projection": _canonical_task_runtime_projection(authoritative=False),
    }

    projection = bench_gates.build_canonical_bench_projection(record)

    assert projection["runtime"]["authoritative"] is False
    assert projection["execution"]["ok"] is False
    assert projection["execution"]["reason_code"] == "task_runtime_projection_not_authoritative"


def test_bench_completed_verified_boundary_does_not_override_failed_runtime() -> None:
    """Bench projection preserves independent TaskRuntime lifecycle authority."""

    ledger = _canonical_run_ledger_projection()
    ledger["task_boundary"] = {
        "ok": True,
        "verdict_count": 3,
        "failed": [],
        "latest": {
            "task_id": "TASK-2",
            "status": "completed_verified",
            "ok": True,
            "failure_class": "PASSED",
        },
        "latest_by_task": {
            "TASK-1": {
                "task_id": "TASK-1",
                "status": "completed_verified",
                "ok": True,
                "failure_class": "PASSED",
            },
            "TASK-2": {
                "task_id": "TASK-2",
                "status": "completed_verified",
                "ok": True,
                "failure_class": "PASSED",
            },
            "TASK-3": {
                "task_id": "TASK-3",
                "status": "completed_verified",
                "ok": True,
                "failure_class": "PASSED",
            },
        },
    }
    runtime = {
        "schema_version": "task_runtime.observable_task_rows_authority.v1",
        "source": "task_runtime.execution_fact",
        "authoritative": True,
        "degraded": False,
        "row_count": 3,
        "rows": [
            {
                "task_id": "1",
                "status": "completed",
                "execution_state": "completed",
                "fact_event_seq": 1,
                "source": "task_runtime.execution_fact",
                "status_source": "task_runtime.execution_fact",
            },
            {
                "task_id": "2",
                "status": "completed",
                "execution_state": "completed",
                "fact_event_seq": 2,
                "source": "task_runtime.execution_fact",
                "status_source": "task_runtime.execution_fact",
            },
            {
                "task_id": "3",
                "status": "failed",
                "execution_state": "failed",
                "fact_event_seq": 3,
                "source": "task_runtime.execution_fact",
                "status_source": "task_runtime.execution_fact",
            },
        ],
        "readiness": {"ready": True, "blocking_reasons": []},
    }
    record = {
        "run_ledger_projection": ledger,
        "task_runtime_projection": runtime,
    }

    projection = bench_gates.build_canonical_bench_projection(record)

    assert projection["runtime"]["authoritative"] is True
    assert projection["runtime"]["completed"] is False
    assert projection["runtime"]["incomplete_task_ids"] == ["3"]
    assert projection["execution"]["ok"] is False
    assert projection["execution"]["reason_code"] == "task_runtime_not_completed"


def test_bench_completed_verified_boundary_does_not_override_active_runtime() -> None:
    """Bench keeps an in-progress TaskRuntime row incomplete despite a green boundary."""

    ledger = _canonical_run_ledger_projection()
    ledger["task_boundary"] = {
        "ok": True,
        "verdict_count": 1,
        "failed": [],
        "latest": {"task_id": "TASK-2", "status": "completed_verified", "ok": True},
        "latest_by_task": {
            "TASK-2": {"task_id": "TASK-2", "status": "completed_verified", "ok": True},
        },
    }
    runtime = {
        "schema_version": "task_runtime.observable_task_rows_authority.v1",
        "source": "task_runtime.execution_fact",
        "authoritative": True,
        "degraded": False,
        "row_count": 1,
        "rows": [
            {
                "task_id": "2",
                "status": "pending",
                "execution_state": "in_progress",
                "fact_event_seq": 4,
                "source": "task_runtime.execution_fact",
                "status_source": "task_runtime.execution_fact",
            },
        ],
        "readiness": {"ready": True, "blocking_reasons": []},
    }

    projection = bench_gates.build_canonical_bench_projection(
        {
            "run_ledger_projection": ledger,
            "task_runtime_projection": runtime,
        }
    )

    assert projection["runtime"]["completed"] is False
    assert projection["runtime"]["incomplete_task_ids"] == ["2"]
    assert projection["execution"]["ok"] is False
    assert projection["execution"]["reason_code"] == "task_runtime_not_completed"


def test_failed_qa_verdict_is_not_masked_by_failed_task_runtime_helper() -> None:
    ledger = _canonical_run_ledger_projection()
    ledger["gates"][0] = {
        **ledger["gates"][0],
        "ok": False,
        "summary": "npm test failed",
    }
    runtime = _canonical_task_runtime_projection()
    runtime["rows"][0] = {
        **runtime["rows"][0],
        "status": "failed",
        "execution_state": "failed",
    }

    projection = bench_gates.build_canonical_bench_projection(
        {
            "run_ledger_projection": ledger,
            "task_runtime_projection": runtime,
        }
    )

    assert projection["runtime"]["completed"] is False
    assert projection["qa"]["authoritative"] is True
    assert projection["qa"]["ok"] is False
    assert projection["execution"]["reason_code"] == "qa_verdict_failed"
    assert projection["execution"]["responsible_layer"] == "qa"


def test_r181_bench_failed_runtime_without_boundary_still_incomplete() -> None:
    """Without completed_verified boundary, failed runtime stays incomplete."""

    ledger = _canonical_run_ledger_projection()
    ledger["task_boundary"] = {
        "ok": True,
        "verdict_count": 1,
        "failed": [],
        "latest": {"task_id": "TASK-1", "status": "completed_verified", "ok": True},
        "latest_by_task": {
            "TASK-1": {"task_id": "TASK-1", "status": "completed_verified", "ok": True},
        },
    }
    runtime = {
        "schema_version": "task_runtime.observable_task_rows_authority.v1",
        "source": "task_runtime.execution_fact",
        "authoritative": True,
        "degraded": False,
        "row_count": 2,
        "rows": [
            {
                "task_id": "1",
                "status": "completed",
                "execution_state": "completed",
                "fact_event_seq": 1,
                "source": "task_runtime.execution_fact",
                "status_source": "task_runtime.execution_fact",
            },
            {
                "task_id": "3",
                "status": "failed",
                "execution_state": "failed",
                "fact_event_seq": 3,
                "source": "task_runtime.execution_fact",
                "status_source": "task_runtime.execution_fact",
            },
        ],
        "readiness": {"ready": True, "blocking_reasons": []},
    }
    record = {
        "run_ledger_projection": ledger,
        "task_runtime_projection": runtime,
    }
    projection = bench_gates.build_canonical_bench_projection(record)
    assert projection["runtime"]["completed"] is False
    assert "3" in projection["runtime"]["incomplete_task_ids"]
    assert projection["execution"]["reason_code"] == "task_runtime_not_completed"


def test_legacy_text_cannot_change_canonical_failure_classification() -> None:
    boundary_failure = {
        "ok": False,
        "status": "missing_entrypoint_target",
        "failure_class": "MISSING_ENTRYPOINT_TARGET",
        "responsible_layer": "task_boundary",
        "reason": "Canonical boundary evidence",
    }
    base = {
        "all_checks_passed": False,
        "checks": [],
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "real_run_gate": {"ok": True},
        "llm_route_audit": {"ok": True},
        "run_ledger_projection": _canonical_run_ledger_projection(task_boundary_failures=[boundary_failure]),
    }
    poisoned = {
        **base,
        "has_qa_verdict": False,
        "chain_results": {
            "qa_ran": True,
            "qa_passed": True,
            "qa_reason": "integration_qa_passed and chain_clean",
        },
        "chain": {
            "audit_bundle": {
                "summary_json": "rate limit director_partial qa_failed",
                "failure": {"detail": "workspace_switch_failed"},
            }
        },
        "factory_gates": [
            {"gate": "chain_clean", "ok": True, "detail": "legacy pass"},
            {"gate": "integration_qa_passed", "ok": True, "detail": "legacy pass"},
        ],
    }

    base_taxonomy = apply_factory_bench_failure_taxonomy(dict(base))
    poisoned_record = dict(poisoned)
    poisoned_taxonomy = apply_factory_bench_failure_taxonomy(poisoned_record)

    assert poisoned_taxonomy["root_cause_signature"] == base_taxonomy["root_cause_signature"]
    assert poisoned_taxonomy["root_cause_signature"] == "task_boundary:missing_entrypoint_target"
    assert poisoned_taxonomy["source"] == "canonical_projection"
    assert poisoned_taxonomy["authoritative"] is True
    canonical_projection = poisoned_record["canonical_projection"]
    assert isinstance(canonical_projection, dict)
    assert canonical_projection["legacy_artifacts"] == {
        "source": "legacy_artifact",
        "authoritative": False,
        "degraded": True,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": False,
        "chain_results": poisoned["chain_results"],
    }


def test_apply_factory_bench_failure_taxonomy_exposes_top_level_fields() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [
            {
                "check": "ts_syntax",
                "ok": False,
                "detail": "TypeScript syntax check failed: src/engine/simulation.ts(58,16): TS1003",
            }
        ],
        "factory_gates": [
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran",
            }
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False},
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert record["failure_taxonomy"] == taxonomy
    assert record["failure_category"] == "llm_output"
    assert record["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert record["failure_reasons"]
    assert record["failure_evidence"] == ["real run gate failed: build_test_lint_ran"]
    assert "opencode_audit" not in record
    assert record["goal_audit"] == {
        "total": 1,
        "real_run_gate": {"passed": 0, "total": 1},
        "run_ledger": {"projected": 0, "total": 1, "missing": 1},
        "llm_route_audit": {"passed": 0, "total": 1},
        "failure_categories": {"llm_output": 1},
        "root_cause_signatures": {"llm_output:real_run_gate.build_test_lint_ran": 1},
    }


def test_failure_taxonomy_classifies_missing_run_ledger_gate_as_control_plane() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "factory_gates": [
            {
                "gate": "run_ledger_projection",
                "ok": False,
                "detail": "run ledger projection missing",
            }
        ],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "control_plane"
    assert taxonomy["root_cause_signature"] == "control_plane:run_ledger_projection_missing"
    assert taxonomy["evidence"] == ["run ledger projection missing"]


def test_failure_taxonomy_classifies_session_not_active_as_control_plane() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "run_ledger_projection": _canonical_run_ledger_projection(
            tool_lifecycle_failures={
                "2": {
                    "failure_class": "SESSION_NOT_ACTIVE",
                    "reason": "TaskRuntime heartbeat failed: session_not_active after factory cancellation",
                }
            }
        ),
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            }
        ],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "control_plane"
    assert taxonomy["root_cause_signature"] == "control_plane:session_not_active"
    assert taxonomy["evidence"] == ["TaskRuntime heartbeat failed: session_not_active after factory cancellation"]


def test_failure_taxonomy_classifies_all_failed_tool_batch_as_control_plane() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "run_ledger_projection": _canonical_run_ledger_projection(
            tool_lifecycle_failures={
                "2": {
                    "failure_class": "TOOL_DISPATCH_FAILED",
                    "reason": (
                        "tool_dispatch_failed: decoded tool batch produced only failed tool results; "
                        "no effect receipts were committed"
                    ),
                }
            }
        ),
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            }
        ],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "control_plane"
    assert taxonomy["root_cause_signature"] == "control_plane:tool_dispatch_failed"
    assert taxonomy["evidence"] == [
        (
            "tool_dispatch_failed: decoded tool batch produced only failed tool results; "
            "no effect receipts were committed"
        )
    ]


def test_failure_taxonomy_uses_canonical_incomplete_materialization_over_runtime_director_result(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    results_dir = runtime_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "director.result.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "summary": "Director completed with failures=1, blocked=1, successes=1/3",
                "task_results": [
                    {"task_id": "1", "status": "completed", "changed_files": ["package.json"]},
                    {
                        "task_id": "2",
                        "status": "failed",
                        "error": "director_no_materialized_changes",
                        "adapter_result": {
                            "failure_class": "INCOMPLETE_MATERIALIZATION",
                            "responsible_layer": "director",
                            "materialization_error": "director_no_materialized_changes",
                        },
                    },
                    {
                        "task_id": "3",
                        "status": "blocked",
                        "error": "blocked_by_failed_dependency",
                        "blocked_by": "2",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "runtime_dir": str(runtime_dir),
        "run_ledger_projection": _canonical_run_ledger_projection(
            task_boundary_failures=[
                {
                    "task_id": "2",
                    "status": "incomplete_materialization",
                    "failure_class": "INCOMPLETE_MATERIALIZATION",
                    "responsible_layer": "director",
                    "reason": "Director task produced no materialized workspace changes before timeout or completion",
                }
            ]
        ),
        "checks": [
            {
                "check": "implementation_depth",
                "ok": False,
                "detail": "test_source_files=0 < 1",
            }
        ],
        "factory_gates": [
            {
                "gate": "integration_qa_passed",
                "ok": False,
                "detail": "qa_ran=False qa_passed=False",
            },
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
        ],
        "chain": {
            "audit_bundle": {
                "failure": {
                    "code": "director.inflight_timeout_settled",
                    "detail": "Director dispatch failed",
                }
            }
        },
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:incomplete_materialization"
    assert taxonomy["evidence"] == [
        "failure_class=INCOMPLETE_MATERIALIZATION;"
        "responsible_layer=director;"
        "Director task produced no materialized workspace changes before timeout or completion"
    ]


def test_failure_taxonomy_reads_canonical_blocked_dependency(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    results_dir = runtime_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "director.result.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "task_results": [
                    {
                        "task_id": "3",
                        "status": "blocked",
                        "error": "blocked_by_failed_dependency",
                        "blocked_by": "2",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "runtime_dir": str(runtime_dir),
        "run_ledger_projection": _canonical_run_ledger_projection(
            task_boundary_failures=[
                {
                    "task_id": "3",
                    "status": "dependency_not_unlocked",
                    "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                    "responsible_layer": "task_boundary",
                    "reason": "Blocked by failed dependency: 2",
                }
            ]
        ),
        "checks": [],
        "factory_gates": [
            {
                "gate": "integration_qa_passed",
                "ok": False,
                "detail": "qa_ran=False qa_passed=False",
            }
        ],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:dependency_not_unlocked"
    assert taxonomy["evidence"] == [
        "failure_class=DEPENDENCY_NOT_UNLOCKED;responsible_layer=task_boundary;Blocked by failed dependency: 2"
    ]


def test_failure_taxonomy_prefers_task_boundary_dependency_over_event_wait_timeout(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    results_dir = runtime_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "director.result.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "task_results": [
                    {
                        "task_id": "3",
                        "status": "blocked",
                        "error": "blocked_by_failed_dependency",
                        "blocked_by": "2",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "runtime_dir": str(runtime_dir),
        "run_ledger_projection": _canonical_run_ledger_projection(
            task_boundary_failures=[
                {
                    "task_id": "3",
                    "status": "dependency_not_unlocked",
                    "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                    "responsible_layer": "task_boundary",
                    "reason": "Blocked by failed dependency: 2",
                }
            ]
        ),
        "checks": [],
        "real_run_gate": {
            "ok": False,
            "requirements": {
                "chain_terminal": {
                    "ok": False,
                    "detail": "chain_terminal=false; phase=event_wait_timeout; status=unknown",
                },
                "artifact_landed": {
                    "ok": False,
                    "detail": "not evaluated because the Polaris chain was non-terminal",
                },
            },
        },
        "factory_gates": [
            {
                "gate": "integration_qa_passed",
                "ok": False,
                "detail": "qa_ran=False qa_passed=False",
            }
        ],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:dependency_not_unlocked"
    assert taxonomy["evidence"] == [
        "failure_class=DEPENDENCY_NOT_UNLOCKED;responsible_layer=task_boundary;Blocked by failed dependency: 2"
    ]


def test_failure_taxonomy_classifies_director_rate_limit_as_runtime_environment() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "chain": {
            "audit_bundle": {
                "failure": {
                    "code": "director.provider_rate_limit",
                    "detail": "Director LLM provider rate limit/quota failure before tool dispatch: 429 Token Plan 用量上限",
                    "failure_class": "RESOURCE_BUDGET_EXHAUSTED",
                    "responsible_layer": "model_provider",
                }
            }
        },
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            }
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: source_files_present",
        },
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:director_provider_rate_limit"
    assert taxonomy["evidence"]
    assert "429" in taxonomy["evidence"][0]
    assert "Token Plan" in taxonomy["evidence"][0]
    assert taxonomy["evidence"]
    assert "Token Plan 用量上限" in taxonomy["evidence"][0]
    assert taxonomy["evidence"] == [
        "Director LLM provider rate limit/quota failure before tool dispatch: 429 Token Plan 用量上限"
    ]


def test_failure_taxonomy_reads_director_rate_limit_from_runtime_llm_events(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    events_dir = runtime_dir / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(
            {
                "data": {
                    "event_type": "llm_error",
                    "role": "director",
                    "model": "MiniMax-M3",
                    "error_category": "rate_limit",
                    "error_message": "429 Rate limited: Token Plan 用量上限",
                }
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "workspace": str(tmp_path),
        "runtime_dir": str(runtime_dir),
        "chain": {
            "audit_bundle": {
                "failure": {
                    "code": "director_no_materialized_changes",
                    "detail": "Director dispatch failed: error=director_no_materialized_changes",
                }
            }
        },
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            }
        ],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:director_provider_rate_limit"


def test_failure_taxonomy_reads_director_provider_invalid_request_from_runtime_llm_events(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    events_dir = runtime_dir / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(
            {
                "data": {
                    "event_type": "llm_error",
                    "role": "director",
                    "model": "kimi-for-coding",
                    "error_category": "unknown",
                    "error_message": (
                        "400 Client Error from https://api.kimi.com/coding/v1/messages: "
                        '{"error":{"type":"invalid_request_error","message":'
                        "\"tool_choice 'specified' is incompatible with thinking enabled\"}}"
                    ),
                }
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "workspace": str(tmp_path),
        "runtime_dir": str(runtime_dir),
        "chain": {
            "audit_bundle": {
                "failure": {
                    "code": "director_no_materialized_changes",
                    "detail": "Director dispatch failed: error=director_no_materialized_changes",
                }
            }
        },
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            }
        ],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:director_provider_invalid_request"
    assert "tool_choice" in taxonomy["evidence"][0]


def test_failure_taxonomy_reads_director_provider_timeout_from_runtime_llm_events(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    events_dir = runtime_dir / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(
            {
                "data": {
                    "event_type": "llm_error",
                    "role": "director",
                    "model": "gemma-local",
                    "error_category": "timeout",
                    "error_message": (
                        "HTTPConnectionPool(host='127.0.0.1', port=8000): "
                        "Max retries exceeded with url: /v1/chat/completions "
                        "(Caused by ConnectTimeoutError: Connection timed out)"
                    ),
                }
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "workspace": str(tmp_path),
        "runtime_dir": str(runtime_dir),
        "chain": {
            "audit_bundle": {
                "failure": {
                    "code": "director_no_materialized_changes",
                    "detail": "Director dispatch failed: error=director_no_materialized_changes",
                }
            }
        },
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            }
        ],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:director_provider_timeout"
    assert "ConnectTimeoutError" in taxonomy["evidence"][0]


def test_canonical_execution_failure_precedes_independent_real_run_failure() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "factory_gates": [
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran",
            },
            {
                "gate": "run_ledger_projection",
                "ok": False,
                "detail": "run ledger projection required evidence failed: command",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "commands_truncated": False,
            "requirements": {
                "artifact_landed": {"ok": True},
                "source_files_present": {"ok": True},
                "build_test_lint_ran": {"ok": False, "detail": "npm run test failed"},
                "entrypoint_smoke": {"ok": True},
            },
        },
        "run_ledger_projection": {
            "schema_version": 1,
            "source": "run_ledger",
            "ok": False,
            "integrity_ok": True,
            "outcome_ok": False,
            "event_count": 1,
            "gate_count": 1,
            "missing": [],
            "failed_gates": [{"name": "real_run_gate", "ok": False}],
            "capability": {"ok": True, "issues": [], "latest_token_id": "token-1"},
            "evidence_policy": {
                "ok": False,
                "integrity_ok": True,
                "outcome_ok": False,
                "required_modalities": ["code", "command"],
                "missing_required_modalities": [],
                "failed_required_modalities": ["command"],
            },
            "physical_evidence": {"command_count": 2, "sampled_command_count": 2},
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "control_plane"
    assert taxonomy["root_cause_signature"] == "control_plane:task_boundary_verdict_missing"
    assert taxonomy["authoritative"] is True
    assert record["goal_audit"]["run_ledger"] == {"projected": 1, "total": 1, "missing": 0}


def test_failure_taxonomy_prioritizes_event_wait_timeout_over_run_ledger_projection() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [
            {"check": "min_files:3", "ok": False, "detail": "0 source files (need >= 3)"},
        ],
        "chain": {
            "exit_code": -1,
            "error": "event_wait_timeout",
            "event_wait_error": {
                "kind": "runtime_v2_connection_failed",
                "message": "received 1012 (service restart)",
            },
            "last_observed_status": {"status": "running", "phase": "director_dispatch"},
        },
        "chain_diagnostics": {
            "backend_url": "http://127.0.0.1:50032",
            "workspace": "/tmp/factory-bench-L1-08-r06/L1-08",
            "event_wait_error": {
                "kind": "runtime_v2_connection_failed",
                "message": "received 1012 (service restart)",
            },
            "cancel_error": {
                "exception": "URLError",
                "reason": "<urlopen error [Errno 111] Connection refused>",
            },
        },
        "factory_gates": [
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate skipped: chain did not reach terminal state (event_wait_timeout)",
            },
            {
                "gate": "run_ledger_projection",
                "ok": False,
                "detail": "run ledger projection has 1 failed gate(s)",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "skipped": True,
            "summary": "real run gate skipped: chain did not reach terminal state (event_wait_timeout)",
            "requirements": {
                "chain_terminal": {
                    "ok": False,
                    "detail": "chain_terminal=false; phase=event_wait_timeout; status=unknown",
                },
            },
        },
        "run_ledger_projection": {"ok": False, "detail": "run ledger projection has 1 failed gate(s)"},
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:event_wait_runtime_v2_connection_failed"
    assert taxonomy["evidence"][0] == "received 1012 (service restart)"
    assert taxonomy["authoritative"] is False


def test_failure_taxonomy_uses_chain_diagnostics_when_chain_error_missing() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "chain": {"exit_code": -1},
        "chain_diagnostics": {
            "chain_non_terminal": True,
            "chain_non_terminal_target_files_truncated": True,
            "event_wait_error": {
                "kind": "runtime_v2_connection_failed",
                "message": "received 1012 (service restart)",
            },
        },
        "factory_gates": [
            {
                "gate": "run_ledger_projection",
                "ok": False,
                "detail": "run ledger projection has 1 failed gate(s)",
            }
        ],
        "run_ledger_projection": {"ok": False, "detail": "run ledger projection has 1 failed gate(s)"},
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:event_wait_runtime_v2_connection_failed"
    assert taxonomy["evidence"][0] == "received 1012 (service restart)"


def test_role_tool_failure_taxonomy_does_not_emit_platform_opencode_audit() -> None:
    record: dict[str, Any] = {
        "project_id": "L1-02",
        "level": 1,
        "chain": {
            "audit_bundle": {
                "events_tail": [
                    {
                        "message": (
                            "Director dispatch failed: Run status: failed | "
                            "error=director_materialization_semantic_quality_failed"
                        )
                    }
                ]
            }
        },
    }
    record["opencode_audit"] = {"required": True, "reason": "legacy_input"}

    taxonomy = bench_gates.apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "director_tool_execution"
    assert "opencode_audit" not in record


def test_failure_taxonomy_classifies_non_terminal_real_run_skip_as_runtime_environment() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "checks": [],
        "factory_gates": [
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate skipped: chain did not reach terminal state (event_wait_timeout)",
            }
        ],
        "real_run_gate": {
            "ok": False,
            "skipped": True,
            "summary": "real run gate skipped: chain did not reach terminal state (event_wait_timeout)",
            "requirements": {
                "chain_terminal": {
                    "ok": False,
                    "detail": "chain_terminal=false; phase=event_wait_timeout; status=unknown",
                },
                "artifact_landed": {
                    "ok": False,
                    "detail": "not evaluated because the Polaris chain was non-terminal",
                },
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:event_wait_timeout"
    assert taxonomy["evidence"] == ["real run gate skipped: chain did not reach terminal state (event_wait_timeout)"]


def test_start_failure_runtime_roles_not_ready_is_runtime_environment() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain": {
            "exit_code": -1,
            "error": "start_failed",
            "start_error": {
                "status": 409,
                "json": {
                    "error": {
                        "code": "RUNTIME_ROLES_NOT_READY",
                        "details": {
                            "role_issues": {
                                "director": (
                                    "director binding (openai_compat-1/qwen3.6-27b-code-gpu0) "
                                    "LLM not ready; run tests first"
                                )
                            }
                        },
                    }
                },
            },
        },
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=fail exit_code=-1"},
        ],
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:runtime_roles_not_ready"
    assert "openai_compat-1/qwen3.6-27b-code-gpu0" in taxonomy["evidence"][0]


def test_factory_bench_taxonomy_prioritizes_pm_runtime_environment_over_missing_ce_blueprint() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain_state": "partial",
        "terminal_status": "director_partial",
        "has_blueprint_doc": False,
        "checks": [
            {"check": "package_scripts", "ok": False, "detail": "package.json not found"},
        ],
        "factory_gates": [
            {
                "gate": "blueprint_artifact_present",
                "ok": False,
                "detail": "blueprint artifact missing",
            },
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
            {
                "gate": "llm_route_audit",
                "ok": False,
                "detail": "LLM route audit failed: pm, director",
            },
        ],
        "chain": {
            "exit_code": 1,
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": (
                        "PM planning failed: Run status: failed | failed_task=task-0-pm (pm) | "
                        "error=cognitive_runtime_mainline_unavailable:process:FileNotFoundError; "
                        "error_code=pm.run_status_non_success"
                    ),
                }
            },
        },
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: pm, director"},
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: source_files_present",
            "requirements": {
                "source_files_present": {"ok": False},
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:cognitive_runtime_mainline_unavailable"
    assert record["failure_category"] == "runtime_environment"
    assert "cognitive_runtime_mainline_unavailable" in record["failure_evidence"][0]


def test_factory_bench_taxonomy_prioritizes_director_fanout_over_real_run_gate() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "terminal_status": "director_partial",
        "checks": [],
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False},
                "entrypoint_smoke": {"ok": False},
            },
        },
        "chain": {
            "exit_code": 1,
            "chain_results": {
                "qa_ran": False,
                "qa_passed": False,
                "director": {
                    "total": None,
                    "successes": None,
                    "failures": None,
                    "blocked": None,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": (
                        "Director dispatch failed: Director binding fanout: 3 bindings, "
                        "0 succeeded, 3 failed, 3 quarantined; "
                        "error_code=director.run_status_non_success"
                    ),
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "director_tool_execution"
    assert taxonomy["root_cause_signature"] == "director_tool_execution:director_binding_fanout_failed"
    assert record["failure_category"] == "director_tool_execution"
    assert record["goal_audit"]["failure_categories"] == {"director_tool_execution": 1}
    assert record["goal_audit"]["root_cause_signatures"] == {
        "director_tool_execution:director_binding_fanout_failed": 1
    }
    assert "Director dispatch failed" in record["failure_evidence"][0]


def test_factory_bench_taxonomy_does_not_treat_ce_full_blueprint_count_as_partial() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain_state": "partial",
        "terminal_status": "director_partial",
        "has_blueprint_doc": True,
        "checks": [],
        "factory_gates": [
            {
                "gate": "blueprint_artifact_present",
                "ok": True,
                "detail": "blueprint present",
            },
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain": {
            "exit_code": 1,
            "stages": [
                {
                    "stage": "chief_engineer_review",
                    "status": "success",
                    "output": "Chief Engineer review generated 3/3 blueprints; signals=0",
                }
            ],
            "chain_results": {
                "qa_ran": True,
                "qa_passed": False,
                "director": {
                    "total": 3,
                    "successes": 0,
                    "failures": 3,
                    "blocked": 0,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": "Director dispatch failed: director_materialization_quality_failed",
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "director_tool_execution"
    assert taxonomy["root_cause_signature"] == "director_tool_execution:director_materialization_failed"
    assert taxonomy["authoritative"] is False
    assert record["failure_category"] == "director_tool_execution"
    assert "opencode_audit" not in record


def test_director_failure_taxonomy_ignores_unstructured_stage_history_note() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain_state": "partial",
        "terminal_status": "director_partial",
        "checks": [],
        "chain": {
            "exit_code": 1,
            "stages": [
                {
                    "stage": "director_dispatch",
                    "status": "failed",
                    "output": "historical note: previous round mentioned director_no_materialized_changes",
                }
            ],
            "chain_results": {
                "director": {
                    "total": 1,
                    "successes": 0,
                    "failures": 1,
                    "blocked": 0,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": "Director dispatch failed: error_code=director.run_status_non_success",
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "director_tool_execution"
    assert taxonomy["root_cause_signature"] == "director_tool_execution:director_run_status_non_success"


def test_role_tool_failure_taxonomy_keeps_opencode_out_of_platform_record() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "project_id": "L1-01",
        "level": 1,
        "backend_metadata": {"workspace": "/tmp/factory-bench"},
        "chain_results": {
            "director": {
                "total": 1,
                "successes": 0,
                "failures": 1,
                "blocked": 0,
            }
        },
        "chain": {
            "audit_bundle": {"failure": {"detail": "Director dispatch failed: director_materialization_quality_failed"}}
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "director_tool_execution"
    assert "opencode_audit" not in record


def test_pm_contract_failure_does_not_emit_platform_opencode_audit() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "project_id": "L1-01",
        "level": 1,
        "chain": {
            "chain_results": {
                "qa_ran": False,
                "qa_passed": False,
                "exit_class": "pm_failed",
                "factory_stage_hint": "pm_planning",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": "PM contract quality failed",
                }
            },
        },
        "factory_gates": [{"gate": "chain_terminal", "ok": False, "detail": "pm failed"}],
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "pm_contract"
    assert "opencode_audit" not in record


def test_factory_bench_taxonomy_prioritizes_post_qa_artifact_failure_over_director_failure() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "chain_state": "partial",
        "checks": [{"check": "ts_syntax", "ok": True, "detail": "8 TypeScript files pass"}],
        "factory_gates": [
            {
                "gate": "integration_qa_passed",
                "ok": False,
                "detail": "qa_ran=True qa_passed=False",
            },
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False, "detail": "npm run build failed"},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain": {
            "exit_code": 1,
            "chain_results": {
                "qa_ran": True,
                "qa_passed": False,
                "director": {
                    "total": None,
                    "successes": None,
                    "failures": None,
                    "blocked": None,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": "Director dispatch failed: director_materialization_quality_failed",
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert record["failure_category"] == "llm_output"
    assert "opencode_audit" not in record


def test_factory_bench_taxonomy_classifies_post_qa_typescript_failure_as_llm_output() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "terminal_status": "director_partial",
        "checks": [
            {
                "check": "ts_syntax",
                "ok": False,
                "detail": "TypeScript syntax check failed: tests/verify.test.ts(1,1698): TS1005",
            }
        ],
        "factory_gates": [
            {
                "gate": "chain_clean",
                "ok": False,
                "detail": "chain_state=partial exit_code=1",
            },
            {
                "gate": "integration_qa_passed",
                "ok": False,
                "detail": "qa_ran=True qa_passed=False",
            },
            {
                "gate": "real_run_gate",
                "ok": False,
                "detail": "real run gate failed: build_test_lint_ran, entrypoint_smoke",
            },
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran, entrypoint_smoke",
            "requirements": {
                "artifact_landed": {"ok": True},
                "environment_prepared": {"ok": True},
                "build_test_lint_ran": {"ok": False},
                "entrypoint_smoke": {"ok": False},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain": {
            "exit_code": 1,
            "chain_results": {
                "qa_ran": True,
                "qa_passed": False,
                "qa_reason": "npm run build failed with TypeScript errors",
                "director": {
                    "total": None,
                    "successes": None,
                    "failures": None,
                    "blocked": None,
                },
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": (
                        "Director dispatch failed: Director binding fanout: 3 bindings, "
                        "0 succeeded, 3 failed, 0 quarantined; Quality gate failed after Director dispatch"
                    ),
                }
            },
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"
    assert record["failure_category"] == "llm_output"
    assert record["goal_audit"]["failure_categories"] == {"llm_output": 1}
    assert "opencode_audit" not in record


def test_factory_bench_taxonomy_prioritizes_chief_engineer_blocker_over_downstream_route_audit() -> None:
    record: dict[str, Any] = {
        "all_checks_passed": False,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "chain_state": "partial",
        "checks": [
            {
                "check": "source_target_coverage:src/**/*.ts",
                "ok": False,
                "detail": "source target 'src/**/*.ts': no source files found",
            }
        ],
        "factory_gates": [
            {
                "gate": "blueprint_artifact_present",
                "ok": True,
                "detail": "blueprint artifact discovered",
            },
            {
                "gate": "llm_route_audit",
                "ok": False,
                "detail": "LLM route audit failed: director",
            },
        ],
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: director"},
        "chain": {
            "exit_code": 1,
            "chain_results": {
                "qa_ran": False,
                "qa_passed": False,
                "director": {"total": None, "successes": None, "failures": None, "blocked": None},
                "exit_class": "director_partial",
            },
            "audit_bundle": {
                "current_stage": "chief_engineer_review",
                "last_successful_stage": "pm_planning",
                "failure": {
                    "code": "FACTORY_STAGE_FAILED",
                    "detail": (
                        "Chief Engineer review generated 8/9 blueprints; "
                        "signals=1; error_code=chief_engineer.llm_review_failed; "
                        "root_cause_hint=验证失败，已重试1次: No JSON object matched "
                        "chief_engineer blueprint keys: construction_plan, scope_for_apply, risk_flags"
                    ),
                },
                "director_convergence": {
                    "blocking_phase": "chief_engineer_review",
                    "missing_delivery_targets": ["director_dispatch", "quality_gate"],
                },
            },
        },
        "director_convergence": {
            "blocking_phase": "chief_engineer_review",
            "missing_delivery_targets": ["director_dispatch", "quality_gate"],
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "chief_engineer_blueprint"
    assert taxonomy["root_cause_signature"] == "chief_engineer_blueprint:llm_review_failed"
    assert record["failure_category"] == "chief_engineer_blueprint"
    assert record["goal_audit"]["failure_categories"] == {"chief_engineer_blueprint": 1}
    assert "Chief Engineer review generated 8/9 blueprints" in record["failure_evidence"][0]


def test_real_run_gate_executes_python_build_and_cli_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in sys.argv else 'ok')\n",
        encoding="utf-8",
    )
    record = {"code_files": ["main.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["artifact_landed"]["ok"] is True
    assert gate["requirements"]["environment_prepared"]["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is True
    assert gate["entrypoint"]["kind"] == "python_cli"


def test_real_run_gate_executes_cpp_multifile_cli_entrypoint(tmp_path: Path) -> None:
    engine_dir = tmp_path / "src" / "engine"
    engine_dir.mkdir(parents=True)
    (engine_dir / "generator.hpp").write_text(
        "#pragma once\n#include <string>\nnamespace moon_post { std::string build_postcard(); }\n",
        encoding="utf-8",
    )
    (engine_dir / "generator.cpp").write_text(
        '#include "generator.hpp"\n'
        "namespace moon_post {\n"
        'std::string build_postcard() { return "moon postcard stamp poem"; }\n'
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "main.cpp").write_text(
        '#include "engine/generator.hpp"\n'
        "#include <iostream>\n"
        "int main(int argc, char** argv) {\n"
        '    if (argc > 1 && std::string(argv[1]) == "--help") {\n'
        '        std::cout << "usage: moon-postcard\\n";\n'
        "        return 0;\n"
        "    }\n"
        '    std::cout << moon_post::build_postcard() << "\\n";\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    record = {"code_files": ["src/main.cpp", "src/engine/generator.cpp"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is True
    assert gate["entrypoint"]["kind"] == "cpp_cli"
    assert "src/engine/generator.cpp" in gate["entrypoint"]["compile"]["command"]


def test_real_run_gate_executes_packaged_java_cli_entrypoint(monkeypatch: Any, tmp_path: Path) -> None:
    java_dir = tmp_path / "src" / "main" / "java" / "polaris" / "factory"
    java_dir.mkdir(parents=True)
    (java_dir / "Main.java").write_text(
        "package polaris.factory;\n"
        "public final class Main {\n"
        "  public static void main(String[] args) {\n"
        '    System.out.println("rhythm monster beat pattern");\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return f"/tool/{name}" if name in {"javac", "java"} else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "ok",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["src/main/java/polaris/factory/Main.java"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is True
    assert gate["entrypoint"]["kind"] == "java_cli"
    java_commands = [command for command in commands if command and command[0] == "/tool/java"]
    assert java_commands
    assert any(command[3] == "polaris.factory.Main" for command in java_commands)


def test_real_run_gate_executes_python_unittest_suite(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in __import__('sys').argv else add(1, 2))\n",
        encoding="utf-8",
    )
    (tests_dir / "test_calculator.py").write_text(
        "from __future__ import annotations\n"
        "import unittest\n"
        "from calculator import add\n\n"
        "class CalculatorTests(unittest.TestCase):\n"
        "    def test_adds_numbers(self) -> None:\n"
        "        self.assertEqual(add(1, 2), 3)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py", "tests/test_calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert "unittest passed" in gate["requirements"]["build_test_lint_ran"]["detail"]
    assert any(command.get("runner") == "unittest" for command in gate["commands"])


def test_real_run_gate_falls_back_to_pytest_when_unittest_finds_zero_cases(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in __import__('sys').argv else add(1, 2))\n",
        encoding="utf-8",
    )
    (tests_dir / "test_calculator.py").write_text(
        "from __future__ import annotations\n"
        "from calculator import add\n\n"
        "def test_adds_numbers() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py", "tests/test_calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert "pytest passed" in gate["requirements"]["build_test_lint_ran"]["detail"]
    assert [command.get("runner") for command in gate["commands"] if command.get("runner")] == [
        "unittest",
        "pytest",
    ]


def test_real_run_gate_rejects_python_tests_that_run_zero_cases(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in __import__('sys').argv else add(1, 2))\n",
        encoding="utf-8",
    )
    (tests_dir / "test_calculator.py").write_text(
        "from __future__ import annotations\nHELPER_VALUE = 3\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py", "tests/test_calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["detail"] in {
        "python pytest discovered zero tests from generated test files",
        "python pytest failed",
    }


def test_real_run_gate_rejects_python_cli_failure_marker(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    print('FAIL: calculate(1+2) = 4 (expected 3)')\n"
        "    raise SystemExit(0)\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False
    assert gate["entrypoint"]["failure_marker"] is True
    assert gate["entrypoint"]["detail"] == "entrypoint output contained a failure marker"


def test_real_run_gate_accepts_required_arg_cli_usage_screen(tmp_path: Path) -> None:
    (tmp_path / "cli.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    if '--help' in sys.argv:\n"
        "        print('Usage: python cli.py <value>', file=sys.stderr)\n"
        "        raise SystemExit(2)\n"
        "    if len(sys.argv) < 2:\n"
        "        print('Usage: python cli.py <value>', file=sys.stderr)\n"
        "        raise SystemExit(1)\n"
        "    print(sys.argv[1])\n",
        encoding="utf-8",
    )
    record = {"code_files": ["cli.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is True
    assert gate["entrypoint"]["usage_screen"] is True


def test_real_run_gate_accepts_interactive_cli_that_starts_and_waits(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "import time\n"
        "if __name__ == '__main__':\n"
        "    if '--help' in sys.argv:\n"
        "        raise SystemExit(2)\n"
        "    print('Interactive Calculator')\n"
        "    print('>>> ', end='', flush=True)\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    record = {"code_files": ["calculator.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=2)

    # Timeout is no longer considered success
    assert gate["ok"] is False
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False
    assert gate["entrypoint"]["started"] is True
    assert gate["entrypoint"]["timeout"] is True


def test_real_run_gate_starts_static_web_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("const answer = 42;\n", encoding="utf-8")
    record = {"code_files": ["index.html", "app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "node --check passed"
    # Accept either web_static or web_playwright (Playwright is preferred when available)
    assert gate["entrypoint"]["kind"] in ("web_static", "web_playwright")


def test_static_web_smoke_fails_missing_html_entrypoint(tmp_path: Path) -> None:
    smoke = bench_gates._smoke_static_web(tmp_path, "missing.html", timeout_s=10)

    assert smoke["ok"] is False
    detail = str(smoke.get("detail") or "")
    assert "404" in detail or "HTTP status" in detail


def test_real_run_gate_does_not_fallback_after_failed_npm_script(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("const answer = 42;\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "node scripts/verify.js",
                }
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": False,
            "returncode": 1,
            "duration_s": 0.01,
            "stdout_tail": "verification failed",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.html", "app.js", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "npm run test failed"
    assert ["node", "--check", "app.js"] not in commands


def test_real_run_gate_accepts_pure_static_html_css_smoke(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="style.css"></head><body><h1>ok</h1></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "style.css").write_text("body { display: grid; }\n", encoding="utf-8")
    record = {"code_files": ["index.html", "style.css"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "static HTML/CSS entrypoint smoke passed"
    # Accept either web_static or web_playwright (Playwright is preferred when available)
    assert gate["entrypoint"]["kind"] in ("web_static", "web_playwright")


def test_real_run_gate_fails_closed_for_required_custom_verifier_when_scripts_disabled(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", raising=False)
    (tmp_path / "index.html").write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")
    (tmp_path / "style.css").write_text("body { display: grid; }\n", encoding="utf-8")
    (tmp_path / "verify.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            custom_script_enabled=True,
            required_modalities=("custom_script",),
            custom_scripts=(
                {
                    "id": "custom-smoke",
                    "path": "verify.py",
                    "modality": "custom_script",
                    "enabled": True,
                    "required": True,
                },
            ),
        )
    )
    record = {"code_files": ["index.html", "style.css"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["user_verifiers"]["ok"] is False
    assert gate["user_verifiers"][0]["required"] is True
    assert "KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED" in gate["user_verifiers"][0]["detail"]


def test_real_run_gate_accepts_required_custom_verifier_when_scripts_enabled(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    (tmp_path / "index.html").write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")
    (tmp_path / "style.css").write_text("body { display: grid; }\n", encoding="utf-8")
    (tmp_path / "verify.py").write_text(
        "from pathlib import Path\nassert '<html>' in Path('index.html').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            custom_script_enabled=True,
            required_modalities=("custom_script",),
            custom_scripts=(
                {
                    "id": "custom-smoke",
                    "path": "verify.py",
                    "modality": "custom_script",
                    "enabled": True,
                    "required": True,
                },
            ),
        )
    )
    record = {"code_files": ["index.html", "style.css"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["user_verifiers"]["ok"] is True
    assert gate["user_verifiers"][0]["ok"] is True
    assert gate["user_verifiers"][0]["required"] is True
    assert gate["user_verifiers"][0]["hash"].startswith("sha256:")


def test_real_run_gate_executes_go_build_and_cli_entrypoint(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text(
        'package main\nimport "fmt"\nfunc main() { fmt.Println("usage: app") }\n',
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/go" if name == "go" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "usage: app\n" if "run" in command else "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["main.go"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["environment_prepared"]["detail"] == "go toolchain available"
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "go vet passed"
    assert gate["entrypoint"]["kind"] == "go_cli"
    assert [command[1] for command in commands] == ["vet", "run"]
    assert not (tmp_path / "go.mod").exists()


def test_real_run_gate_reports_go_vet_failure_without_mutating_workspace(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text(
        'package main\nfunc main() { println("usage: app") }\n',
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/go" if name == "go" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        is_vet = len(command) > 1 and command[1] == "vet"
        return {
            "command": command,
            "ok": not is_vet,
            "returncode": 1 if is_vet else 0,
            "duration_s": 0.01,
            "stdout_tail": "usage: app\n" if not is_vet else "",
            "stderr_tail": "vet failed\n" if is_vet else "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["main.go"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "go vet failed"
    assert [command[1] for command in commands] == ["vet", "run"]
    assert not (tmp_path / "go.mod").exists()


def test_real_run_gate_ts_build_before_test_order(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "test": "node dist/index.js",
                    "start": "node dist/index.js",
                },
                "devDependencies": {"typescript": "^5.6.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "test" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    assert build_idx < test_idx


def test_real_run_gate_ts_build_failure_blocks_gate(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "test": "node dist/index.js",
                    "start": "node dist/index.js",
                },
                "devDependencies": {"typescript": "^5.6.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        is_build = command == ["npm", "run", "build"]
        return {
            "command": command,
            "ok": not is_build,
            "returncode": 1 if is_build else 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "TS1005: ';' expected." if is_build else "",
            "timeout": False,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert "TS1005" in gate["requirements"]["build_test_lint_ran"]["detail"]


def test_real_run_gate_non_compiled_js_can_run_test_only(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest", "start": "node app.js"}}),
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "npm run test passed"
    build_test_calls = [
        cmd for cmd in commands if cmd[0] == "npm" and cmd[1] == "run" and cmd[2] in ("test", "build", "lint", "check")
    ]
    assert len(build_test_calls) == 1
    assert build_test_calls[0][2] == "test"


def test_real_run_gate_build_failure_blocks_npm_start(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "start": "node dist/index.js",
                },
                "devDependencies": {"typescript": "^5.6.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        is_build = command == ["npm", "run", "build"]
        return {
            "command": command,
            "ok": not is_build,
            "returncode": 1 if is_build else 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "TS1005: ';' expected." if is_build else "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is False
    assert "TS1005" in gate["requirements"]["build_test_lint_ran"]["detail"]
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "start" not in script_names
    assert gate["entrypoint"]["kind"] == "npm_start"
    assert gate["entrypoint"]["ok"] is False
    assert "build did not succeed" in gate["entrypoint"]["detail"] or "TS1005" in gate["entrypoint"]["detail"]


def test_real_run_gate_build_pass_test_fail_still_attempts_npm_start(monkeypatch: Any, tmp_path: Path) -> None:
    """R128: test failure must not erase build success or block entrypoint smoke.

    r126 L1-01: tsc passed, npm start worked, but missing tests/ made the gate
    report build_test_lint fail AND entrypoint_smoke fail ("depends on build
    output but npm run test failed") — a false measurement on the rigid ruler.
    """
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc -p tsconfig.json",
                    "test": "node --test tests",
                    "start": "npm run build && node dist/main.js",
                },
                "devDependencies": {"typescript": "^5.6.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export const x = 1;\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        is_test = command == ["npm", "run", "test"]
        return {
            "command": command,
            "ok": not is_test,
            "returncode": 1 if is_test else 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "Could not find 'tests'" if is_test else "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["src/main.ts", "package.json", "tsconfig.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert "build passed" in gate["requirements"]["build_test_lint_ran"]["detail"]
    assert "test failed" in gate["requirements"]["build_test_lint_ran"]["detail"]
    script_names = [cmd[2] for cmd in commands if cmd[0] == "npm" and len(cmd) >= 3]
    assert "build" in script_names
    assert "test" in script_names
    assert "start" in script_names
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is True
    assert gate["entrypoint"]["kind"] == "npm_start"
    assert gate["entrypoint"]["ok"] is True


def test_real_run_gate_build_first_when_no_ts_but_build_is_tsc(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "test": "node dist/index.js",
                    "start": "node dist/index.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "test" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    assert build_idx < test_idx


def test_real_run_gate_build_first_when_test_references_dist(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "test": "node dist/bundle.js",
                    "start": "node dist/bundle.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "test" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    assert build_idx < test_idx


def test_real_run_gate_non_compiled_js_test_only_no_forced_build(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest", "start": "node app.js"}}),
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["build_test_lint_ran"]["detail"] == "npm run test passed"
    build_test_calls = [
        cmd for cmd in commands if cmd[0] == "npm" and cmd[1] == "run" and cmd[2] in ("test", "build", "lint", "check")
    ]
    assert len(build_test_calls) == 1
    assert build_test_calls[0][2] == "test"


class TestScriptDependsOnBuildOutput:
    """Direct tests for _script_depends_on_build_output helper."""

    def test_serve_s_dist(self) -> None:
        assert _script_depends_on_build_output({"start": "serve -s dist"}, "start") is True

    def test_node_build(self) -> None:
        assert _script_depends_on_build_output({"start": "node build"}, "start") is True

    def test_node_dot_slash_out_server(self) -> None:
        assert _script_depends_on_build_output({"start": "node ./out/server.js"}, "start") is True

    def test_node_backslash_dist_index(self) -> None:
        assert _script_depends_on_build_output({"start": "node .\\dist\\index.js"}, "start") is True

    def test_vite_preview(self) -> None:
        assert _script_depends_on_build_output({"start": "vite preview"}, "start") is True

    def test_node_flag_dir_equals_dist(self) -> None:
        assert _script_depends_on_build_output({"start": "node --dir=dist"}, "start") is True

    def test_npx_serve(self) -> None:
        assert _script_depends_on_build_output({"start": "npx serve -s dist"}, "start") is True

    def test_dist_slash_index_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node dist/index.js"}, "start") is True

    def test_build_slash_server_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node build/server.js"}, "start") is True

    def test_dot_slash_dist(self) -> None:
        assert _script_depends_on_build_output({"start": "node ./dist"}, "start") is True

    def test_outdir_flag_equals_dist(self) -> None:
        assert _script_depends_on_build_output({"build": "tsc --outDir=dist"}, "build") is True

    def test_node_scripts_build_start_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node scripts/build/start.js"}, "start") is False

    def test_node_src_build_helper_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node src/build-helper.js"}, "start") is False

    def test_node_tools_outdated_check_js(self) -> None:
        assert _script_depends_on_build_output({"start": "node tools/outdated-check.js"}, "start") is False

    def test_empty_command(self) -> None:
        assert _script_depends_on_build_output({"start": ""}, "start") is False

    def test_missing_script(self) -> None:
        assert _script_depends_on_build_output({"test": "jest"}, "start") is False

    def test_none_value(self) -> None:
        assert _script_depends_on_build_output({"start": None}, "start") is False


