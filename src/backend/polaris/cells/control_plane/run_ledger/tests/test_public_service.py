from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionBarrierQueryV1,
    ReadRunLedgerProjectionQueryV1,
    ReadRunProvenanceBundleQueryV1,
    RunLedger,
    append_run_ledger_event,
    build_run_ledger_projection,
    build_tool_call_lifecycle_receipt,
    read_run_ledger_projection,
    read_run_ledger_projection_barrier,
    read_run_provenance_bundle,
    service as run_ledger_service,
    summarize_run_ledger_projection,
)


def _write_ledger_event(workspace: Path, *, run_id: str = "run-1") -> None:
    ledger_path = workspace / "runtime" / "factory" / "ledger" / f"{run_id}.ndjson"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event_type": "gate_evaluated",
        "event_id": "evt-1",
        "content_id": "cid-1",
        "append_id": "append-1",
        "stage": "qa_verifier",
        "gate": {"name": "qa_verifier", "ok": True, "summary": "gate passed"},
        "job_token": {
            "token_id": "token-1",
            "run_id": run_id,
            "project_id": "P1",
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {
                "enabled_evidence_modalities": ["browser"],
                "required_evidence_modalities": [],
            },
        },
        "physical_evidence": {
            "modalities": {
                "browser": {
                    "present": True,
                    "ok": True,
                    "detail": "browser verifier passed",
                }
            }
        },
    }
    ledger_path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")


def test_run_ledger_writer_uses_platform_control_plane_namespace(tmp_path: Path) -> None:
    persisted = RunLedger(tmp_path, run_id="run-1").append_event(
        {
            "event_type": "gate_evaluated",
            "gate": {"name": "qa_verifier", "ok": True, "summary": "ok"},
            "job_token": {
                "token_id": "token-1",
                "project_id": "P1",
                "capability_audit": {"ok": True, "issues": []},
                "gate_policy": {},
            },
            "physical_evidence": {},
        }
    )

    ledger_path = Path(str(persisted["ledger_path"]))
    assert ledger_path.parent == tmp_path / "runtime" / "control_plane" / "ledger"
    assert RunLedger(tmp_path, run_id="run-1").read_events()[0]["event_type"] == "gate_evaluated"


def test_append_run_ledger_event_public_service_projects_event(tmp_path: Path) -> None:
    result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "director_mutation",
                "gate": {"name": "director_mutation", "ok": True, "summary": "mutation verified"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["tool_receipt"],
                        "required_evidence_modalities": [],
                    },
                },
                "physical_evidence": {
                    "tool_receipts": [
                        {
                            "operation": "write_file",
                            "file": "src/app.ts",
                            "capability_token": {"token_id": "token-1"},
                        }
                    ]
                },
            },
        )
    )

    ledger_path = Path(str(result.receipt["ledger_path"]))
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection

    assert ledger_path.parent == tmp_path / "runtime" / "control_plane" / "ledger"
    assert result.receipt["event"]["append_id"]
    assert projection["ok"] is True
    assert projection["projects"][0]["project_id"] == "P1"
    assert projection["evidence_modalities"]["tool_receipt"]["present"] == 1


