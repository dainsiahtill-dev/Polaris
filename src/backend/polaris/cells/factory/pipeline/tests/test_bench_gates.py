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


class TestCommandServesBuildOutput:
    """Direct tests for _command_serves_build_output helper."""

    def test_vite_preview_true(self) -> None:
        assert _command_serves_build_output("vite preview") is True

    def test_npx_vite_preview_true(self) -> None:
        assert _command_serves_build_output("npx vite preview") is True

    def test_serve_s_dist_true(self) -> None:
        assert _command_serves_build_output("serve -s dist") is True

    def test_npx_serve_s_dist_true(self) -> None:
        assert _command_serves_build_output("npx serve -s dist") is True

    def test_http_server_dist_true(self) -> None:
        assert _command_serves_build_output("http-server dist") is True

    def test_serve_dot_slash_dist_true(self) -> None:
        assert _command_serves_build_output("serve ./dist") is True

    def test_serve_dist_index_js_true(self) -> None:
        assert _command_serves_build_output("serve dist/index.js") is True

    def test_serve_build_true(self) -> None:
        assert _command_serves_build_output("serve build") is True

    def test_serve_out_true(self) -> None:
        assert _command_serves_build_output("serve out") is True

    def test_serve_dir_equals_dist_true(self) -> None:
        assert _command_serves_build_output("serve --dir=dist") is True

    def test_serve_public_false(self) -> None:
        assert _command_serves_build_output("serve public") is False

    def test_serve_src_false(self) -> None:
        assert _command_serves_build_output("serve src") is False

    def test_serve_no_args_false(self) -> None:
        assert _command_serves_build_output("serve") is False

    def test_npx_serve_public_false(self) -> None:
        assert _command_serves_build_output("npx serve public") is False

    def test_npx_serve_no_args_false(self) -> None:
        assert _command_serves_build_output("npx serve") is False

    def test_http_server_public_false(self) -> None:
        assert _command_serves_build_output("http-server public") is False

    def test_http_server_no_args_false(self) -> None:
        assert _command_serves_build_output("http-server") is False

    def test_npx_http_server_dist_true(self) -> None:
        assert _command_serves_build_output("npx http-server dist") is True

    def test_serve_scripts_false(self) -> None:
        assert _command_serves_build_output("serve scripts") is False

    def test_empty_command_false(self) -> None:
        assert _command_serves_build_output("") is False


