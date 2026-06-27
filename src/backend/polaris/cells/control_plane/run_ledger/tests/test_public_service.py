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


def test_read_run_ledger_projection_ignores_compat_ledgers_by_default(tmp_path: Path) -> None:
    _write_ledger_event(tmp_path)

    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1"))
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is False
    assert projection["ok"] is False
    assert projection["compat_ledgers_included"] is False
    assert projection["projects"] == []


def test_read_run_ledger_projection_can_include_compat_ledgers_explicitly_for_migration(
    tmp_path: Path,
) -> None:
    _write_ledger_event(tmp_path)

    result = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="run-1",
            include_compat_ledgers=True,
        )
    )
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is True
    assert projection["ok"] is True
    assert projection["compat_ledgers_included"] is True
    assert projection["projects"][0]["project_id"] == "P1"
    assert projection["evidence_policy"]["enabled_modalities"] == ["browser"]
    assert projection["evidence_policy"]["required_modalities"] == []


def test_read_run_ledger_projection_returns_empty_when_no_ledger_exists(tmp_path: Path) -> None:
    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path)))
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is False
    assert projection["status"] == "pending"
    assert projection["compat_ledgers_included"] is False
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
                    "context_snapshot_ref": "runtime/contexts/aa/provider-request.json",
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
    assert "runtime/contexts/aa/provider-request.json" in bundle["evidence_refs"]


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