def test_required_evidence_distinguishes_missing_from_failed() -> None:
    base_event = {
        "event_type": "gate_evaluated",
        "stage": "real_run",
        "gate": {"name": "real_run_gate", "ok": False, "summary": "command failed"},
        "job_token": {
            "token_id": "token-1",
            "project_id": "P1",
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {"required_evidence_modalities": ["code", "command"]},
        },
        "physical_evidence": {
            "modalities": {
                "code": {"present": True, "ok": True, "detail": "files landed"},
                "command": {"present": True, "ok": False, "detail": "go test failed"},
            }
        },
    }

    projection = build_run_ledger_projection([base_event])
    summary = summarize_run_ledger_projection(projection)

    assert projection["integrity_ok"] is True
    assert projection["outcome_ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["command"]
    assert projection["missing"] == []
    assert summary["missing"] == []
    assert summary["failed_required_modalities"] == ["command"]
    assert summary["detail"] == "run ledger projection required evidence failed: command"

    missing_projection = build_run_ledger_projection(
        [
            {
                **base_event,
                "physical_evidence": {
                    "modalities": {
                        "code": {"present": True, "ok": True, "detail": "files landed"},
                    }
                },
            }
        ]
    )

    assert missing_projection["integrity_ok"] is False
    assert missing_projection["evidence_policy"]["missing_required_modalities"] == ["command"]
    assert missing_projection["evidence_policy"]["failed_required_modalities"] == []


def test_projection_exposes_tool_dispatch_dropped() -> None:
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        native_tool_calls_count=1,
        decoded_tool_calls_count=0,
        dispatched_tool_calls_count=0,
        receipts=[],
        dropped_tool_calls=["write_file"],
        dispatch_status="dropped",
        failure_class="TOOL_DISPATCH_DROPPED",
        reason="decode failed",
    ).to_dict()
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "director",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
            {
                "event_type": "tool_call_lifecycle",
                "tool_call_lifecycle_receipt": lifecycle,
            },
        ]
    )
    summary = summarize_run_ledger_projection(projection)

    assert projection["ok"] is False
    assert projection["integrity_ok"] is False
    assert projection["tool_lifecycle"]["ok"] is False
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["tool_lifecycle"]["native_tool_call_names"] == ["write_file"]
    assert projection["tool_lifecycle"]["events"][0]["native_tool_call_names"] == ["write_file"]
    assert projection["tool_lifecycle"]["events"][0]["provider_response_hash"] == "provider-response-hash"
    assert projection["tool_lifecycle"]["events"][0]["receipt"]["schema_version"] == "tool_call_lifecycle_receipt.v1"
    assert projection["tool_lifecycle"]["failure_evidence"][0]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert projection["tool_lifecycle"]["failure_evidence"][0]["reason"] == "decode failed"
    assert projection["tool_lifecycle"]["events"][0]["failure_evidence"] == projection["tool_lifecycle"][
        "failure_evidence"
    ][0]
    assert summary["detail"] == "run ledger projection tool lifecycle failed: TOOL_DISPATCH_DROPPED"
    assert summary["missing"] == []
    assert summary["failed_control_plane_events"] == ["TOOL_DISPATCH_DROPPED"]


def test_task_boundary_plan_probe_projects_failed_required_evidence() -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "workspace_quality",
                "gate": {"name": "workspace_quality", "ok": False, "summary": "task boundary triage"},
                "job_token": {
                    "token_id": "token-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {"required_evidence_modalities": ["task_boundary"]},
                },
                "physical_evidence": {
                    "repair": {
                        "plan_probe_preaudit": {
                            "status": "coverage_matched_but_unplannable",
                            "plannable_source_tools": [],
                            "covered_unplannable_source_tools": ["deterministic_go_missing_symbol_repair"],
                            "covered_unplannable_diagnostic_count": 1,
                        },
                        "interface_discrepancy_evidence": {
                            "reason": "coverage_matched_but_unplannable",
                            "recommended_owner": "chief_engineer",
                            "recommended_route": "pending_design_interface_contract",
                            "llm_fallback_blocked": True,
                        },
                        "interface_discrepancy_receipts": [
                            {
                                "schema_version": "director.interface_discrepancy_receipt.v1",
                                "task_id": "TASK-1",
                                "status": "semantic_discrepancy_triage_required",
                                "source": "director.runtime.task_boundary_quality_loop",
                                "plan_probe_status": "coverage_matched_but_unplannable",
                                "reason": "coverage_matched_but_unplannable",
                                "source_tools": ["deterministic_go_missing_symbol_repair"],
                                "recommended_owner": "chief_engineer",
                                "recommended_route": "pending_design_interface_contract",
                                "llm_fallback_blocked": True,
                                "director_retry_allowed": False,
                                "interface_delta": {
                                    "schema_version": "director.interface_delta.v1",
                                    "contract_present": False,
                                    "requested_symbols": ["NewCapsule"],
                                    "diagnostic_paths": ["src/main.go"],
                                },
                                "triage_summary": {
                                    "schema_version": "director.interface_discrepancy_triage.v1",
                                    "recommended_owner": "chief_engineer",
                                    "recommended_route": "pending_design_interface_contract",
                                    "reason": "task_interface_contract_missing",
                                },
                            }
                        ],
                    }
                },
            }
        ]
    )
    summary = summarize_run_ledger_projection(projection)

    assert projection["integrity_ok"] is True
    assert projection["outcome_ok"] is False
    assert projection["evidence_modalities"]["task_boundary"]["present"] == 1
    assert projection["evidence_modalities"]["task_boundary"]["failed"] == 1
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["task_boundary"]
    task_boundary_metadata = projection["gates"][0]["evidence_modalities"]["task_boundary"]["metadata"]
    assert task_boundary_metadata["interface_discrepancy_schema_version"] == "director.interface_discrepancy_receipt.v1"
    assert task_boundary_metadata["interface_delta_available"] is True
    assert task_boundary_metadata["interface_delta"]["requested_symbols"] == ["NewCapsule"]
    assert task_boundary_metadata["triage_summary_available"] is True
    assert task_boundary_metadata["triage_summary"]["reason"] == "task_interface_contract_missing"
    assert summary["missing"] == []
    assert summary["failed_required_modalities"] == ["task_boundary"]
    assert summary["detail"] == "run ledger projection required evidence failed: task_boundary"