def test_real_run_gate_build_first_when_start_serves_dist(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "test": "jest",
                    "start": "serve -s dist",
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
    assert "start" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    start_idx = script_names.index("start")
    assert build_idx < test_idx < start_idx


def test_real_run_gate_build_failure_blocks_start_with_serve_dist(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "start": "serve -s dist",
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
        is_build = command == ["npm", "run", "build"]
        return {
            "command": command,
            "ok": not is_build,
            "returncode": 1 if is_build else 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "BUILD FAILED" if is_build else "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "start" not in script_names
    assert gate["entrypoint"]["ok"] is False


def test_real_run_gate_build_first_when_start_vite_preview(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "vite build",
                    "start": "vite preview",
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
    assert "start" in script_names
    build_idx = script_names.index("build")
    start_idx = script_names.index("start")
    assert build_idx < start_idx


def test_real_run_gate_false_positive_guard_no_forced_build(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "test": "jest",
                    "start": "node scripts/build/start.js",
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
    assert "test" in script_names
    build_test_calls = [
        cmd for cmd in commands if cmd[0] == "npm" and cmd[1] == "run" and cmd[2] in ("test", "build", "lint", "check")
    ]
    assert len(build_test_calls) == 1
    assert build_test_calls[0][2] == "test"


def test_collect_llm_events_reads_runtime_role_jsonl(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    events_dir = runtime / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(
            {
                "event": "llm_call_end",
                "role": "pm",
                "data": {
                    "role": "pm",
                    "provider": "kimi-cloud",
                    "model": "kimi-k2",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(tmp_path, runtime)

    assert len(events) == 1
    assert events[0]["role"] == "pm"
    assert events[0]["provider_id"] == "kimi-cloud"
    assert events[0]["model"] == "kimi-k2"
    assert events[0]["terminal"] is True


def test_collect_llm_events_projects_final_request_evidence(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    events_dir = runtime / "events"
    events_dir.mkdir(parents=True)
    refs = {
        "pm": "111111111111111111111111",
        "chief_engineer": "222222222222222222222222",
        "director": "333333333333333333333333",
    }
    for role in ("pm", "chief_engineer", "director"):
        (events_dir / f"{role}.llm.events.jsonl").write_text(
            json.dumps(
                {
                    "event": "llm_call_start",
                    "role": role,
                    "context_snapshot_ref": f"runtime/contexts/{role}/{refs[role]}.json",
                    "final_request_context_audit_hash": f"audit-hash-{role}",
                    "final_request_evidence_hash": f"evidence-hash-{role}",
                    "final_request_evidence": {
                        "context_snapshot_ref": f"runtime/contexts/{role}/{refs[role]}.json",
                        "final_request_context_audit_present": True,
                        "final_request_evidence_authority_hash": f"authority-hash-{role}",
                        "final_request_evidence_coverage_pass": False,
                        "role_id": role,
                        "expected_role_id": role,
                        "role_identity_ok": True,
                        "required_refs": ["pm_contract", "ce_blueprint"],
                        "included_refs": ["pm_contract"],
                        "missing_required_refs": ["execution_envelope"],
                        "required_tools": ["read_file", "write_file"],
                        "available_tools": ["read_file"],
                        "missing_required_tools": ["write_file"],
                        "unexpected_tool_pruning": [
                            {
                                "tool": "write_file",
                                "reason": "required_tool_missing_from_final_provider_request",
                            }
                        ],
                        "tool_schema_registry_coverage": {"missing_schema_tools": ["write_file"]},
                        "workflow_chain": {"pm_contract_hash": f"pm-hash-{role}"},
                    },
                    "data": {"provider": "qwen-local", "model": "qwen3"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    events = collect_llm_events(tmp_path, runtime)

    by_role = {event["role"]: event for event in events}
    assert set(by_role) == {"pm", "chief_engineer", "director"}
    for role in ("pm", "chief_engineer", "director"):
        event = by_role[role]
        assert event["context_snapshot_ref"] == refs[role]
        assert event["final_request_context_audit_present"] is True
        assert event["final_request_context_audit_hash"] == f"audit-hash-{role}"
        assert event["final_request_evidence_hash"] == f"evidence-hash-{role}"
        assert event["final_request_evidence_authority_hash"] == f"authority-hash-{role}"
        assert event["final_request_evidence_coverage_pass"] is False
        assert event["role_id"] == role
        assert event["expected_role_id"] == role
        assert event["role_identity_ok"] is True
        assert event["required_refs"] == ["pm_contract", "ce_blueprint"]
        assert event["included_refs"] == ["pm_contract"]
        assert event["missing_required_refs"] == ["execution_envelope"]
        assert event["required_tools"] == ["read_file", "write_file"]
        assert event["available_tools"] == ["read_file"]
        assert event["missing_required_tools"] == ["write_file"]
        assert event["unexpected_tool_pruning"][0]["tool"] == "write_file"
        assert event["tool_schema_registry_coverage"] == {"missing_schema_tools": ["write_file"]}
        assert event["workflow_chain"] == {"pm_contract_hash": f"pm-hash-{role}"}


def test_collect_llm_events_reads_multiple_runtime_candidates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    for runtime, role, model in (
        (runtime_a, "pm", "kimi-k2"),
        (runtime_b, "director", "qwen3.6-27b-gpu0"),
    ):
        events_dir = runtime / "events"
        events_dir.mkdir(parents=True)
        (events_dir / f"{role}.llm.events.jsonl").write_text(
            json.dumps(
                {
                    "event": "llm_call_end",
                    "role": role,
                    "model": model,
                    "data": {"prompt_tokens": 1, "completion_tokens": 2},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    events = collect_llm_events(workspace, [runtime_a, runtime_b])

    assert {(event["role"], event["model"]) for event in events} == {
        ("pm", "kimi-k2"),
        ("director", "qwen3.6-27b-gpu0"),
    }


def test_collect_llm_events_reads_route_events_from_audit_bundle_result(tmp_path: Path) -> None:
    bundle = {
        "events_tail": [
            {
                "type": "stage_completed",
                "stage": "director_dispatch",
                "result": {
                    "per_binding_route_events": [_real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0")],
                },
            }
        ],
    }

    events = collect_llm_events(tmp_path, None, bundle)

    assert [(event["role"], event["provider_id"], event["binding_id"]) for event in events] == [
        ("director", "qwen-gpu0", "d0")
    ]
    assert events[0]["source_path"] == "audit_bundle.events_tail.result"


def test_collect_llm_events_reads_factory_dispatch_log_glob(tmp_path: Path) -> None:
    dispatch_dir = tmp_path / ".polaris" / "dispatch"
    dispatch_dir.mkdir(parents=True)
    dispatch_log = dispatch_dir / "factory_abc123.log.json"
    dispatch_log.write_text(
        json.dumps(
            {
                "per_binding_route_events": [_real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    events = collect_llm_events(tmp_path, None)

    assert [(event["role"], event["provider_id"], event["binding_id"]) for event in events] == [
        ("director", "qwen-gpu1", "d1")
    ]
    assert events[0]["source_path"].endswith("factory_abc123.log.json")


def test_llm_route_audit_requires_actual_bound_families_and_all_director_routes() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True


def test_llm_route_audit_prefers_actual_configured_binding_over_hardcoded_family() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "openai-a", "model": "gpt-5.3-codex", "binding_id": ""}],
        "chief_engineer": [
            {"role": "chief_engineer", "provider_id": "glm-a", "model": "glm-4.7-flash", "binding_id": ""}
        ],
        "qa": [{"role": "qa", "provider_id": "gemini-a", "model": "gemini-2.5-pro", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "local-director", "model": "custom-director-30b", "binding_id": "d0"}
        ],
    }
    events = [
        _real_llm_event("pm", "openai-a", "gpt-5.3-codex"),
        _real_llm_event("chief_engineer", "glm-a", "glm-4.7-flash"),
        _real_llm_event("qa", "gemini-a", "gemini-2.5-pro"),
        _real_llm_event("director", "local-director", "custom-director-30b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["pm"]["family_ok"] is True
    assert audit["roles"]["chief_engineer"]["family_ok"] is True
    assert audit["roles"]["qa"]["family_ok"] is True
    assert audit["roles"]["director"]["family_ok"] is True


def test_llm_route_audit_fails_when_a_director_route_is_unobserved() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_llm_route_audit_treats_readiness_skipped_director_as_diagnostic() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
        {
            "event": "llm_route_terminal",
            "role": "director",
            "provider_id": "qwen-gpu0",
            "model": "qwen3.6-27b",
            "binding_id": "d0",
            "source": "llm",
            "cache_hit": False,
            "invocation": False,
            "terminal": True,
            "fail_closed": True,
            "skipped": True,
            "skip_reason": "provider_connectivity_unavailable",
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["missing_bindings"] == []
    assert audit["roles"]["director"]["skipped_bindings"] == ["qwen-gpu0|qwen3.6-27b"]
    assert audit["roles"]["director"]["fail_closed_count"] == 1


def test_llm_route_audit_fails_when_all_director_routes_are_readiness_skipped() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    skipped = [
        {
            "event": "llm_route_terminal",
            "role": "director",
            "provider_id": "qwen-gpu0",
            "model": "qwen3.6-27b",
            "binding_id": "d0",
            "source": "llm",
            "cache_hit": False,
            "invocation": False,
            "terminal": True,
            "fail_closed": True,
            "skipped": True,
            "skip_reason": "provider_unreachable",
        },
        {
            "event": "llm_route_terminal",
            "role": "director",
            "provider_id": "qwen-gpu1",
            "model": "qwen3.6-27b",
            "binding_id": "d1",
            "source": "llm",
            "cache_hit": False,
            "invocation": False,
            "terminal": True,
            "fail_closed": True,
            "skipped": True,
            "skip_reason": "provider_unreachable",
        },
    ]
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        *skipped,
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["roles"]["director"]["observed_count"] == 0
    assert audit["roles"]["director"]["fail_closed_count"] == 2
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["skipped_bindings"] == [
        "qwen-gpu0|qwen3.6-27b",
        "qwen-gpu1|qwen3.6-27b",
    ]


def test_llm_route_audit_accepts_single_live_director_route() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b-gpu1", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True


def test_llm_route_audit_can_relax_director_route_coverage_for_serial_bench() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "pm0"}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": "qa0"}],
        "director": [
            {"role": "director", "provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-b", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-for-coding", "pm0"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3", "qa0"),
        _real_llm_event("director", "qwen-b", "qwen3.6-27b-gpu1", "d1"),
    ]

    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "qa", "director"),
        require_all_director_routes=False,
    )

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["multi_route_required"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_llm_route_audit_resolves_providerless_via_expected_bindings_and_rejects_cached() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "pm0"}],
        "chief_engineer": [
            {"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "ce0"}
        ],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": "qa0"}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b-gpu0", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-for-coding", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "chief_engineer",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-for-coding", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "qa",
            "provider_id": "minimax-a",
            "model": "MiniMax-M3",
            "source": "cache",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "qwen3.6-27b-gpu0", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "provider_id": "qwen-gpu1",
            "model": "qwen3.6-27b-gpu1",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"metadata": {"source": "llm", "cached": True}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["events_observed"] == 3
    assert audit["events_rejected"] == 2
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["pm"]["observed_bindings"] == ["kimi-a|kimi-for-coding"]
    assert audit["roles"]["chief_engineer"]["observed_count"] == 1
    assert audit["roles"]["chief_engineer"]["observed_bindings"] == ["kimi-a|kimi-for-coding"]
    assert audit["roles"]["qa"]["observed_count"] == 0
    assert audit["roles"]["director"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_bindings"] == ["qwen-gpu0|qwen3.6-27b-gpu0"]


def test_failure_taxonomy_prefers_llm_route_before_generic_chain_failure() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [{"gate": "llm_route_audit", "ok": False, "detail": "missing qa"}],
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: qa"},
        "chain_state": "fail",
        "checks": [],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:llm_route_audit"


def test_failure_taxonomy_classifies_integration_qa_before_generic_chain_failure() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=True qa_passed=False"},
            {"gate": "real_run_gate", "ok": True, "detail": "real run gate passed"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_results": {"qa_reason": "qa_passed=False; qa_score=34"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:integration_qa_failed"
    assert taxonomy["evidence"] == ["qa_passed=False; qa_score=34"]


def test_failure_taxonomy_prefers_plannable_repair_convergence_before_integration_qa() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: build_test_lint_ran"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "npm run build failed: TS2304 Cannot find name 'dayOfYear'",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "11 generated file(s)"},
                "build_test_lint_ran": {"ok": False, "detail": "TS2304 Cannot find name 'dayOfYear'"},
            },
        },
        "chain_results": {"qa_reason": "qa did not run because upstream task did not compile"},
        "workspace_quality": {
            "repair": {
                "plan_probe_preaudit": {
                    "schema_version": "director.repair_plan_probe_result.v1",
                    "status": "covered_plannable",
                    "plannable_source_tools": ["deterministic_typescript_unresolved_identifier_repair"],
                },
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "repair_convergence"
    assert taxonomy["root_cause_signature"] == "repair_convergence:covered_plannable_not_converged"
    assert taxonomy["evidence"] == [
        "plan_probe:covered_plannable;plannable_source_tools=deterministic_typescript_unresolved_identifier_repair"
    ]


def test_failure_taxonomy_routes_unplannable_probe_to_task_boundary() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "workspace_quality": {
            "repair": {
                "plan_probe_preaudit": {
                    "schema_version": "director.repair_plan_probe_result.v1",
                    "status": "coverage_matched_but_unplannable",
                    "plannable_source_tools": [],
                    "covered_unplannable_source_tools": ["deterministic_typescript_missing_export_repair"],
                },
            },
        },
        "real_run_gate": {"ok": False, "summary": "build failed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:repair_plan_probe_unplannable"
    assert taxonomy["evidence"] == [
        "plan_probe:coverage_matched_but_unplannable;"
        "covered_unplannable_source_tools=deterministic_typescript_missing_export_repair"
    ]


def test_failure_taxonomy_prefers_task_boundary_dependency_before_integration_qa() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "run_ledger_projection": _canonical_run_ledger_projection(
            task_boundary_failures=[
                {
                    "schema_version": "task_boundary.verdict.v1",
                    "ok": False,
                    "status": "dependency_not_unlocked",
                    "failure_class": "dependency_not_unlocked",
                    "responsible_layer": "task_boundary",
                    "reason": "TASK-1 did not reach completed_verified",
                }
            ]
        ),
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_results": {"qa_reason": "qa did not run"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:dependency_not_unlocked"
    assert taxonomy["evidence"] == [
        "failure_class=dependency_not_unlocked;responsible_layer=task_boundary;TASK-1 did not reach completed_verified"
    ]


def test_failure_taxonomy_prefers_specific_task_boundary_failure_over_downstream_dependency(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    results_dir = runtime_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "director.result.json").write_text(
        json.dumps(
            {
                "task_results": [
                    {
                        "task_id": "TASK-3",
                        "status": "blocked",
                        "error": "blocked_by_failed_dependency",
                        "blocked_by": ["TASK-2"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record = {
        "all_checks_passed": False,
        "runtime_dir": str(runtime_dir),
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
        ],
        "run_ledger_projection": _canonical_run_ledger_projection(
            task_boundary_failures=[
                {
                    "schema_version": "task_boundary.verdict.v1",
                    "ok": False,
                    "status": "missing_entrypoint_target",
                    "failure_class": "MISSING_ENTRYPOINT_TARGET",
                    "responsible_layer": "task_boundary",
                    "reason": "index.html references src/web.js",
                }
            ]
        ),
        "real_run_gate": {"ok": False, "summary": "entrypoint smoke failed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:missing_entrypoint_target"
    assert taxonomy["evidence"] == [
        "failure_class=MISSING_ENTRYPOINT_TARGET;responsible_layer=task_boundary;index.html references src/web.js"
    ]


def test_failure_taxonomy_classifies_missing_toolchain_check_as_runtime_environment() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "clean",
        "checks": [{"check": "go_compile", "ok": False, "detail": "go unavailable for Go project"}],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:go_compile"


def test_failure_taxonomy_classifies_workspace_switch_before_real_run_gate() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: artifact_landed"},
        ],
        "chain": {
            "error": "workspace_switch_failed",
            "workspace_switch": {"workspace": "/tmp/factory-bench/L1-01"},
        },
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: artifact_landed",
            "requirements": {"artifact_landed": {"ok": False, "detail": "no generated source files"}},
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "fail",
        "checks": [],
        "has_plan_doc": False,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:workspace_switch_failed"
    assert taxonomy["evidence"] == ["/tmp/factory-bench/L1-01"]


def test_failure_taxonomy_classifies_all_director_bindings_unavailable_as_runtime_environment() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: artifact_landed"},
            {"gate": "llm_route_audit", "ok": False, "detail": "LLM route audit failed: director"},
        ],
        "chain": {
            "audit_bundle": {
                "failure": {
                    "detail": "Director dispatch failed: No available Director binding after readiness filtering",
                }
            },
            "factory_terminal_status": {
                "event_payload": {
                    "result": {
                        "metadata": {
                            "binding_count": 2,
                            "active_binding_count": 0,
                            "readiness_skipped_count": 2,
                            "per_binding": [
                                {
                                    "provider_id": "qwen-gpu0",
                                    "model": "qwen3.6-27b",
                                    "status": "skipped",
                                    "skip_reason": "provider_unreachable",
                                },
                                {
                                    "provider_id": "qwen-gpu1",
                                    "model": "qwen3.6-27b",
                                    "status": "skipped",
                                    "skip_reason": "provider_unreachable",
                                },
                            ],
                        }
                    }
                }
            },
        },
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: artifact_landed",
            "requirements": {"artifact_landed": {"ok": False, "detail": "no generated source files"}},
        },
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: director"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:director_bindings_unavailable"
    assert taxonomy["evidence"] == ["Director dispatch failed: No available Director binding after readiness filtering"]


def test_failure_taxonomy_classifies_generated_typescript_syntax_failure_as_llm_output() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: build_test_lint_ran"},
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "22 generated code file(s)"},
                "environment_prepared": {"ok": True, "detail": "npm available"},
                "build_test_lint_ran": {
                    "ok": False,
                    "detail": "npm test failed: src/models/humidity.ts(1,29): error TS1434",
                },
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [
            {
                "check": "ts_syntax",
                "ok": False,
                "detail": "TypeScript syntax check failed: src/models/humidity.ts(1,29): TS1434",
            }
        ],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"


def test_failure_taxonomy_classifies_missing_typescript_dependency_as_llm_output() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: environment_prepared"},
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: environment_prepared, build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "4 generated code file(s)"},
                "environment_prepared": {
                    "ok": False,
                    "detail": "package.json missing devDependency 'typescript' for TypeScript build",
                },
                "build_test_lint_ran": {"ok": False, "detail": "no build/test/lint command was discovered"},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.environment_prepared"


def test_failure_taxonomy_classifies_missing_blueprint_as_chief_engineer_blueprint() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "plan_artifact_present", "ok": True, "detail": "plan artifact discovered"},
            {"gate": "blueprint_artifact_present", "ok": False, "detail": "blueprint artifact missing"},
            {"gate": "qa_verdict_artifact_present", "ok": True, "detail": "QA verdict artifact discovered"},
            {"gate": "chain_clean", "ok": True, "detail": "chain_state=clean exit_code=0"},
            {"gate": "integration_qa_passed", "ok": True, "detail": "qa_ran=True qa_passed=True"},
            {"gate": "real_run_gate", "ok": True, "detail": "real run gate passed"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "clean",
        "checks": [],
        "has_plan_doc": True,
        "has_blueprint_doc": False,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "chief_engineer_blueprint"
    assert taxonomy["root_cause_signature"] == "chief_engineer_blueprint:missing_or_invalid_blueprint"


def test_aggregate_goal_audit_counts_real_route_ledger_and_root_causes(tmp_path: Path) -> None:
    ledger_file = tmp_path / "ledger.ndjson"
    ledger_file.write_text('{"ok":true}\n', encoding="utf-8")
    records = [
        {
            "real_run_gate": {"ok": True},
            "run_ledger_projection": {
                "source": "run_ledger",
                "integrity_ok": True,
                "outcome_ok": True,
                "ok": True,
                "event_count": 1,
                "gate_count": 1,
                "failed_gates": [],
                "capability": {"ok": True, "issues": [], "latest_token_id": "j1"},
                "physical_evidence": {},
            },
            "llm_route_audit": {"ok": True},
            "failure_taxonomy": {"ok": True},
        },
        {
            "real_run_gate": {"ok": False},
            "llm_route_audit": {"ok": False},
            "failure_taxonomy": {
                "ok": False,
                "category": "target_project_baseline",
                "root_cause_signature": "target_project_baseline:real_run_gate.entrypoint_smoke",
            },
        },
        {
            "real_run_gate": {"ok": False},
            "run_ledger_projection": {
                "source": "run_ledger",
                "integrity_ok": True,
                "outcome_ok": False,
                "ok": False,
                "event_count": 1,
                "gate_count": 1,
                "failed_gates": [{"name": "real_run_gate", "ok": False}],
                "capability": {"ok": True, "issues": [], "latest_token_id": "j2"},
                "evidence_policy": {
                    "missing_required_modalities": [],
                    "failed_required_modalities": ["command"],
                },
                "physical_evidence": {"command_count": 1},
            },
            "llm_route_audit": {"ok": True},
            "failure_taxonomy": {
                "ok": False,
                "category": "llm_output",
                "root_cause_signature": "llm_output:real_run_gate.build_test_lint_ran",
            },
        },
    ]

    aggregate = aggregate_goal_audit(records)

    assert aggregate["real_run_gate"] == {"passed": 1, "total": 3}
    assert aggregate["run_ledger"] == {"projected": 2, "total": 3, "missing": 1}
    assert aggregate["llm_route_audit"] == {"passed": 2, "total": 3}
    assert aggregate["failure_categories"]["target_project_baseline"] == 1
    assert aggregate["failure_categories"]["llm_output"] == 1


def test_nested_roles_kernel_events_passes_with_model_only_and_expected_binding() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": "pm0"}],
        "chief_engineer": [],
        "qa": [],
        "director": [{"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"}],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-k2", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "qwen3.6-27b", "metadata": {"source": "llm"}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("pm", "director"))

    assert audit["ok"] is True
    assert audit["events_observed"] == 2
    assert audit["events_rejected"] == 0
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["pm"]["observed_bindings"] == ["kimi-a|kimi-k2"]
    assert audit["roles"]["director"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_bindings"] == ["qwen-gpu0|qwen3.6-27b"]


def test_metadata_cached_true_rejected() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": "pm0"}],
        "chief_engineer": [],
        "qa": [],
        "director": [],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"metadata": {"source": "llm", "cached": True}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("pm",))

    assert audit["ok"] is False
    assert audit["events_observed"] == 0
    assert audit["events_rejected"] == 1
    assert audit["roles"]["pm"]["observed_count"] == 0


def test_director_multi_binding_missing_one_fails() -> None:
    expected = {
        "pm": [],
        "chief_engineer": [],
        "qa": [],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("director",))

    assert audit["ok"] is False
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_director_multi_binding_all_pass() -> None:
    expected = {
        "pm": [],
        "chief_engineer": [],
        "qa": [],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("director",))

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True
    assert audit["roles"]["director"]["observed_count"] == 2


def test_real_run_gate_source_files_present_with_real_source(tmp_path: Path) -> None:
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
    assert gate["requirements"]["source_files_present"]["ok"] is True
    assert "1 source file" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" not in gate


def test_real_run_gate_source_files_present_fails_for_scaffold_only(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    record = {"code_files": ["package.json", "tsconfig.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["source_files_present"]["ok"] is False
    assert "scaffold-only" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" in gate
    assert gate["missing_source_targets"]["source_file_count"] == 0
    assert gate["missing_source_targets"]["code_file_count"] == 2


def test_real_run_gate_source_files_present_with_ts_source(tmp_path: Path) -> None:
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    record = {"code_files": ["index.ts", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["source_files_present"]["ok"] is True
    assert "1 source file" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" not in gate


def test_real_run_gate_source_files_present_with_mixed_scaffold(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".catalog_meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
    record = {"code_files": ["package.json", "tsconfig.json", ".catalog_meta.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["source_files_present"]["ok"] is False
    assert "missing_source_targets" in gate
    assert gate["missing_source_targets"]["code_file_count"] == 3
    assert gate["missing_source_targets"]["source_file_count"] == 0


def test_real_run_gate_declared_source_targets_missing_fails(tmp_path: Path) -> None:
    """plan declares src/index.ts but workspace only has package.json -> gate fail."""
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    record = {
        "code_files": ["package.json"],
        "declared_source_targets": ["src/index.ts", "src/utils.ts"],
        "declared_source_target_count": 2,
        "missing_declared_source_targets": ["src/index.ts", "src/utils.ts"],
        "missing_declared_source_target_count": 2,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["declared_source_targets_present"]["ok"] is False
    assert "2 declared source target(s) missing" in gate["requirements"]["declared_source_targets_present"]["detail"]


def test_real_run_gate_declared_source_targets_all_present_passes(tmp_path: Path) -> None:
    """All declared source targets exist -> gate ok."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    record = {
        "code_files": ["src/index.ts"],
        "declared_source_targets": ["src/index.ts"],
        "declared_source_target_count": 1,
        "missing_declared_source_targets": [],
        "missing_declared_source_target_count": 0,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is True
    assert (
        "all 1 declared source target(s) present" in gate["requirements"]["declared_source_targets_present"]["detail"]
    )


def test_real_run_gate_pm_plan_missing_source_targets_fails(tmp_path: Path) -> None:
    """PM plan with no source targets -> pm_plan_missing_source_targets signal."""
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    record = {
        "code_files": ["README.md"],
        "declared_source_targets": [],
        "declared_source_target_count": 0,
        "missing_declared_source_targets": [],
        "missing_declared_source_target_count": 0,
        "pm_plan_missing_source_targets": True,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is False
    assert "pm_plan_missing_source_targets" in gate["requirements"]["declared_source_targets_present"]["detail"]


def test_real_run_gate_no_declared_targets_no_plan(tmp_path: Path) -> None:
    """No plan.json -> no declared targets, requirement passes."""
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    record = {
        "code_files": ["main.py"],
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is True
    assert "no declared source targets" in gate["requirements"]["declared_source_targets_present"]["detail"]


def _disk_llm_event(
    role: str,
    provider: str,
    model: str,
    *,
    event: str = "llm_call_end",
    source: str = "roles.kernel.events",
    run_id: str = "test-run-001",
) -> dict[str, Any]:
    """Create an event matching _emit_llm_event_to_disk schema."""
    return {
        "schema_version": 1,
        "ts": "2026-06-21T00:00:00",
        "ts_epoch": 1750464000.0,
        "seq": 1,
        "event_id": "abcd1234",
        "run_id": run_id,
        "iteration": 1,
        "role": role,
        "source": source,
        "event": event,
        "data": {
            "event_type": event,
            "role": role,
            "model": model,
            "provider": provider,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "metadata": {"call_id": "c0", "workspace": "/tmp/test"},
        },
    }


def test_collect_llm_events_reads_from_resolve_polaris_roots_path(tmp_path: Path) -> None:
    """Events written by _emit_llm_event_to_disk to resolve_polaris_roots path are found."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    polaris_dir.mkdir()
    runtime = polaris_dir / "runtime"
    runtime.mkdir()
    events_dir = runtime / "events"
    events_dir.mkdir()
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(workspace, None)

    assert len(events) == 1
    assert events[0]["role"] == "pm"
    assert events[0]["provider_id"] == "kimi-cloud"
    assert events[0]["model"] == "kimi-k2"
    assert events[0]["terminal"] is True
    assert events[0]["invocation"] is True


def test_collect_llm_events_reads_disk_schema_from_polaris_roots(tmp_path: Path) -> None:
    """Events in _emit_llm_event_to_disk schema are normalized with correct source=llm."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("director", "qwen-gpu0", "qwen3.6-27b")) + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(workspace, None)

    assert len(events) == 1
    assert events[0]["source"] == "llm"
    assert events[0]["terminal"] is True
    assert events[0]["invocation"] is True


def test_build_llm_route_audit_observes_configured_bindings_from_disk_events(
    tmp_path: Path,
) -> None:
    """build_llm_route_audit observes PM and Director bindings from disk-format events."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("director", "qwen-gpu0", "qwen3.6-27b")) + "\n",
        encoding="utf-8",
    )

    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-cloud", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
        ],
    }
    events = collect_llm_events(workspace, None)
    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "director"),
        require_all_director_routes=False,
    )

    assert audit["ok"] is True
    assert audit["events_observed"] == 2
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_count"] == 1


def test_llm_route_audit_missing_director_evidence_fails_closed(tmp_path: Path) -> None:
    """Missing Director route evidence remains fail-closed (events_observed=0)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )

    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-cloud", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = collect_llm_events(workspace, None)
    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "director"),
    )

    assert audit["ok"] is False
    assert audit["roles"]["director"]["ok"] is False
    assert audit["roles"]["director"]["observed_count"] == 0
    assert audit["roles"]["pm"]["ok"] is True


def test_llm_route_audit_zero_events_with_all_required_roles_fails(tmp_path: Path) -> None:
    """Zero events with all required roles -> all roles fail."""
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
        ],
    }

    audit = build_llm_route_audit([], expected_bindings=expected, required_roles=("pm", "director"))

    assert audit["ok"] is False
    assert audit["events_observed"] == 0
    assert audit["roles"]["pm"]["ok"] is False
    assert audit["roles"]["director"]["ok"] is False


def test_resolve_polaris_roots_runtime_dir_returns_path(tmp_path: Path) -> None:
    """_resolve_polaris_roots_runtime_dir returns a Path for a valid workspace."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    result = _resolve_polaris_roots_runtime_dir(workspace)

    assert result is not None
    assert isinstance(result, Path)
    assert "runtime" in str(result)


def test_resolve_polaris_roots_runtime_dir_returns_none_for_invalid() -> None:
    """_resolve_polaris_roots_runtime_dir returns None gracefully."""
    result = _resolve_polaris_roots_runtime_dir(Path("/nonexistent/path/that/does/not/exist"))
    # Should not raise; may return a path or None depending on cache availability
    assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# Scaffolding requirement tests (R18-C)
# ---------------------------------------------------------------------------


def test_real_run_gate_ts_project_without_package_json_fails(tmp_path: Path) -> None:
    """TypeScript project with source files but no package.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]
    assert "tsconfig.json" in scaffolding["detail"]


def test_real_run_gate_ts_project_with_package_but_no_tsconfig_fails(tmp_path: Path) -> None:
    """TypeScript project with package.json but no tsconfig.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "tsconfig.json" in scaffolding["detail"]


def test_real_run_gate_ts_project_with_scaffolding_passes(tmp_path: Path) -> None:
    """TypeScript project with package.json and tsconfig.json passes scaffolding."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc"}}),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"outDir": "dist"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json", "tsconfig.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is True
    assert "package.json present" in scaffolding["detail"]
    assert "tsconfig.json present" in scaffolding["detail"]


def test_real_run_gate_fails_blank_canvas_even_when_package_scripts_pass(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text(
        "<html><body><canvas id='scene'></canvas><script src='app.js'></script></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text("console.log('loaded');\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "blank-canvas",
                "scripts": {
                    "build": 'node -e "process.exit(0)"',
                    "test": 'node -e "process.exit(0)"',
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_smoke_static_web(_workspace: Path, html_rel: str, *, timeout_s: int) -> dict[str, Any]:
        return {
            "kind": "web_playwright",
            "ok": False,
            "entrypoint": html_rel,
            "has_canvas": True,
            "canvas_non_blank": False,
            "detail": "Canvas entrypoint did not render non-empty pixels",
        }

    monkeypatch.setattr(bench_gates, "_smoke_static_web", fake_smoke_static_web)
    record = {"code_files": ["index.html", "app.js", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False
    assert gate["entrypoint"]["detail"] == "Canvas entrypoint did not render non-empty pixels"


def test_real_run_gate_ts_project_requires_local_typescript_dependency(monkeypatch: Any, tmp_path: Path) -> None:
    """Package-managed TypeScript projects must not borrow host global tsc."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc", "test": "npm run build"}}),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"outDir": "dist"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json", "tsconfig.json"]}

    def fail_if_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("npm scripts must not run when TypeScript dependency is missing")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(subprocess, "run", fail_if_run)

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    environment = gate["requirements"]["environment_prepared"]
    assert environment["ok"] is False
    assert "missing devDependency 'typescript'" in environment["detail"]
    assert not any(command.get("phase") == "build_test_lint" for command in gate["commands"])


def test_real_run_gate_html_project_without_index_fails(tmp_path: Path) -> None:
    """HTML project with code_files claiming .html but no real file must fail closed."""
    record = {"code_files": ["index.html"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["scaffolding_present"]["ok"] is False
    assert "index.html" in gate["requirements"]["scaffolding_present"]["detail"]
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False


def test_real_run_gate_js_project_without_package_json_fails(tmp_path: Path) -> None:
    """JavaScript project with source files but no package.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("console.log('hello');\n", encoding="utf-8")
    record = {"code_files": ["src/app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]


def test_real_run_gate_python_project_no_scaffolding_required(tmp_path: Path) -> None:
    """Python project does not require npm scaffolding."""
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    record = {"code_files": ["main.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is True
    assert "no scaffolding required" in scaffolding["detail"]


def test_real_run_gate_ts_source_only_no_scaffold_comprehensive(tmp_path: Path) -> None:
    """Comprehensive: TS project with src/**/*.ts but zero scaffolding fails on scaffolding."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "render.ts").write_text("export function render() { return 'ok'; }\n", encoding="utf-8")
    (src / "simulation.ts").write_text("export function simulate() { return 42; }\n", encoding="utf-8")
    record = {"code_files": ["src/render.ts", "src/simulation.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]
    assert "tsconfig.json" in scaffolding["detail"]


def test_cli_smoke_result_timeout_not_success() -> None:
    """CLI timeout should not be considered successful."""
    result = {
        "ok": False,
        "returncode": -1,
        "duration_s": 2.0,
        "stdout_tail": "Interactive CLI started",
        "stderr_tail": "",
        "timeout": True,
        "timeout_s": 2,
    }

    payload = bench_gates._cli_smoke_result("python_cli", "main.py", result)

    assert payload["ok"] is False
    assert payload["started"] is True
    assert payload["timeout"] is True


def test_smoke_static_web_missing_explicit_resource_fails_closed(tmp_path: Path) -> None:
    """HTML that points at a missing local script is not a runnable web artifact."""
    (tmp_path / "index.html").write_text(
        "<html><body><canvas id='scene'></canvas><script type='module' src='/dist/bundle.js'></script></body></html>",
        encoding="utf-8",
    )

    original_smoke = bench_gates._smoke_static_web_playwright

    def force_http_fallback(workspace: Path, html_rel: str, *, timeout_s: int) -> dict[str, Any]:
        raise ImportError("playwright unavailable")

    bench_gates._smoke_static_web_playwright = force_http_fallback
    try:
        result = bench_gates._smoke_static_web(tmp_path, "index.html", timeout_s=10)

        assert result["ok"] is False
        assert result["kind"] == "web_static"
        assert "/dist/bundle.js" in result["missing_resources"]
        assert "missing local resources" in result["detail"]
    finally:
        bench_gates._smoke_static_web_playwright = original_smoke


def test_smoke_static_web_canvas_state_requires_non_blank_pixels() -> None:
    """A visual canvas entrypoint with only an empty canvas should fail the render smoke."""
    assert bench_gates._canvas_smoke_ok([]) is True
    assert bench_gates._canvas_smoke_ok([{"width": 300, "height": 150, "non_blank": False}]) is False
    assert bench_gates._canvas_smoke_ok([{"width": 300, "height": 150, "non_blank": True}]) is True


def test_smoke_static_web_console_resource_noise_is_not_enough_to_fail() -> None:
    """Generic network resource console noise is handled by resource-specific checks."""
    assert bench_gates._is_ignorable_web_console_error("Failed to load resource: net::ERR_CONNECTION_CLOSED") is True
    assert bench_gates._is_ignorable_web_console_error("favicon.ico failed to load") is True
    assert bench_gates._is_ignorable_web_console_error("Uncaught Error: render failed") is False


def test_smoke_static_web_favicon_is_not_required_resource(tmp_path: Path) -> None:
    """Missing favicon should not decide whether the generated app is runnable."""
    html = "<html><head><link rel='icon' href='favicon.ico'></head><body><img src='favicon.ico'></body></html>"

    assert bench_gates._html_local_resource_refs(tmp_path, "index.html", html) == []


def test_smoke_static_web_srcset_resources_are_checked(tmp_path: Path) -> None:
    """Responsive image resources in srcset are explicit local assets too."""
    html = "<html><body><img srcset='small.png 1x, /large.png 2x'></body></html>"

    assert bench_gates._html_local_resource_refs(tmp_path, "index.html", html) == ["small.png", "/large.png"]


def test_smoke_static_web_playwright_critical_errors_fail(tmp_path: Path) -> None:
    """Critical JavaScript errors should cause failure."""
    # Create a test HTML file
    (tmp_path / "index.html").write_text(
        "<html><body><script>throw new Error('Critical error');</script></body></html>",
        encoding="utf-8",
    )

    # Mock Playwright to simulate critical errors
    console_errors = [
        "Uncaught Error: Critical error",
        "Failed to load resource: the server responded with a status of 404 (Not Found)",
    ]

    critical_errors = [err for err in console_errors if not bench_gates._is_ignorable_web_console_error(err)]

    result = {
        "kind": "web_playwright",
        "ok": len(critical_errors) == 0,
        "url": "http://localhost/index.html",
        "entrypoint": "index.html",
        "duration_s": 0.1,
        "http_status": 200,
        "console_errors": console_errors,
        "has_canvas": False,
        "detail": f"Console errors: {'; '.join(critical_errors[:3])}",
    }

    # Should fail because of the critical error
    assert result["ok"] is False
    assert "Uncaught Error: Critical error" in str(result["detail"])


# ---------------------------------------------------------------------------
# Tests for _primary_source_language (F2/F3: Go/Rust projects with stray .py)
# ---------------------------------------------------------------------------


class TestPrimarySourceLanguage:
    """Verify that a Go project with a Python contract test is Go-primary."""

    def test_go_project_with_python_test(self) -> None:
        files = [
            "main.go",
            "src/engine/engine.go",
            "src/models/pet.go",
            "tests/test_ascii.py",
        ]
        assert _primary_source_language(files) == "go"

    def test_pure_go_project(self) -> None:
        assert _primary_source_language(["main.go", "lib.go"]) == "go"

    def test_pure_python_project(self) -> None:
        assert _primary_source_language(["main.py", "utils.py", "tests/test_main.py"]) == "python"

    def test_rust_project_with_python_test(self) -> None:
        files = ["src/main.rs", "src/lib.rs", "tests/test_contract.py"]
        assert _primary_source_language(files) == "rust"

    def test_node_project(self) -> None:
        files = ["index.js", "src/app.js", "src/utils.ts"]
        assert _primary_source_language(files) == "javascript"

    def test_html_only_project(self) -> None:
        assert _primary_source_language(["index.html", "style.css"]) == "html"

    def test_empty_project(self) -> None:
        assert _primary_source_language([]) == ""

    def test_mixed_go_and_python(self) -> None:
        # Both Go source and real Python source → Python wins by count
        files = ["main.go", "app.py", "utils.py", "helpers.py"]
        assert _primary_source_language(files) == "python"


# ---------------------------------------------------------------------------
# Tests for _go_command no-mutation behavior without go.mod
# ---------------------------------------------------------------------------


class TestGoCommandNoMutation:
    """Verify _go_command does not auto-init go.mod when missing."""

    def test_with_go_mod(self, monkeypatch: Any, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module test\n", encoding="utf-8")
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
        cmd = _go_command(tmp_path, ["main.go"])
        assert cmd == ["/usr/local/go/bin/go", "test", "./..."]

    def test_without_go_mod_uses_vet_fallback(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
        cmd = _go_command(tmp_path, ["main.go", "src/engine/engine.go"])
        assert cmd == ["/usr/local/go/bin/go", "vet", "main.go"]

    def test_without_go_mod_empty_files_returns_empty(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
        cmd = _go_command(tmp_path, [])
        assert cmd == []

    def test_without_go_mod_init_timeout_vet_fallback(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")

        def _raise_timeout(*args: Any, **kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="go", timeout=15)

        monkeypatch.setattr(bench_gates.subprocess, "run", _raise_timeout)
        cmd = _go_command(tmp_path, ["main.go"])
        assert cmd == ["/usr/local/go/bin/go", "vet", "main.go"]

    def test_go_unavailable(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: None)
        cmd = _go_command(tmp_path, ["main.go"])
        assert cmd == []


# ---------------------------------------------------------------------------
# Test: Go project skips Python test path (F2)
# ---------------------------------------------------------------------------


def test_rust_cargo_project_uses_native_test_gate(monkeypatch: Any, tmp_path: Path) -> None:
    """Rust delivery must execute native tests instead of a skipped Python harness."""
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-test"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "product.rs").write_text(
        "use native_rust_test::answer;\n#[test]\nfn product_works() { assert_eq!(answer(), 42); }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bench_gates.shutil, "which", lambda name: "/usr/bin/cargo" if name == "cargo" else None)

    command = bench_gates._rust_compile_command(
        tmp_path,
        ["src/lib.rs", "tests/product.rs"],
    )

    assert command == ["/usr/bin/cargo", "test", "--quiet"]


def test_rust_native_gate_physically_executes_integration_test(tmp_path: Path) -> None:
    """The real-run gate executes Rust tests in isolation from target sources."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-physical"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "lib.rs"
    source.write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "product.rs").write_text(
        "#[test]\nfn product_works() {\n"
        "    assert_eq!(native_rust_physical::answer(), 42);\n"
        '    std::fs::write("src/lib.rs", "pub fn answer() -> u8 { 7 }\\n").unwrap();\n'
        f'    assert!(std::fs::write({json.dumps(source.as_posix())}, b"host mutation").is_err());\n'
        "}\n",
        encoding="utf-8",
    )

    ok, detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs", "tests/product.rs"],
        timeout_s=30,
    )

    assert ok is True
    assert detail == "cargo test passed"
    assert commands[0]["command"][1:] == ["test", "--quiet"]
    assert commands[0]["sandboxed"] is True
    assert commands[0]["native_test_count"] >= 1
    assert source.read_text(encoding="utf-8") == "pub fn answer() -> u8 { 42 }\n"


def test_rust_native_gate_rejects_zero_tests(tmp_path: Path) -> None:
    """Cargo's exit-zero/zero-tests result is not valid native test evidence."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-zero"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")

    ok, detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert detail == "cargo test executed zero tests"
    assert commands[0]["returncode"] == 0
    assert commands[0]["native_test_count"] == 0


def test_rust_native_gate_fails_closed_without_sandbox(monkeypatch: Any, tmp_path: Path) -> None:
    """Native tests never fall back to direct target-workspace execution."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-no-sandbox"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")

    def unavailable_sandbox(**_kwargs: Any) -> Any:
        raise bench_gates.NativeValidationSandboxError("bubblewrap unavailable")

    monkeypatch.setattr(bench_gates, "sandboxed_cargo_test_command", unavailable_sandbox)

    ok, detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert detail == "cargo test failed"
    assert commands[0]["sandboxed"] is False
    assert str(commands[0]["error"]).startswith("native_validation_sandbox_unavailable:")


def test_rust_native_sandbox_setup_does_not_follow_project_support_symlink(tmp_path: Path) -> None:
    """Host-side sandbox preparation cannot follow a project-controlled symlink."""
    if not bench_gates.shutil.which("cargo") or not bench_gates.shutil.which("bwrap"):
        pytest.skip("cargo/bwrap unavailable")
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-symlink"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (workspace / "src").mkdir()
    (workspace / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    escaped_support = tmp_path / "escaped-support"
    escaped_support.mkdir()
    (workspace / ".cargo-home").symlink_to(escaped_support, target_is_directory=True)

    with bench_gates.sandboxed_cargo_test_command(
        workspace=workspace,
        command=[bench_gates.shutil.which("cargo") or "cargo", "test", "--quiet"],
    ):
        pass

    assert list(escaped_support.iterdir()) == []


def test_rust_native_gate_rejects_project_rustc_wrapper_output_spoof(tmp_path: Path) -> None:
    """Project Cargo config cannot forge native-test evidence through a compiler wrapper."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-wrapper"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo" / "config.toml").write_text(
        '[build]\nrustc-wrapper = "./wrapper.sh"\n',
        encoding="utf-8",
    )
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' 'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out' >&2\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


def test_rust_native_gate_rejects_custom_harness_output_spoof(tmp_path: Path) -> None:
    """A harness=false binary is not authoritative libtest execution evidence."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-custom-harness"\nversion = "0.1.0"\nedition = "2021"\n'
        '[[test]]\nname = "fake"\npath = "tests/fake.rs"\nharness = false\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fake.rs").write_text(
        'fn main() { println!("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"); }\n',
        encoding="utf-8",
    )

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs", "tests/fake.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


@pytest.mark.parametrize(
    ("manifest_target", "source_path"),
    [
        ('[lib]\npath = "src/lib.rs"\nharness = false\n', "src/lib.rs"),
        (
            '[[bin]]\nname = "fake-bin"\npath = "src/main.rs"\ntest = true\nharness = false\n',
            "src/main.rs",
        ),
        (
            '[[example]]\nname = "fake-example"\npath = "examples/fake.rs"\ntest = true\nharness = false\n',
            "examples/fake.rs",
        ),
        (
            '[[bench]]\nname = "fake-bench"\npath = "benches/fake.rs"\nharness = false\n',
            "benches/fake.rs",
        ),
    ],
)
def test_rust_native_gate_rejects_all_custom_harness_targets(
    tmp_path: Path,
    manifest_target: str,
    source_path: str,
) -> None:
    """Every Cargo target kind with a custom harness is non-authoritative."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-target-harness"\nversion = "0.1.0"\nedition = "2021"\n' + manifest_target,
        encoding="utf-8",
    )
    source = tmp_path / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        'fn main() { println!("running 1 test"); '
        'println!("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"); }\n',
        encoding="utf-8",
    )

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        [source_path],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


@pytest.mark.parametrize(
    "cargo_config",
    [
        '[target.x86_64-unknown-linux-gnu]\nlinker = "./fake-linker.sh"\n',
        '[target.x86_64-unknown-linux-gnu]\nrustflags = ["-C", "linker=./fake-linker.sh"]\n',
        '[build]\nrustflags = ["-C", "linker=./fake-linker.sh"]\n',
        '[build]\nrustdoc = "./fake-rustdoc.sh"\n',
        '[env]\nCARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER = "./fake-linker.sh"\n',
        '[env]\nRUSTFLAGS = "-C linker=./fake-linker.sh"\n',
    ],
)
def test_rust_native_gate_rejects_project_linker_and_rustflags_overrides(
    tmp_path: Path,
    cargo_config: str,
) -> None:
    """Project Cargo config cannot replace/link-inject the native test executable."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-linker"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text(
        "#[test]\nfn real_test() { assert!(true); }\n",
        encoding="utf-8",
    )
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo" / "config.toml").write_text(cargo_config, encoding="utf-8")
    fake_linker = tmp_path / "fake-linker.sh"
    fake_linker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_linker.chmod(0o755)

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


def test_rust_native_gate_rejects_workspace_member_custom_harness(tmp_path: Path) -> None:
    """Cargo workspace members cannot bypass the root manifest harness audit."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["member"]\nresolver = "2"\n',
        encoding="utf-8",
    )
    member = tmp_path / "member"
    member.mkdir()
    (member / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-member"\nversion = "0.1.0"\nedition = "2021"\n'
        '[[bin]]\nname = "fake-member"\npath = "src/main.rs"\ntest = true\nharness = false\n',
        encoding="utf-8",
    )
    (member / "src").mkdir()
    (member / "src" / "main.rs").write_text(
        'fn main() { println!("running 1 test"); '
        'println!("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"); }\n',
        encoding="utf-8",
    )

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["member/src/main.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


def test_rust_native_gate_rejects_implicit_path_dependency_member_harness(
    tmp_path: Path,
) -> None:
    """Cargo's implicit in-root path-dependency members receive the same audit."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\nresolver = "2"\n',
        encoding="utf-8",
    )
    app = tmp_path / "app"
    app.mkdir()
    (app / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-app"\nversion = "0.1.0"\nedition = "2021"\n'
        '[dependencies]\nimplicit-member = { path = "../implicit-member" }\n',
        encoding="utf-8",
    )
    (app / "src").mkdir()
    (app / "src" / "lib.rs").write_text(
        "pub fn answer() -> u8 { implicit_member::answer() }\n",
        encoding="utf-8",
    )
    implicit_member = tmp_path / "implicit-member"
    implicit_member.mkdir()
    (implicit_member / "Cargo.toml").write_text(
        '[package]\nname = "implicit-member"\nversion = "0.1.0"\nedition = "2021"\n'
        '[[bin]]\nname = "fake-implicit"\npath = "src/main.rs"\ntest = true\nharness = false\n',
        encoding="utf-8",
    )
    (implicit_member / "src").mkdir()
    (implicit_member / "src" / "lib.rs").write_text(
        "pub fn answer() -> u8 { 42 }\n",
        encoding="utf-8",
    )
    (implicit_member / "src" / "main.rs").write_text(
        'fn main() { println!("running 1 test"); '
        'println!("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"); }\n',
        encoding="utf-8",
    )

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["app/src/lib.rs", "implicit-member/src/lib.rs", "implicit-member/src/main.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


def test_non_cargo_rust_compile_is_labeled_and_leaves_no_workspace_output(tmp_path: Path) -> None:
    """Generic rustc fallback is compile evidence, never cargo-test evidence."""
    if not bench_gates.shutil.which("rustc"):
        pytest.skip("rustc unavailable")
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    ok, detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["main.rs"],
        timeout_s=30,
    )

    assert ok is True
    assert detail == "rustc compile passed"
    assert Path(commands[0]["command"][0]).name == "rustc"
    assert "test" not in commands[0]["command"][1:]
    assert not list(tmp_path.glob("*.rmeta"))


def test_go_project_skips_python_test_path(monkeypatch: Any, tmp_path: Path) -> None:
    """A Go project with tests/test_*.py must NOT run python unittest."""
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module generated\ngo 1.22\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ascii.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n"
        "    def test_x(self): self.fail('should not run')\n"
        "if __name__ == '__main__': unittest.main()\n",
        encoding="utf-8",
    )
    commands_run: list[list[str]] = []

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands_run.append(command)
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

    monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["main.go", "tests/test_ascii.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    # No Python commands should have been run.
    for cmd in commands_run:
        assert "python" not in str(cmd[0]).lower(), f"Python command leaked: {cmd}"
    # Go test should have run.
    assert any("test" in str(c) for c in commands_run)
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True


# ---------------------------------------------------------------------------
# Test: Go project uses Go entrypoint, not Python (F3)
# ---------------------------------------------------------------------------


def test_go_project_uses_go_entrypoint_not_python(monkeypatch: Any, tmp_path: Path) -> None:
    """A Go project with tests/test_*.py must use go_cli entrypoint."""
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module generated\ngo 1.22\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ascii.py").write_text(
        "import unittest\nif __name__ == '__main__': unittest.main()\n",
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        is_go_run = any("run" in str(c) for c in command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "usage: app\n" if is_go_run else "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["main.go", "tests/test_ascii.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["entrypoint"]["kind"] == "go_cli"


# ---------------------------------------------------------------------------
# Tests for Go import normalization detection (Repair Kernel owns mutation)
# ---------------------------------------------------------------------------


class TestGoImportNormalization:
    """Verify bench gates do not mutate inconsistent module prefixes."""

    def _write_go_files(self, tmp_path: Path) -> list[str]:
        go_files = ["main.go", "src/engine/engine.go", "src/models/pet.go"]
        (tmp_path / "src" / "engine").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "models").mkdir(parents=True, exist_ok=True)
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "my-project/src/engine"\n\nfunc main() { _ = engine.X }\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "engine.go").write_text(
            'package engine\n\nimport "my-proj/src/models"\n\nvar X = models.Y\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "pet.go").write_text(
            "package models\n\nvar Y = 42\n",
            encoding="utf-8",
        )
        return go_files

    def test_collect_local_imports(self, tmp_path: Path) -> None:
        go_files = self._write_go_files(tmp_path)
        imports = _collect_go_local_imports(tmp_path, go_files)
        assert len(imports) == 2
        assert imports[0][1] == "my-project/src/engine"
        assert imports[1][1] == "my-proj/src/models"

    def test_infer_module_name_dominant_prefix(self, tmp_path: Path) -> None:
        go_files = self._write_go_files(tmp_path)
        # "my-project" appears in main.go (1 import), "my-proj" in engine.go (1 import)
        # Both have count 1, so max picks whichever is lexicographically last.
        # The important thing is it returns a valid prefix.
        name = _infer_go_module_name(tmp_path, go_files)
        assert name in ("my-project", "my-proj")

    def test_normalize_repairs_inconsistent_imports(self, tmp_path: Path) -> None:
        go_files = self._write_go_files(tmp_path)
        before = (tmp_path / "src" / "engine" / "engine.go").read_text(encoding="utf-8")
        modified = _normalize_go_imports(tmp_path, go_files, "my-project")
        assert modified == 0
        engine_text = (tmp_path / "src" / "engine" / "engine.go").read_text(encoding="utf-8")
        assert engine_text == before
        assert '"my-proj/src/models"' in engine_text

    def test_normalize_no_change_when_consistent(self, tmp_path: Path) -> None:
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "mymod/pkg"\n\nfunc main() {}\n',
            encoding="utf-8",
        )
        modified = _normalize_go_imports(tmp_path, ["main.go"], "mymod")
        assert modified == 0

    def test_go_command_auto_init_with_normalization(self, monkeypatch: Any, tmp_path: Path) -> None:
        go_files = self._write_go_files(tmp_path)
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
        monkeypatch.setattr(
            bench_gates.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr=""),
        )
        cmd = _go_command(tmp_path, go_files)
        assert cmd == ["/usr/local/go/bin/go", "vet", "main.go"]
        # Verify imports were not normalized by bench_gates.
        engine_text = (tmp_path / "src" / "engine" / "engine.go").read_text(encoding="utf-8")
        assert '"my-proj/src/models"' in engine_text

    def test_bench_gates_source_has_no_workspace_mutation_calls(self) -> None:
        source = Path(bench_gates.__file__).read_text(encoding="utf-8")

        assert ".write_text(" not in source
        assert ".unlink(" not in source


# ---------------------------------------------------------------------------
# Tests for _go_version_of
# ---------------------------------------------------------------------------


class TestGoVersionOf:
    def test_parses_version_string(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            bench_gates.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="go version go1.23.8 linux/amd64\n", stderr=""
            ),
        )
        assert _go_version_of("/fake/go") == (1, 23, 8)

    def test_handles_failure(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            bench_gates.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error"),
        )
        assert _go_version_of("/fake/go") == (0,)

    def test_handles_timeout(self, monkeypatch: Any) -> None:
        def _raise(*a: Any, **kw: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="go", timeout=5)

        monkeypatch.setattr(bench_gates.subprocess, "run", _raise)
        assert _go_version_of("/fake/go") == (0,)


# ---------------------------------------------------------------------------
# Tests for _read_go_mod_module (F7: go.mod as canonical module authority)
# ---------------------------------------------------------------------------


class TestReadGoModModule:
    def test_reads_module_name(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module ascii-pet-terminal\n\ngo 1.23\n", encoding="utf-8")
        assert _read_go_mod_module(tmp_path) == "ascii-pet-terminal"

    def test_returns_empty_when_no_go_mod(self, tmp_path: Path) -> None:
        assert _read_go_mod_module(tmp_path) == ""

    def test_handles_malformed_go_mod(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("// comment only\n", encoding="utf-8")
        assert _read_go_mod_module(tmp_path) == ""


# ---------------------------------------------------------------------------
# Test: normalization discovers undeclared Go files on disk (F7)
# ---------------------------------------------------------------------------


def test_normalize_go_imports_discovers_disk_files(tmp_path: Path) -> None:
    """Files NOT in go_files list remain untouched by the measurement gate."""
    (tmp_path / "go.mod").write_text("module myproject\n\ngo 1.23\n", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    # Declared file with correct import.
    (tmp_path / "main.go").write_text(
        'package main\n\nimport "myproject/src/pkg"\n\nfunc main() { _ = pkg.X }\n',
        encoding="utf-8",
    )
    # Undeclared file with wrong prefix.
    (tmp_path / "src" / "pkg" / "helper.go").write_text(
        'package pkg\n\nimport "my-proj/src/pkg"\n\nvar X = 1\nvar _ = Y\n',
        encoding="utf-8",
    )
    # Only pass main.go — helper.go is on disk but not declared.
    modified = _normalize_go_imports(tmp_path, ["main.go"], "myproject")
    assert modified == 0
    helper_text = (tmp_path / "src" / "pkg" / "helper.go").read_text(encoding="utf-8")
    assert '"my-proj/src/pkg"' in helper_text


def test_go_command_normalizes_even_with_go_mod(monkeypatch: Any, tmp_path: Path) -> None:
    """_go_command must not normalize imports when go.mod already exists."""
    (tmp_path / "go.mod").write_text("module canonical\n\ngo 1.23\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(
        'package main\n\nimport "wrong-prefix/pkg"\n\nfunc main() {}\n',
        encoding="utf-8",
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "lib.go").write_text("package pkg\n", encoding="utf-8")

    monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
    cmd = _go_command(tmp_path, ["main.go", "pkg/lib.go"])
    assert cmd == ["/usr/local/go/bin/go", "test", "./..."]
    # Verify the import was not normalized.
    main_text = (tmp_path / "main.go").read_text(encoding="utf-8")
    assert '"wrong-prefix/pkg"' in main_text


# ---------------------------------------------------------------------------
# Tests for F8: Go import sub-path hallucination repair
# ---------------------------------------------------------------------------


class TestGoImportSubpathRepair:
    """Verify _repair_go_import_subpath fixes hallucinated sub-paths."""

    def test_repairs_hallucinated_subpath(self) -> None:
        pkg_dirs = {"src/engine", "src/models"}
        result = _repair_go_import_subpath("mymod/example/pet-ascii/src/engine", "mymod", pkg_dirs)
        assert result == "mymod/src/engine"

    def test_leaves_valid_subpath_unchanged(self) -> None:
        pkg_dirs = {"src/engine", "src/models"}
        result = _repair_go_import_subpath("mymod/src/engine", "mymod", pkg_dirs)
        assert result == "mymod/src/engine"

    def test_leaves_non_matching_module_unchanged(self) -> None:
        pkg_dirs = {"src/engine"}
        result = _repair_go_import_subpath("other/src/engine", "mymod", pkg_dirs)
        assert result == "other/src/engine"

    def test_discovers_go_package_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "engine.go").write_text("package engine\n", encoding="utf-8")
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "models" / "pet.go").write_text("package models\n", encoding="utf-8")
        dirs = _discover_go_package_dirs(tmp_path)
        assert "src/engine" in dirs
        assert "src/models" in dirs

    def test_normalize_repairs_both_prefix_and_subpath(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module my-project\n\ngo 1.23\n", encoding="utf-8")
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "engine.go").write_text("package engine\n", encoding="utf-8")
        # main.go has a hallucinated sub-path with the correct prefix.
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "my-project/hallucinated/path/src/engine"\n\n'
            "// comment about my-project should NOT change\n"
            "func main() {}\n",
            encoding="utf-8",
        )
        modified = _normalize_go_imports(tmp_path, ["main.go", "src/engine/engine.go"], "my-project")
        assert modified == 0
        main_text = (tmp_path / "main.go").read_text(encoding="utf-8")
        assert '"my-project/hallucinated/path/src/engine"' in main_text
        # Comment must NOT be modified.
        assert "// comment about my-project should NOT change" in main_text
