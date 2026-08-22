"""Exact-run evidence correlation and provider-request projection tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from polaris.cells.audit.diagnosis.public import service as diagnosis_service
from polaris.cells.audit.diagnosis.public.contracts import (
    QueryAuditDiagnosisTrailV1,
    QueryExactRunCausalAuditV1,
)
from polaris.cells.audit.diagnosis.public.service import (
    _context_snapshot_refs,
    _event_correlated_run_ids,
    _event_matches_run_id,
    _file_deficits_from_final_request,
    _file_deficits_from_request_metadata,
    _repair_evidence_from_workspace,
    _structured_failure_signals,
    query_audit_diagnosis_trail,
)


def test_nested_factory_run_id_correlates_role_event_but_message_text_does_not() -> None:
    event = {
        "run_id": "director-role-run",
        "message": "unrelated factory_other appeared in prose",
        "raw": {"data": {"metadata": {"factory_run_id": "factory_exact"}}},
    }

    assert _event_correlated_run_ids(event) == {"director-role-run", "factory_exact"}
    assert _event_matches_run_id(event, "factory_exact") is True
    assert _event_matches_run_id(event, "factory_other") is False


def test_file_deficit_extractor_accepts_only_final_request_numeric_signatures() -> None:
    messages = [
        {
            "role": "user",
            "content": "delivery depth: prod_files=3 < 7; test_files: 1 < 2; plain words do not count",
        }
    ]
    assert _file_deficits_from_final_request(messages) == [
        {
            "metric": "prod_files",
            "actual": 3,
            "required": 7,
            "source": "final_provider_request.messages",
        },
        {
            "metric": "test_files",
            "actual": 1,
            "required": 2,
            "source": "final_provider_request.messages",
        },
    ]


def test_structured_request_metadata_is_preferred_for_file_deficits() -> None:
    request_metadata = {
        "failed_gate_evidence_summary": {
            "quality_metrics": {"prod_files": 3, "test_files": 1},
            "quality_minimums": {"min_prod_files": 7},
        },
        "delivery_depth_contract_summary": {"minimums": {"min_test_files": 2}},
    }

    assert _file_deficits_from_request_metadata(request_metadata) == [
        {
            "metric": "prod_files",
            "actual": 3,
            "required": 7,
            "source": "final_request_context_audit.request_metadata_summary",
        },
        {
            "metric": "test_files",
            "actual": 1,
            "required": 2,
            "source": "final_request_context_audit.request_metadata_summary",
        },
    ]


def test_context_snapshot_selection_preserves_each_role_under_long_director_tail() -> None:
    pm_ref = "000000000000000000000001"
    ce_ref = "000000000000000000000002"
    events: list[dict[str, object]] = [
        {"role": "pm", "context_snapshot_ref": pm_ref},
        {"role": "chief_engineer", "context_snapshot_ref": ce_ref},
    ]
    events.extend({"role": "director", "context_snapshot_ref": f"{index:024x}"} for index in range(3, 30))

    refs = _context_snapshot_refs(events, limit=3)

    assert refs == [pm_ref, ce_ref, f"{29:024x}"]


def test_structured_failure_signal_uses_typed_fields_not_message_prose() -> None:
    events = [
        {
            "event": "llm.call_error",
            "message": "ignored prose mentions unrelated_error",
            "data": {
                "error_code": "provider_stream_timeout",
                "role": "chief_engineer",
                "stage": "chief_engineer_review",
                "task_id": "TASK-2",
                "context_snapshot_ref": "000000000000000000000002",
            },
        }
    ]

    assert _structured_failure_signals(events) == [
        {
            "error_code": "provider_stream_timeout",
            "event_kind": "llm.call_error",
            "role": "chief_engineer",
            "stage": "chief_engineer_review",
            "task_id": "TASK-2",
            "context_snapshot_ref": "000000000000000000000002",
            "timestamp": "",
        }
    ]


def test_structured_failure_signal_promotes_machine_error_message_prefix() -> None:
    events = [
        {
            "event": "error",
            "raw": {
                "metadata": {
                    "role": "chief_engineer",
                    "error_code": "output_validation_failed",
                    "error_category": "provider",
                    "error_message": (
                        "structured_output_payload_schema_mismatch:$:Additional properties are not allowed"
                    ),
                    "context_snapshot_ref": "000000000000000000000003",
                }
            },
        }
    ]

    assert _structured_failure_signals(events) == [
        {
            "error_code": "structured_output_payload_schema_mismatch",
            "error_category": "provider",
            "error_message": "structured_output_payload_schema_mismatch:$:Additional properties are not allowed",
            "event_kind": "error",
            "role": "chief_engineer",
            "stage": "",
            "task_id": "",
            "context_snapshot_ref": "000000000000000000000003",
            "timestamp": "",
        }
    ]


def test_structured_failure_signal_dedup_keeps_latest_physical_error() -> None:
    events = [
        {
            "event": "error",
            "ts": "2026-08-21T00:00:01Z",
            "raw": {
                "metadata": {
                    "role": "chief_engineer",
                    "error_message": "structured_output_payload_schema_mismatch:first",
                    "context_snapshot_ref": "000000000000000000000001",
                }
            },
        },
        {
            "event": "error",
            "ts": "2026-08-21T00:00:02Z",
            "raw": {
                "metadata": {
                    "role": "chief_engineer",
                    "error_message": "structured_output_payload_schema_mismatch:latest",
                    "context_snapshot_ref": "000000000000000000000002",
                }
            },
        },
    ]

    signals = _structured_failure_signals(events)

    assert len(signals) == 1
    assert signals[0]["error_message"].endswith(":latest")
    assert signals[0]["context_snapshot_ref"] == "000000000000000000000002"


def test_workspace_quality_artifact_is_read_from_canonical_runtime_root(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    evidence_path = runtime_root / "qa" / "workspace-validation.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(
        json.dumps(
            {
                "repair": {
                    "residual_errors": ["src/main.ts(1,1): error TS9999: example"],
                    "plan_probe_preaudit": {"status": "coverage_gap"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        diagnosis_service,
        "resolve_storage_roots",
        lambda _workspace: SimpleNamespace(runtime_root=str(runtime_root)),
    )

    evidence = _repair_evidence_from_workspace(str(tmp_path))

    assert evidence["evidence_source"] == "runtime.qa.workspace_validation"
    assert evidence["full_evidence_ref"] == str(evidence_path)
    assert evidence["plan_probe_preaudit"]["status"] == "coverage_gap"


@pytest.mark.asyncio
async def test_exact_run_query_reuses_preloaded_ledger_projection(monkeypatch) -> None:
    async def factory_projection(_query):
        return SimpleNamespace(
            to_dict=lambda: {
                "status": "failed",
                "failed_stages": ["director_dispatch"],
                "completed_stages": ["pm_planning", "chief_engineer_review"],
            }
        )

    monkeypatch.setattr(diagnosis_service, "get_factory_chain_projection", factory_projection)
    monkeypatch.setattr(
        diagnosis_service,
        "read_run_ledger_projection",
        lambda _query: (_ for _ in ()).throw(AssertionError("duplicate ledger scan")),
    )
    monkeypatch.setattr(diagnosis_service, "get_factory_terminal_task_runtime_projection", lambda _query: None)
    monkeypatch.setattr(
        diagnosis_service,
        "query_audit_diagnosis_trail",
        lambda _query: SimpleNamespace(ok=True, payload={"events": [], "total": 0}),
    )
    monkeypatch.setattr(
        diagnosis_service,
        "_chief_engineer_authority_feasibility",
        lambda **_kwargs: {"available": False},
    )

    result = await diagnosis_service.query_exact_run_causal_audit(
        QueryExactRunCausalAuditV1(
            workspace="/tmp/project",
            factory_run_id="factory-preloaded-ledger",
            preloaded_run_ledger_projection={
                "run_projection": {},
                "evidence_policy": {},
                "task_boundary": {},
                "tool_lifecycle": {},
                "evidence_modalities": {},
            },
        )
    )

    assert result.status != "unavailable"


def test_offline_journal_fallback_correlates_role_run_to_factory_run(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    logs = runtime_root / "runs" / "director-role-run" / "logs"
    logs.mkdir(parents=True)
    event = {
        "run_id": "director-role-run",
        "ts": "2026-08-21T00:00:00Z",
        "ts_epoch": 1.0,
        "refs": {"context_snapshot_ref": "ec851e95f353eb000dab334b"},
        "raw": {"data": {"factory_run_id": "factory_exact"}},
    }
    with open(logs / "journal.norm.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    monkeypatch.setattr(
        "polaris.cells.audit.diagnosis.public.service.has_audit_store_factory",
        lambda: False,
    )

    result = query_audit_diagnosis_trail(
        QueryAuditDiagnosisTrailV1(
            workspace=str(tmp_path),
            run_id="factory_exact",
            limit=50,
        )
    )

    assert result.ok is True
    assert result.status == "available"
    assert result.payload["total"] == 1
    assert result.payload["events"][0]["run_id"] == "director-role-run"


def test_registered_empty_audit_store_still_discovers_exact_run_journal(tmp_path, monkeypatch) -> None:
    """A registered-but-empty AuditStore must not hide physical run evidence."""
    runtime_root = tmp_path / ".polaris" / "runtime"
    logs = runtime_root / "runs" / "factory_exact" / "logs"
    logs.mkdir(parents=True)
    event = {
        "event_id": "event-from-journal",
        "run_id": "factory_exact",
        "ts": "2026-08-21T00:00:00Z",
        "ts_epoch": 1.0,
        "role": "chief_engineer",
        "refs": {"context_snapshot_ref": "ec851e95f353eb000dab334b"},
    }
    with open(logs / "journal.norm.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    monkeypatch.setattr(diagnosis_service, "has_audit_store_factory", lambda: True)
    monkeypatch.setattr(
        diagnosis_service,
        "AuditUseCaseFacade",
        lambda *, runtime_root: SimpleNamespace(query_logs=lambda **_kwargs: []),
    )

    result = query_audit_diagnosis_trail(
        QueryAuditDiagnosisTrailV1(
            workspace=str(tmp_path),
            run_id="factory_exact",
            limit=50,
        )
    )

    assert result.ok is True
    assert result.status == "available"
    assert result.payload["total"] == 1
    assert result.payload["events"][0]["event_id"] == "event-from-journal"