def test_projection_exposes_failed_tool_lifecycle_without_dropped_dispatch() -> None:
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()
    lifecycle["failure_class"] = "missing-effect-receipt"
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "director",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
            {
                "event_type": "tool_call_lifecycle",
                "tool_call_lifecycle_receipt": lifecycle,
            },
        ]
    )
    summary = summarize_run_ledger_projection(projection)

    assert projection["ok"] is False
    assert projection["integrity_ok"] is False
    assert projection["tool_lifecycle"]["ok"] is False
    assert projection["tool_lifecycle"]["failed_count"] == 1
    assert projection["tool_lifecycle"]["dropped_count"] == 0
    assert projection["tool_lifecycle"]["events"][0]["failed"] is True
    assert projection["tool_lifecycle"]["events"][0]["failure_class"] == "MISSING_EFFECT_RECEIPT"
    assert summary["detail"] == "run ledger projection tool lifecycle failed: MISSING_EFFECT_RECEIPT"
    assert summary["missing"] == []
    assert summary["failed_control_plane_events"] == ["MISSING_EFFECT_RECEIPT"]


def test_projection_exposes_task_boundary_failure() -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "director",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
            {
                "event_type": "task_boundary_verdict",
                "task_boundary_verdict": {
                    "schema_version": "polaris.task_boundary_verdict.v1",
                    "task_id": "TASK-1",
                    "status": "missing_entrypoint_target",
                    "ok": False,
                    "failure_class": "MISSING_ENTRYPOINT_TARGET",
                    "responsible_layer": "task_boundary",
                    "reason": "package.json references src/index.js",
                    "missing_entrypoint_targets": ["src/index.js"],
                },
            },
        ]
    )
    summary = summarize_run_ledger_projection(projection)

    assert projection["ok"] is False
    assert projection["outcome_ok"] is False
    assert projection["task_boundary"]["ok"] is False
    assert projection["task_boundary"]["latest"]["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert summary["detail"] == "run ledger projection task boundary failed: MISSING_ENTRYPOINT_TARGET"


def test_public_projection_summary_normalizes_task_boundary_failure_alias() -> None:
    summary = summarize_run_ledger_projection(
        {
            "source": "run_ledger",
            "ok": False,
            "gate_count": 1,
            "capability": {"ok": True},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "ok": False,
                    "failure_class": "missing-entrypoint-target",
                },
            },
        }
    )

    assert summary["detail"] == "run ledger projection task boundary failed: MISSING_ENTRYPOINT_TARGET"
    assert summary["failed_control_plane_events"] == ["MISSING_ENTRYPOINT_TARGET"]


def test_public_projection_carries_task_boundary_and_tool_lifecycle(tmp_path: Path) -> None:
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=0,
        dispatched_tool_calls_count=0,
        receipts=[],
        dispatch_status="dropped",
        failure_class="TOOL_DISPATCH_DROPPED",
    ).to_dict()
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "tool_call_lifecycle",
                "run_id": "run-1",
                "task_id": "TASK-1",
                "job_token": {"project_id": "P1", "capability_audit": {"ok": True, "issues": []}},
                "tool_call_lifecycle_receipt": lifecycle,
            },
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "task_boundary_verdict",
                "run_id": "run-1",
                "task_id": "TASK-1",
                "job_token": {"project_id": "P1", "capability_audit": {"ok": True, "issues": []}},
                "task_boundary_verdict": {
                    "schema_version": "polaris.task_boundary_verdict.v1",
                    "task_id": "TASK-1",
                    "status": "deferred_followup_required",
                    "ok": False,
                    "failure_class": "DEFERRED_FOLLOWUP_REQUIRED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "needs follow-up",
                },
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection

    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["task_boundary"]["latest"]["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"
    assert projection["projects"][0]["tool_lifecycle"]["dropped_count"] == 1
    assert projection["projects"][0]["task_boundary"]["latest"]["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"


def test_read_run_ledger_projection_evidence_policy_failed_is_not_ok(tmp_path: Path) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "real_run",
                "gate": {"name": "real_run_gate", "ok": True, "summary": "gate saw evidence"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["command"],
                        "required_evidence_modalities": ["command"],
                    },
                },
                "physical_evidence": {
                    "modalities": {
                        "command": {
                            "present": True,
                            "ok": False,
                            "detail": "pytest failed",
                        }
                    }
                },
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection

    assert projection["ok"] is False
    assert projection["failed"] == 1
    assert projection["evidence_policy"]["ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["command"]


def test_read_run_ledger_projection_repair_missing_evidence_is_failed_not_missing(tmp_path: Path) -> None:
    append_result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "director_repair",
                "gate": {"name": "director_repair_gate", "ok": True, "summary": "repair wrote file"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["repair"],
                        "required_evidence_modalities": ["repair"],
                    },
                },
                "physical_evidence": {
                    "repair_receipts": [
                        {
                            "receipt_id": "repair-1",
                            "source_tool": "deterministic_typescript_return_object_semicolon_repair",
                            "status": "applied",
                            "authoritative": False,
                            "evidence_status": "missing_evidence",
                        }
                    ],
                    "receipt_authority_policy": {
                        "schema_version": "director.repair_receipt_authority_policy.v1",
                        "authoritative_success": False,
                        "receipt_count": 1,
                        "missing_evidence_receipt_count": 1,
                        "failed_evidence_receipt_count": 0,
                        "non_authoritative_receipt_count": 1,
                    },
                },
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection
    canonical = build_run_ledger_projection([append_result.receipt["event"]])

    repair_modality = canonical["gates"][0]["evidence_modalities"]["repair"]
    assert projection["ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["repair"]
    assert repair_modality["present"] is True
    assert repair_modality["ok"] is False
    assert repair_modality["metadata"]["blocker"] == "repair_missing_revalidation_evidence"
    assert repair_modality["metadata"]["missing_evidence_receipt_count"] == 1


def test_read_run_ledger_projection_environment_prep_failed_is_failed_not_missing(tmp_path: Path) -> None:
    append_result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "director_repair",
                "gate": {"name": "director_repair_gate", "ok": True, "summary": "env prep ran"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["environment_prep"],
                        "required_evidence_modalities": ["environment_prep"],
                    },
                },
                "physical_evidence": {
                    "environment_prep_receipts": [
                        {
                            "schema_version": "director.environment_prep_receipt.v1",
                            "plan_id": "env-prep-1",
                            "ecosystem": "node",
                            "package_manager": "npm",
                            "command": ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                            "exit_code": 1,
                            "status": "failed",
                            "manifest": "package.json",
                            "error_code": "environment_prep_command_failed",
                        }
                    ],
                },
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection
    canonical = build_run_ledger_projection([append_result.receipt["event"]])

    env_modality = canonical["gates"][0]["evidence_modalities"]["environment_prep"]
    assert projection["ok"] is False
    assert projection["missing_required_modalities"] == []
    assert projection["failed_required_modalities"] == ["environment_prep"]
    assert projection["failed_evidence_details"]["required_modalities"] == ["environment_prep"]
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["environment_prep"]
    assert env_modality["present"] is True
    assert env_modality["ok"] is False
    assert env_modality["metadata"]["failed_receipt_count"] == 1
    assert env_modality["metadata"]["error_codes"] == ["environment_prep_command_failed"]


def test_read_run_ledger_projection_barrier_waits_for_effect_receipt(tmp_path: Path) -> None:
    result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-barrier",
            event={
                "event_type": "gate_evaluated",
                "stage": "director_mutation",
                "gate": {"name": "director_mutation", "ok": True, "summary": "effect persisted"},
                "job_token": {
                    "token_id": "token-barrier",
                    "run_id": "run-barrier",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["tool_receipt"],
                        "required_evidence_modalities": ["tool_receipt"],
                    },
                },
                "physical_evidence": {
                    "modalities": {
                        "tool_receipt": {
                            "present": True,
                            "ok": True,
                            "detail": "write_file receipt recorded",
                        }
                    }
                },
            },
        )
    )
    event = result.receipt["event"]

    barrier_result = read_run_ledger_projection_barrier(
        ReadRunLedgerProjectionBarrierQueryV1(
            workspace=str(tmp_path),
            run_id="run-barrier",
            min_append_id=str(event["append_id"]),
        )
    )

    assert barrier_result.barrier["barrier_satisfied"] is True
    assert barrier_result.barrier["consumed_until_append_id"] == event["append_id"]
    assert event["append_id"] in barrier_result.barrier["consumed_append_ids"]
    assert barrier_result.projection["available"] is True
    assert barrier_result.projection["ok"] is True


def test_read_run_ledger_projection_barrier_reports_unsatisfied_snapshot(tmp_path: Path) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-barrier-miss",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa",
                "gate": {"name": "qa_verdict", "ok": True, "summary": "qa passed"},
                "job_token": {
                    "token_id": "token-barrier-miss",
                    "run_id": "run-barrier-miss",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )

    barrier_result = read_run_ledger_projection_barrier(
        ReadRunLedgerProjectionBarrierQueryV1(
            workspace=str(tmp_path),
            run_id="run-barrier-miss",
            min_append_id="append-not-yet-consumed",
            timeout_ms=0,
        )
    )

    assert barrier_result.barrier["barrier_satisfied"] is False
    assert barrier_result.barrier["event_count"] == 1
    assert barrier_result.projection["available"] is True


def test_append_run_ledger_event_publishes_control_plane_projection_event(tmp_path: Path, monkeypatch) -> None:
    class FakePublisher:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            self.calls.append((subject, payload))
            return True

    publisher = FakePublisher()
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(run_ledger_service, "get_log_jetstream_publisher", lambda: publisher)
    monkeypatch.setattr(
        run_ledger_service,
        "resolve_storage_roots",
        lambda workspace: SimpleNamespace(workspace_key="workspace-key"),
    )

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa_verifier",
                "gate": {"name": "qa_verifier", "ok": True, "summary": "qa verified"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )

    assert len(publisher.calls) == 1
    subject, payload = publisher.calls[0]
    event_payload = payload["payload"]
    assert subject == "hp.runtime.workspace-key.status.control_plane"
    assert payload["schema_version"] == "runtime.v2"
    assert payload["channel"] == "status.control_plane"
    assert payload["kind"] == "control_plane_ledger_projection_update"
    assert isinstance(event_payload, dict)
    projection = event_payload["projection"]
    assert isinstance(projection, dict)
    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is True
    assert projection["projects"][0]["project_id"] == "P1"


def test_read_run_ledger_projection_ignores_migration_ledgers_by_default(tmp_path: Path) -> None:
    _write_ledger_event(tmp_path)

    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1"))
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is False
    assert projection["ok"] is False
    assert projection["migration_ledgers_included"] is False
    assert projection["projects"] == []


def test_read_run_ledger_projection_can_include_migration_ledgers_explicitly_for_migration(
    tmp_path: Path,
) -> None:
    _write_ledger_event(tmp_path)

    result = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="run-1",
            include_migration_ledgers=True,
        )
    )
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is True
    assert projection["ok"] is True
    assert projection["migration_ledgers_included"] is True
    assert projection["projects"][0]["project_id"] == "P1"
    assert projection["evidence_policy"]["enabled_modalities"] == ["browser"]
    assert projection["evidence_policy"]["required_modalities"] == []


def test_read_run_ledger_projection_returns_empty_when_no_ledger_exists(tmp_path: Path) -> None:
    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path)))
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is False
    assert projection["status"] == "pending"
    assert projection["migration_ledgers_included"] is False
    assert projection["projects"] == []


def test_read_run_provenance_bundle_links_contract_blueprint_envelope_and_receipts(tmp_path: Path) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa_verifier",
                "trace_id": "trace-1",
                "gate": {
                    "name": "qa_verifier",
                    "ok": True,
                    "summary": "verified",
                    "content_id": "qa-hash",
                },
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "task_id": "TASK-1",
                    "project_id": "P1",
                    "contract_hash": "pm-hash",
                    "blueprint_hash": "ce-hash",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["tool_receipt", "command"],
                        "required_evidence_modalities": ["tool_receipt"],
                    },
                },
                "physical_evidence": {
                    "modalities": {"tool_receipt": {"present": True, "ok": True, "detail": "receipt verified"}},
                    "tool_receipts": [
                        {
                            "operation": "write_file",
                            "file": "src/main.py",
                            "capability_token": {"token_id": "token-1"},
                        }
                    ],
                    "commands": [{"command": "python -m unittest", "ok": True, "exit_code": 0}],
                    "final_request_context_audit": {
                        "schema_version": "llm.final_request_context_audit.v1",
                        "final_request_evidence_coverage": {
                            "schema_version": "polaris.final_request_evidence_coverage.v1",
                            "request_hash": "provider-request-hash",
                            "workflow_chain": {
                                "pm_contract_hash": "pm-hash",
                                "ce_blueprint_hash": "ce-hash",
                                "handoff_decision_hash": "handoff-hash",
                                "execution_profile_hash": "profile-hash",
                                "execution_envelope_hash": "envelope-hash",
                            },
                        },
                    },
                    "context_snapshot_ref": "abcdefabcdefabcdefabcdef",
                },
            },
        )
    )

    bundle = read_run_provenance_bundle(ReadRunProvenanceBundleQueryV1(workspace=str(tmp_path), run_id="run-1")).bundle

    assert bundle["schema_version"] == "polaris.run_provenance_bundle.v1"
    assert bundle["bundle_id"].startswith("run-prov-")
    assert bundle["run_id"] == "run-1"
    assert bundle["task_id"] == "TASK-1"
    assert bundle["status"] == "success"
    assert bundle["pm_contract_hash"] == "pm-hash"
    assert bundle["ce_blueprint_hash"] == "ce-hash"
    assert bundle["handoff_decision_hash"] == "handoff-hash"
    assert bundle["execution_envelope_hash"] == "envelope-hash"
    assert bundle["final_provider_request_hashes"] == ["provider-request-hash"]
    assert bundle["tool_receipt_hashes"]
    assert bundle["command_receipt_hashes"]
    assert "abcdefabcdefabcdefabcdef" in bundle["evidence_refs"]
    assert bundle["invalid_evidence_refs"] == []


def test_read_run_provenance_bundle_rejects_path_shaped_context_snapshot_ref(
    tmp_path: Path,
) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "llm_request",
                "gate": {"name": "llm_request", "ok": True, "summary": "snapshot emitted"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "task_id": "TASK-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                },
                "physical_evidence": {
                    "context_snapshot_ref": "runtime/contexts/aa/provider-request.json",
                    "evidence_ref": "runtime/evidence/provider-request.json",
                },
            },
        )
    )

    bundle = read_run_provenance_bundle(ReadRunProvenanceBundleQueryV1(workspace=str(tmp_path), run_id="run-1")).bundle

    assert "runtime/contexts/aa/provider-request.json" not in bundle["evidence_refs"]
    assert "runtime/evidence/provider-request.json" in bundle["evidence_refs"]
    assert bundle["invalid_evidence_refs"] == [
        {
            "ref_type": "context_snapshot_ref",
            "value": "runtime/contexts/aa/provider-request.json",
            "reason": "context hash must be a 24-character lowercase hexadecimal string",
        }
    ]


def test_read_run_provenance_bundle_marks_failed_required_evidence_as_failed(
    tmp_path: Path,
) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "real_run",
                "gate": {"name": "real_run_gate", "ok": True, "summary": "gate emitted command evidence"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "task_id": "TASK-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {"required_evidence_modalities": ["command"]},
                },
                "physical_evidence": {
                    "modalities": {
                        "command": {
                            "present": True,
                            "ok": False,
                            "detail": "npm test failed",
                            "exit_code": 1,
                        }
                    },
                    "commands": [{"command": "npm test", "ok": False, "exit_code": 1}],
                },
            },
        )
    )

    bundle = read_run_provenance_bundle(ReadRunProvenanceBundleQueryV1(workspace=str(tmp_path), run_id="run-1")).bundle

    assert bundle["status"] == "failed"
    assert bundle["missing_required_modalities"] == []
    assert bundle["failed_required_modalities"] == ["command"]


def test_read_run_provenance_bundle_exposes_missing_authority_hashes(tmp_path: Path) -> None:
    bundle = read_run_provenance_bundle(
        ReadRunProvenanceBundleQueryV1(workspace=str(tmp_path), run_id="missing-run")
    ).bundle

    assert bundle["schema_version"] == "polaris.run_provenance_bundle.v1"
    assert bundle["run_id"] == "missing-run"
    assert bundle["status"] == "blocked"
    assert bundle["pm_contract_hash"] == "missing:pm_contract_hash"
    assert bundle["ce_blueprint_hash"] == "missing:ce_blueprint_hash"
    assert bundle["handoff_decision_hash"] == "missing:handoff_decision_hash"
    assert bundle["execution_envelope_hash"] == "missing:execution_envelope_hash"
