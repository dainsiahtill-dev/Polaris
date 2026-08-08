from __future__ import annotations

import fcntl
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import polaris.cells.control_plane.run_ledger.public.ledger as run_ledger_module
import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    ReadRunLedgerProjectionBarrierQueryV1,
    ReadRunLedgerProjectionQueryV1,
    ReadRunProvenanceBundleQueryV1,
    RunLedger,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_run_ledger_projection,
    build_tool_call_lifecycle_receipt,
    read_run_ledger_projection,
    read_run_ledger_projection_barrier,
    read_run_provenance_bundle,
    service as run_ledger_service,
    summarize_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.projection import _directed_effect_receipt_payload_hash
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
from polaris.cells.events.fact_stream.public.contracts import (
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
)
from polaris.cells.events.fact_stream.public.workspace_bootstrap import (
    bootstrap_fact_stream_workspace,
)
from polaris.kernelone.events.sourcing.models import EventEnvelope
from polaris.kernelone.storage import resolve_logical_path


@pytest.fixture(autouse=True)
def _bootstrap_run_ledger_fact_streams(tmp_path: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="run_ledger_public_service_tests",
        )
    )


def _control_plane_facts(workspace: Path, *, run_id: str) -> list[dict[str, Any]]:
    return list(
        query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream="execution.control_plane",
                run_id=run_id,
                strict_integrity=True,
            )
        ).events
    )


def _append_control_plane_fact(
    workspace: Path,
    *,
    run_id: str,
    event: dict[str, Any],
) -> Any:
    ledger = RunLedger(workspace, run_id=run_id)
    canonical_event = ledger.prepare_idempotent_event(event)
    event_type = str(canonical_event.get("event_type") or "control_plane_event").strip()
    return append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="execution.control_plane",
            event_type=event_type,
            payload={
                "schema_version": "execution.control_plane.fact.v1",
                "run_id": run_id,
                "event": canonical_event,
            },
            source="control_plane.run_ledger",
            run_id=run_id,
            task_id=str(canonical_event.get("task_id") or "").strip() or None,
            correlation_id=str(canonical_event.get("turn_id") or canonical_event.get("event_id") or "").strip() or None,
            idempotency_key=ledger.fact_idempotency_key(canonical_event),
            durability="fsync",
            strict_integrity=True,
        )
    )


def _append_raw_control_plane_fact(
    workspace: Path,
    *,
    run_id: str,
    payload: dict[str, Any],
    source: str = "unrelated.control_plane",
    idempotency_key: str = "unrelated:fact",
) -> Any:
    return append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="execution.control_plane",
            event_type="unrelated.control_plane.event",
            payload=payload,
            source=source,
            run_id=run_id,
            idempotency_key=idempotency_key,
            durability="fsync",
            strict_integrity=True,
        )
    )


def _canonical_projection_row(
    ledger: RunLedger,
    event: dict[str, Any],
    *,
    recorded_at: str = "2026-07-19T00:00:00+00:00",
) -> tuple[str, dict[str, Any]]:
    payload = ledger.prepare_idempotent_event(event)
    payload["recorded_at"] = recorded_at
    row = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    return row, payload


def _assert_projection_flock_available(ledger: RunLedger) -> None:
    with ledger.path.open("a+b") as probe:
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe.fileno(), fcntl.LOCK_UN)


def test_projection_query_rejects_project_scope_without_factory_run() -> None:
    with pytest.raises(ValueError, match="project_id requires factory_run_id"):
        ReadRunLedgerProjectionQueryV1(
            workspace="/tmp/polaris-run-ledger-contract",
            project_id="L1-01",
        )


def _append_task_runtime_execution_fact(
    workspace: Path,
    *,
    run_id: str,
    factory_run_id: str,
    project_id: str,
    task_id: str,
    event_type: str = "created",
    role_id: str = "",
    target_files: tuple[str, ...] = (),
) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type=event_type,
            source="run_ledger_scope_test",
            run_id=run_id,
            task_id=task_id,
            payload={
                "event_type": event_type,
                "run_id": run_id,
                "task_id": task_id,
                "factory_run_id": factory_run_id,
                "factory_bench_project_id": project_id,
                "task_row_snapshot": {
                    "id": task_id,
                    "metadata": {
                        "external_task_id": task_id,
                        "target_files": list(target_files),
                        "task_contract": {"target_files": list(target_files)},
                        "runtime_execution": {"role_id": role_id},
                    },
                },
            },
        )
    )


def _write_ledger_event(
    workspace: Path,
    *,
    run_id: str = "run-1",
    include_lifecycle: bool = True,
) -> None:
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
    events: list[dict[str, Any]] = [event]
    if include_lifecycle:
        events.append(_successful_tool_lifecycle_event(run_id=run_id))
    ledger_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n",
        encoding="utf-8",
    )


def _authoritative_directed_effect_receipt(*, run_id: str) -> dict[str, object]:
    payload = {
        "arguments_hash": "1" * 64,
        "authoritative": True,
        "batch_id": f"batch-{run_id}",
        "claim_grant_hash": "2" * 64,
        "context_id": f"context-{run_id}",
        "durable": True,
        "effect_call_id": None,
        "effect_operation_id": None,
        "normalized_tool_name": "write_file",
        "operation_id": f"operation-{run_id}",
        "parent_close_eligible": True,
        "physical_result_hash": "3" * 64,
        "plan_hash": None,
        "policy_evidence_hash": "4" * 64,
        "repair_binding_hash": None,
        "repair_contingency_kind": None,
        "repair_request_hash": None,
        "receipt_binding_hash": "5" * 64,
        "receipt_outcome": "succeeded",
        "schema_version": "roles.adapters.director_physical_effect_receipt.v2",
        "target_state_hash": "6" * 64,
        "tool_call_id": f"call-{run_id}",
    }
    receipt_hash = _directed_effect_receipt_payload_hash(payload)
    assert receipt_hash is not None
    return {
        **payload,
        "receipt_hash": receipt_hash,
        "receipt_id": f"director-physical-effect-{receipt_hash[:24]}",
    }


def _authoritative_directed_effect_receipt_commit(*, run_id: str) -> dict[str, object]:
    receipt = _authoritative_directed_effect_receipt(run_id=run_id)
    return {
        "code": "receipt_committed",
        "state": "RECEIPT_COMMITTED",
        "operation_id": receipt["operation_id"],
        "event_id": f"event-{run_id}",
        "receipt_ref": receipt["receipt_id"],
        "receipt_hash": receipt["receipt_hash"],
        "receipt_binding_hash": receipt["receipt_binding_hash"],
        "receipt_outcome": receipt["receipt_outcome"],
        "version": 3,
    }


def _successful_tool_lifecycle_event(
    *,
    run_id: str = "run-1",
    task_id: str = "TASK-1",
    project_id: str = "P1",
) -> dict[str, object]:
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": f"batch-{run_id}",
                "results": [
                    {
                        "call_id": f"call-{run_id}",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": _authoritative_directed_effect_receipt(run_id=run_id),
                        "effect_receipt_commit": _authoritative_directed_effect_receipt_commit(run_id=run_id),
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()
    return {
        "event_type": "tool_call_lifecycle",
        "run_id": run_id,
        "task_id": task_id,
        "project_id": project_id,
        "tool_call_lifecycle_receipt": lifecycle,
    }


def test_projection_before_director_execution_marks_lifecycle_not_required(tmp_path: Path) -> None:
    _write_ledger_event(tmp_path, run_id="run-pre-director", include_lifecycle=False)

    result = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="run-pre-director",
            include_migration_ledgers=True,
        )
    )

    lifecycle = result.projection["tool_lifecycle"]
    assert lifecycle["ok"] is True
    assert lifecycle["requirement"] is False
    assert lifecycle["requirement_status"] == "not_required"


def test_director_materialization_claim_requires_lifecycle_receipt(tmp_path: Path) -> None:
    run_id = "run-director-claim"
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id=run_id,
        factory_run_id="",
        project_id="P1",
        task_id="TASK-1",
        event_type="claimed",
        role_id="director",
        target_files=("src/main.py",),
    )
    _write_ledger_event(tmp_path, run_id=run_id, include_lifecycle=False)

    result = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id=run_id,
            include_migration_ledgers=True,
        )
    )

    lifecycle = result.projection["tool_lifecycle"]
    assert lifecycle["ok"] is False
    assert lifecycle["requirement"] is True
    assert lifecycle["requirement_status"] == "missing_required"
    assert lifecycle["required_task_keys"] == ["TASK-1"]
    assert lifecycle["missing_required_task_keys"] == ["TASK-1"]


def test_non_director_claim_does_not_activate_lifecycle_requirement(tmp_path: Path) -> None:
    run_id = "run-chief-engineer-claim"
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id=run_id,
        factory_run_id="",
        project_id="P1",
        task_id="TASK-1",
        event_type="claimed",
        role_id="chief_engineer",
        target_files=("src/main.py",),
    )
    _write_ledger_event(tmp_path, run_id=run_id, include_lifecycle=False)

    result = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id=run_id,
            include_migration_ledgers=True,
        )
    )

    lifecycle = result.projection["tool_lifecycle"]
    assert lifecycle["ok"] is True
    assert lifecycle["requirement"] is False
    assert lifecycle["requirement_status"] == "not_required"


def _append_successful_tool_lifecycle_event(
    workspace: Path,
    *,
    run_id: str,
) -> str:
    result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            event=_successful_tool_lifecycle_event(run_id=run_id),
        )
    )
    return str(result.receipt["event"]["append_id"])


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
    _append_successful_tool_lifecycle_event(tmp_path, run_id="run-1")

    ledger_path = Path(str(result.receipt["ledger_path"]))
    fact_receipt = result.receipt["fact_receipt"]
    assert fact_receipt["stream"] == "execution.control_plane"
    assert fact_receipt["appended_seq"] == 1
    # The NDJSON file is a rebuildable compatibility view, not authority.
    ledger_path.unlink()
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection

    assert ledger_path.parent == tmp_path / "runtime" / "control_plane" / "ledger"
    assert result.receipt["event"]["append_id"]
    assert projection["ok"] is True
    assert projection["projects"][0]["project_id"] == "P1"
    tool_receipt = projection["evidence_modalities"]["tool_receipt"]
    assert tool_receipt["present"] == 0
    assert projection["run_projection"]["gate_count"] == 1
    assert projection["run_projection"]["gates"][0]["name"] == "director_mutation"
    gate_tool_receipt = projection["run_projection"]["gates"][0]["evidence_modalities"]["tool_receipt"]
    assert gate_tool_receipt["metadata"]["legacy_receipt_count"] == 1
    assert projection["query_scope"] == {"run_id": "run-1", "factory_run_id": "", "project_id": ""}
    assert projection["consumed_run_ids"] == ["run-1"]


def test_append_run_ledger_event_uses_strict_fsync_fact_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[AppendFactEventCommandV1] = []

    def fake_append_fact_event(command: AppendFactEventCommandV1) -> SimpleNamespace:
        captured.append(command)
        return SimpleNamespace(
            event_id="fact-1",
            stream="execution.control_plane",
            storage_path="runtime/events/execution.control_plane.jsonl",
            appended_at="2026-07-19T00:00:00+00:00",
            appended_seq=1,
        )

    monkeypatch.setattr(run_ledger_service, "append_fact_event", fake_append_fact_event)

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-strict",
            event={"event_type": "gate_evaluated"},
        )
    )

    assert len(captured) == 1
    assert captured[0].stream == "execution.control_plane"
    assert captured[0].strict_integrity is True
    assert captured[0].durability == "fsync"
    assert captured[0].idempotency_key
    assert "recorded_at" not in captured[0].payload["event"]


def test_append_run_ledger_event_preserves_fact_projection_publish_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[str] = []
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")

    def fake_append_fact_event(_command: AppendFactEventCommandV1) -> SimpleNamespace:
        operations.append("fact")
        return SimpleNamespace(
            event_id="fact-order",
            stream="execution.control_plane",
            storage_path="runtime/events/execution.control_plane.jsonl",
            appended_at="2026-07-19T00:00:00+00:00",
            appended_seq=1,
        )

    original_append_serialized_row = RunLedger._append_serialized_row_locked

    def observe_projection_write(self: RunLedger, handle: Any, serialized_row: str) -> None:
        operations.append("projection")
        original_append_serialized_row(self, handle, serialized_row)

    def fake_publish(**_kwargs: Any) -> bool:
        operations.append("publish")
        return True

    monkeypatch.setattr(run_ledger_service, "append_fact_event", fake_append_fact_event)
    monkeypatch.setattr(RunLedger, "_append_serialized_row_locked", observe_projection_write)
    monkeypatch.setattr(run_ledger_service, "_publish_run_ledger_projection_update", fake_publish)

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-order",
            event={"event_type": "gate_evaluated"},
        )
    )

    assert operations == ["fact", "projection", "publish"]


def test_append_run_ledger_event_rejects_legacy_non_strict_control_plane_stream(tmp_path: Path) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="execution.control_plane",
            event_type="legacy.recorded",
            source="legacy_writer",
            payload={"run_id": "run-legacy"},
        )
    )

    with pytest.raises(FactStreamError) as caught:
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-strict-after-legacy",
                event={"event_type": "gate_evaluated"},
            )
        )

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_failure_code"] == "missing_integrity_digest"


def test_run_ledger_append_once_dedupes_and_rejects_semantic_conflict_under_file_lock(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-once")
    event = {
        "event_id": "event-once",
        "event_type": "gate_evaluated",
        "stage": "qa",
    }

    first = ledger.append_event_once(event, recorded_at="2026-07-19T00:00:00+00:00")
    replay = ledger.append_event_once(event, recorded_at="2099-01-01T00:00:00+00:00")

    assert replay == first
    assert ledger.read_events() == [first["event"]]
    with pytest.raises(ValueError, match="event_id already exists with different semantic content"):
        ledger.append_event_once(
            {**event, "stage": "director"},
            recorded_at="2026-07-19T00:00:01+00:00",
        )


def test_run_ledger_append_once_rejects_preserved_identity_semantic_tamper_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "0")
    command = AppendRunLedgerEventCommandV1(
        workspace=str(tmp_path),
        run_id="run-tampered-projection",
        event={"event_type": "gate_evaluated", "stage": "qa"},
    )
    first = append_run_ledger_event(command).receipt
    tampered = dict(first["event"])
    tampered["stage"] = "director"
    ledger = RunLedger(tmp_path, run_id="run-tampered-projection")
    ledger.path.write_text(json.dumps(tampered, ensure_ascii=False) + "\n", encoding="utf-8")
    published: list[dict[str, Any]] = []
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")

    def record_publish(**kwargs: Any) -> bool:
        published.append(kwargs)
        return True

    monkeypatch.setattr(
        run_ledger_service,
        "_publish_run_ledger_projection_update",
        record_publish,
    )

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt"):
        append_run_ledger_event(command)

    assert published == []


@pytest.mark.parametrize(
    "corrupt_row",
    (
        "[]\n",
        '{"event_id":"duplicate","event_id":"duplicate"}\n',
        '{"event_id":"duplicate","poison":NaN}\n',
    ),
    ids=("non_object", "duplicate_key", "non_finite"),
)
def test_run_ledger_append_once_strictly_rejects_corrupt_ndjson_rows(
    tmp_path: Path,
    corrupt_row: str,
) -> None:
    ledger = RunLedger(tmp_path, run_id="run-corrupt-row")
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(corrupt_row, encoding="utf-8")

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt"):
        ledger.append_event_once(
            {"event_type": "gate_evaluated"},
            recorded_at="2026-07-19T00:00:00+00:00",
        )


def test_run_ledger_append_once_releases_lock_after_corrupt_projection_error(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-corrupt-lock")
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text('{"event_id":"first","event_id":"second"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt"):
        ledger.append_event_once(
            {"event_type": "gate_evaluated"},
            recorded_at="2026-07-19T00:00:00+00:00",
        )

    ledger.path.write_text("", encoding="utf-8")
    receipt = ledger.append_event_once(
        {"event_type": "gate_evaluated"},
        recorded_at="2026-07-19T00:00:00+00:00",
    )
    assert ledger.read_events() == [receipt["event"]]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("content_id", 7),
        ("content_id", True),
        ("content_id", "a" * 63),
        ("content_id", "A" * 64),
        ("append_id", 7),
        ("append_id", False),
        ("append_id", "b" * 65),
        ("append_id", "B" * 64),
        ("event_id", 7),
        ("event_id", True),
        ("event_id", ""),
        ("event_id", " drift"),
        ("event_id", "x" * 257),
    ),
)
def test_prepare_idempotent_event_rejects_noncanonical_identity_inputs(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=f"invalid_{field}"):
        RunLedger(tmp_path, run_id="run-invalid-id").prepare_idempotent_event(
            {"event_type": "gate_evaluated", field: value}
        )


def test_prepare_idempotent_event_rejects_string_subclass_identity_inputs(tmp_path: Path) -> None:
    class StringSubclass(str):
        pass

    ledger = RunLedger(tmp_path, run_id="run-string-subclass")
    prepared = ledger.prepare_idempotent_event({"event_type": "gate_evaluated"})
    for field, value in (
        ("content_id", StringSubclass(prepared["content_id"])),
        ("append_id", StringSubclass(prepared["append_id"])),
        ("event_id", StringSubclass("caller-event")),
    ):
        with pytest.raises(ValueError, match=f"invalid_{field}"):
            ledger.prepare_idempotent_event({"event_type": "gate_evaluated", field: value})


@pytest.mark.parametrize(
    "recorded_at",
    (
        7,
        True,
        "",
        " 2026-07-19T00:00:00+00:00",
        "2026-07-19T00:00:00",
        "not-a-time",
    ),
)
def test_run_ledger_append_once_rejects_noncanonical_recorded_at(
    tmp_path: Path,
    recorded_at: object,
) -> None:
    with pytest.raises(ValueError, match="invalid_recorded_at"):
        RunLedger(tmp_path, run_id="run-invalid-time").append_event_once(
            {"event_type": "gate_evaluated"},
            recorded_at=recorded_at,  # type: ignore[arg-type]
        )


def test_run_ledger_append_once_rejects_recorded_at_string_subclass(tmp_path: Path) -> None:
    class StringSubclass(str):
        pass

    with pytest.raises(ValueError, match="invalid_recorded_at"):
        RunLedger(tmp_path, run_id="run-time-subclass").append_event_once(
            {"event_type": "gate_evaluated"},
            recorded_at=StringSubclass("2026-07-19T00:00:00+00:00"),
        )


def test_idempotent_canonical_hash_rejects_nonfinite_but_legacy_append_remains_compatible(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-nonfinite")

    with pytest.raises(ValueError, match="non_finite"):
        ledger.prepare_idempotent_event({"event_type": "metric", "value": float("nan")})

    legacy = ledger.append_event(
        {
            "event_type": "metric",
            "value": float("nan"),
            "recorded_at": "2026-07-19T00:00:00+00:00",
        }
    )
    assert math.isnan(legacy["event"]["value"])


def test_run_ledger_append_once_rejects_missing_final_newline(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-no-final-newline")
    receipt = ledger.append_event_once(
        {"event_type": "gate_evaluated"},
        recorded_at="2026-07-19T00:00:00+00:00",
    )
    ledger.path.write_text(json.dumps(receipt["event"], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt"):
        ledger.append_event_once(
            {"event_type": "gate_evaluated"},
            recorded_at="2026-07-19T00:00:00+00:00",
        )


def test_run_ledger_append_once_treats_only_lf_as_ndjson_row_separator(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-unicode-separator")
    event = {"event_type": "gate_evaluated", "note": "before\u2028after"}

    first = ledger.append_event_once(event, recorded_at="2026-07-19T00:00:00+00:00")
    replay = ledger.append_event_once(event, recorded_at="2099-01-01T00:00:00+00:00")

    assert replay == first


def test_run_ledger_append_once_rejects_projection_row_and_byte_bounds(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-bounds")
    receipt = ledger.append_event_once(
        {"event_type": "gate_evaluated"},
        recorded_at="2026-07-19T00:00:00+00:00",
    )
    row = json.dumps(receipt["event"], ensure_ascii=False) + "\n"
    ledger.path.write_text(row * 4097, encoding="utf-8")
    with pytest.raises(ValueError, match="run_ledger_projection_corrupt"):
        ledger.append_event_once(
            {"event_type": "gate_evaluated"},
            recorded_at="2026-07-19T00:00:00+00:00",
        )

    ledger.path.write_text(" " * (8 * 1024 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="run_ledger_projection_corrupt"):
        ledger.append_event_once(
            {"event_type": "gate_evaluated"},
            recorded_at="2026-07-19T00:00:00+00:00",
        )


def test_run_ledger_append_once_exact_row_limit_replays_but_rejects_new_append(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-exact-row-limit")
    rows: list[str] = []
    events: list[dict[str, Any]] = []
    for index in range(4096):
        event = {
            "event_id": f"event-{index}",
            "event_type": "gate_evaluated",
            "index": index,
        }
        row, _payload = _canonical_projection_row(ledger, event)
        rows.append(row)
        events.append(event)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text("".join(rows), encoding="utf-8")
    before = ledger.path.read_bytes()

    replay = ledger.append_event_once(events[-1], recorded_at="2099-01-01T00:00:00+00:00")
    assert replay["event"]["event_id"] == "event-4095"

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt"):
        ledger.append_event_once(
            {"event_id": "event-new", "event_type": "gate_evaluated"},
            recorded_at="2026-07-19T00:00:01+00:00",
        )

    assert ledger.path.read_bytes() == before
    assert (
        ledger.append_event_once(events[0], recorded_at="2099-01-01T00:00:00+00:00")["event"]["event_id"] == "event-0"
    )


def test_run_ledger_append_once_exact_byte_limit_replays_but_rejects_new_append(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-exact-byte-limit")
    max_bytes = 8 * 1024 * 1024
    base_event = {
        "event_id": "event-exact-bytes",
        "event_type": "gate_evaluated",
        "filler": "",
    }
    base_row, _payload = _canonical_projection_row(ledger, base_event)
    filler_length = max_bytes - len(base_row.encode("utf-8"))
    event = {**base_event, "filler": "x" * filler_length}
    row, _payload = _canonical_projection_row(ledger, event)
    assert len(row.encode("utf-8")) == max_bytes
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(row, encoding="utf-8")
    before = ledger.path.read_bytes()

    replay = ledger.append_event_once(event, recorded_at="2099-01-01T00:00:00+00:00")
    assert replay["event"]["event_id"] == "event-exact-bytes"

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt"):
        ledger.append_event_once(
            {"event_id": "event-new", "event_type": "gate_evaluated"},
            recorded_at="2026-07-19T00:00:01+00:00",
        )

    assert ledger.path.read_bytes() == before
    assert ledger.append_event_once(event, recorded_at="2099-01-01T00:00:00+00:00") == replay


def test_public_append_exact_row_limit_rejects_before_fact_and_preserves_projection(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-public-row-limit")
    rows = [
        _canonical_projection_row(
            ledger,
            {"event_id": f"event-{index}", "event_type": "gate_evaluated", "index": index},
        )[0]
        for index in range(4096)
    ]
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text("".join(rows), encoding="utf-8")
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt:prospective_row_limit_exceeded"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-public-row-limit",
                event={"event_id": "event-new", "event_type": "gate_evaluated"},
            )
        )

    assert _control_plane_facts(tmp_path, run_id="run-public-row-limit") == []
    assert ledger.path.read_bytes() == before


def test_public_append_exact_byte_limit_rejects_before_fact_and_preserves_projection(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-public-byte-limit")
    max_bytes = 8 * 1024 * 1024
    base_event = {"event_id": "event-exact-bytes", "event_type": "gate_evaluated", "filler": ""}
    base_row, _payload = _canonical_projection_row(ledger, base_event)
    event = {**base_event, "filler": "x" * (max_bytes - len(base_row.encode("utf-8")))}
    row, _payload = _canonical_projection_row(ledger, event)
    assert len(row.encode("utf-8")) == max_bytes
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(row, encoding="utf-8")
    before = ledger.path.read_bytes()
    fact_calls: list[AppendFactEventCommandV1] = []

    def fake_append_fact_event(command: AppendFactEventCommandV1) -> SimpleNamespace:
        fact_calls.append(command)
        return SimpleNamespace(
            event_id="fact-exact-byte-replay",
            stream="execution.control_plane",
            storage_path="runtime/events/execution.control_plane.jsonl",
            appended_at="2026-07-19T00:00:00+00:00",
            appended_seq=1,
        )

    command = AppendRunLedgerEventCommandV1(
        workspace=str(tmp_path),
        run_id="run-public-byte-limit",
        event=event,
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(run_ledger_service, "append_fact_event", fake_append_fact_event)
        first_replay = append_run_ledger_event(command).receipt
        second_replay = append_run_ledger_event(command).receipt

        assert first_replay == second_replay
        assert len(fact_calls) == 2
        assert first_replay["fact_receipt"]["event_id"] == "fact-exact-byte-replay"
        assert ledger.path.read_bytes() == before

        with pytest.raises(ValueError, match="run_ledger_projection_corrupt:prospective_byte_limit_exceeded"):
            append_run_ledger_event(
                AppendRunLedgerEventCommandV1(
                    workspace=str(tmp_path),
                    run_id="run-public-byte-limit",
                    event={"event_id": "event-new", "event_type": "gate_evaluated"},
                )
            )

        assert len(fact_calls) == 2

    assert _control_plane_facts(tmp_path, run_id="run-public-byte-limit") == []
    assert ledger.path.read_bytes() == before


def test_public_append_concurrent_row_boundary_has_no_overflow_or_fact_orphan(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-public-concurrent-boundary")
    rows = [
        _canonical_projection_row(
            ledger,
            {"event_id": f"event-{index}", "event_type": "gate_evaluated", "index": index},
        )[0]
        for index in range(4095)
    ]
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text("".join(rows), encoding="utf-8")
    start = Barrier(2)

    def invoke(index: int) -> tuple[str, str]:
        start.wait()
        try:
            result = append_run_ledger_event(
                AppendRunLedgerEventCommandV1(
                    workspace=str(tmp_path),
                    run_id="run-public-concurrent-boundary",
                    event={"event_id": f"event-new-{index}", "event_type": "gate_evaluated"},
                )
            )
        except ValueError as exc:
            return "failed", str(exc)
        return "completed", str(result.receipt["event"]["event_id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, range(2)))

    assert sorted(status for status, _detail in outcomes) == ["completed", "failed"]
    assert any("prospective_row_limit_exceeded" in detail for status, detail in outcomes if status == "failed")
    assert len(ledger.read_events()) == 4096
    facts_before_replay = _control_plane_facts(tmp_path, run_id="run-public-concurrent-boundary")
    assert len(facts_before_replay) == 1
    projection_before_replay = ledger.path.read_bytes()
    successful_event_id = next(detail for status, detail in outcomes if status == "completed")

    replay = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-public-concurrent-boundary",
            event={"event_id": successful_event_id, "event_type": "gate_evaluated"},
        )
    ).receipt

    assert replay["fact_receipt"]["event_id"] == facts_before_replay[0]["event_id"]
    assert replay["fact_receipt"]["appended_seq"] == facts_before_replay[0]["seq"]
    assert _control_plane_facts(tmp_path, run_id="run-public-concurrent-boundary") == facts_before_replay
    assert ledger.path.read_bytes() == projection_before_replay


def test_public_append_fact_failure_leaves_no_projection_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = RunLedger(tmp_path, run_id="run-fact-failure")

    def fail_fact(_command: AppendFactEventCommandV1) -> Any:
        raise OSError("fact fsync failed")

    monkeypatch.setattr(run_ledger_service, "append_fact_event", fail_fact)

    with pytest.raises(OSError, match="fact fsync failed"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-fact-failure",
                event={"event_type": "gate_evaluated"},
            )
        )

    assert ledger.read_events() == []
    assert not ledger.path.is_file() or ledger.path.read_bytes() == b""


@pytest.mark.parametrize("failure_stage", ("write", "flush"))
def test_public_append_capturable_write_and_flush_errors_rollback_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    run_id = f"run-{failure_stage}-rollback"
    event = {"event_type": "gate_evaluated", "stage": "qa"}
    command = AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event)
    ledger = RunLedger(tmp_path, run_id=run_id)
    original_write = RunLedger._write_projection_bytes_locked

    with monkeypatch.context() as failure_patch:
        if failure_stage == "write":

            def partial_write_then_fail(self: RunLedger, handle: Any, serialized_row: bytes) -> int:
                original_write(self, handle, serialized_row[:17])
                raise OSError("projection write failed")

            failure_patch.setattr(RunLedger, "_write_projection_bytes_locked", partial_write_then_fail)
        else:
            failure_patch.setattr(
                RunLedger,
                "_flush_projection_locked",
                lambda _self, _handle: (_ for _ in ()).throw(OSError("projection flush failed")),
            )

        with pytest.raises(OSError, match=f"projection {failure_stage} failed"):
            append_run_ledger_event(command)

    facts_after_failure = _control_plane_facts(tmp_path, run_id=run_id)
    assert len(facts_after_failure) == 1
    assert ledger.path.read_bytes() == b""

    receipt = append_run_ledger_event(command).receipt
    expected_row, _payload = _canonical_projection_row(
        ledger,
        event,
        recorded_at=str(facts_after_failure[0]["occurred_at"]),
    )
    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_after_failure
    assert ledger.path.read_bytes() == expected_row.encode()
    assert ledger.read_events() == [receipt["event"]]


def test_public_append_fsync_error_rolls_back_then_reuses_fact_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-fsync-rollback"
    event = {"event_type": "gate_evaluated", "stage": "qa"}
    command = AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event)
    ledger = RunLedger(tmp_path, run_id=run_id)
    original_fsync = RunLedger._fsync_projection_locked
    fsync_attempts = 0

    def fail_first_fsync(self: RunLedger, handle: Any) -> None:
        nonlocal fsync_attempts
        fsync_attempts += 1
        if fsync_attempts == 1:
            raise OSError("projection fsync failed")
        original_fsync(self, handle)

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(RunLedger, "_fsync_projection_locked", fail_first_fsync)
        with pytest.raises(OSError, match="projection fsync failed"):
            append_run_ledger_event(command)

    facts_after_failure = _control_plane_facts(tmp_path, run_id=run_id)
    assert len(facts_after_failure) == 1
    assert ledger.path.read_bytes() == b""

    receipt = append_run_ledger_event(command).receipt
    expected_row, _payload = _canonical_projection_row(
        ledger,
        event,
        recorded_at=str(facts_after_failure[0]["occurred_at"]),
    )
    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_after_failure
    assert ledger.path.read_bytes() == expected_row.encode()
    assert ledger.read_events() == [receipt["event"]]


def test_public_append_recovers_ascii_partial_tail_from_unique_existing_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-partial-ascii"
    event = {"event_type": "gate_evaluated", "stage": "qa", "detail": "ascii-tail"}
    command = AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event)
    ledger = RunLedger(tmp_path, run_id=run_id)
    original_write = RunLedger._write_projection_bytes_locked

    def write_partial_then_fail(self: RunLedger, handle: Any, serialized_row: bytes) -> int:
        original_write(self, handle, serialized_row[:41])
        raise OSError("simulated process interruption")

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(RunLedger, "_write_projection_bytes_locked", write_partial_then_fail)
        crash_patch.setattr(
            RunLedger,
            "_rollback_projection_locked",
            lambda _self, _handle, _offset: (_ for _ in ()).throw(OSError("rollback unavailable")),
        )
        with pytest.raises(RuntimeError, match="run_ledger_projection_write_ambiguous"):
            append_run_ledger_event(command)

    partial_bytes = ledger.path.read_bytes()
    facts_after_crash = _control_plane_facts(tmp_path, run_id=run_id)
    assert partial_bytes and not partial_bytes.endswith(b"\n")
    assert len(facts_after_crash) == 1

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("recovery must not append Fact")),
    )
    recovered = append_run_ledger_event(command).receipt
    expected_row, _payload = _canonical_projection_row(
        ledger,
        event,
        recorded_at=str(facts_after_crash[0]["occurred_at"]),
    )
    assert recovered["fact_receipt"]["event_id"] == facts_after_crash[0]["event_id"]
    assert recovered["fact_receipt"]["appended_seq"] == facts_after_crash[0]["seq"]
    assert ledger.path.read_bytes() == expected_row.encode()
    assert ledger.read_events() == [recovered["event"]]


def test_public_append_recovers_partial_tail_split_inside_multibyte_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-partial-multibyte"
    event = {"event_type": "gate_evaluated", "detail": "边界恢复"}
    command = AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event)
    ledger = RunLedger(tmp_path, run_id=run_id)
    original_write = RunLedger._write_projection_bytes_locked

    def split_multibyte_then_fail(self: RunLedger, handle: Any, serialized_row: bytes) -> int:
        marker = "边".encode()
        marker_offset = serialized_row.index(marker)
        original_write(self, handle, serialized_row[: marker_offset + 1])
        raise OSError("simulated multibyte interruption")

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(RunLedger, "_write_projection_bytes_locked", split_multibyte_then_fail)
        crash_patch.setattr(
            RunLedger,
            "_rollback_projection_locked",
            lambda _self, _handle, _offset: (_ for _ in ()).throw(OSError("rollback unavailable")),
        )
        with pytest.raises(RuntimeError, match="run_ledger_projection_write_ambiguous"):
            append_run_ledger_event(command)

    partial_bytes = ledger.path.read_bytes()
    facts_after_crash = _control_plane_facts(tmp_path, run_id=run_id)
    with pytest.raises(UnicodeDecodeError):
        partial_bytes.decode("utf-8")

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("recovery must not append Fact")),
    )
    recovered = append_run_ledger_event(command).receipt
    expected_row, _payload = _canonical_projection_row(
        ledger,
        event,
        recorded_at=str(facts_after_crash[0]["occurred_at"]),
    )
    assert ledger.path.read_bytes() == expected_row.encode()
    assert ledger.read_events() == [recovered["event"]]


def test_public_append_full_row_before_error_replays_fact_and_fsyncs_existing_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-full-write-before-error"
    event = {"event_type": "gate_evaluated", "stage": "qa"}
    command = AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event)
    ledger = RunLedger(tmp_path, run_id=run_id)
    original_write = RunLedger._write_projection_bytes_locked

    def write_full_then_fail(self: RunLedger, handle: Any, serialized_row: bytes) -> int:
        original_write(self, handle, serialized_row)
        raise OSError("ack lost after full write")

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(RunLedger, "_write_projection_bytes_locked", write_full_then_fail)
        crash_patch.setattr(
            RunLedger,
            "_rollback_projection_locked",
            lambda _self, _handle, _offset: (_ for _ in ()).throw(OSError("rollback unavailable")),
        )
        with pytest.raises(RuntimeError, match="run_ledger_projection_write_ambiguous"):
            append_run_ledger_event(command)

    facts_after_crash = _control_plane_facts(tmp_path, run_id=run_id)
    bytes_after_crash = ledger.path.read_bytes()
    assert len(facts_after_crash) == 1
    assert bytes_after_crash.endswith(b"\n")

    original_append_fact = run_ledger_service.append_fact_event
    original_fsync = RunLedger._fsync_projection_locked
    fact_replay_calls = 0
    projection_fsync_calls = 0

    def observe_fact_replay(fact_command: AppendFactEventCommandV1) -> Any:
        nonlocal fact_replay_calls
        fact_replay_calls += 1
        return original_append_fact(fact_command)

    def observe_projection_fsync(self: RunLedger, handle: Any) -> None:
        nonlocal projection_fsync_calls
        projection_fsync_calls += 1
        original_fsync(self, handle)

    monkeypatch.setattr(run_ledger_service, "append_fact_event", observe_fact_replay)
    monkeypatch.setattr(RunLedger, "_fsync_projection_locked", observe_projection_fsync)
    replay = append_run_ledger_event(command).receipt

    assert fact_replay_calls == 1
    assert projection_fsync_calls == 1
    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_after_crash
    assert ledger.path.read_bytes() == bytes_after_crash
    assert ledger.read_events() == [replay["event"]]


def test_public_append_orphan_partial_tail_without_fact_fails_before_fact_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-orphan-partial"
    event = {"event_type": "gate_evaluated", "stage": "qa"}
    ledger = RunLedger(tmp_path, run_id=run_id)
    serialized_row, _payload = _canonical_projection_row(ledger, event)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(serialized_row.encode()[:37])
    fact_mutations = 0

    def reject_fact_mutation(_command: AppendFactEventCommandV1) -> Any:
        nonlocal fact_mutations
        fact_mutations += 1
        raise AssertionError("orphan recovery must not mutate FactStream")

    monkeypatch.setattr(run_ledger_service, "append_fact_event", reject_fact_mutation)

    with pytest.raises(ValueError, match="partial_tail_fact_missing"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event))

    assert fact_mutations == 0
    assert _control_plane_facts(tmp_path, run_id=run_id) == []
    assert ledger.path.read_bytes() == serialized_row.encode()[:37]


def test_public_append_mismatched_partial_tail_with_fact_fails_before_fact_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-mismatched-partial"
    event = {"event_type": "gate_evaluated", "stage": "qa"}
    ledger = RunLedger(tmp_path, run_id=run_id)
    _append_control_plane_fact(tmp_path, run_id=run_id, event=event)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(b'{"not":"the-current-row-prefix"')
    fact_mutations = 0

    def reject_fact_mutation(_command: AppendFactEventCommandV1) -> Any:
        nonlocal fact_mutations
        fact_mutations += 1
        raise AssertionError("mismatched recovery must not mutate FactStream")

    monkeypatch.setattr(run_ledger_service, "append_fact_event", reject_fact_mutation)

    with pytest.raises(ValueError, match="partial_tail_mismatch"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event))

    assert fact_mutations == 0
    assert len(_control_plane_facts(tmp_path, run_id=run_id)) == 1
    assert ledger.path.read_bytes() == b'{"not":"the-current-row-prefix"'


def test_public_append_partial_tail_rejects_when_two_run_facts_are_unprojected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-two-unprojected-facts"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    ledger = RunLedger(tmp_path, run_id=run_id)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(b"{")
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    projection_before = ledger.path.read_bytes()

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("ambiguous recovery must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_unprojected_fact_ambiguity"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before


def test_public_append_complete_fact_a_row_then_partial_fact_b_recovers_b(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-projected-a-partial-b"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    ledger = RunLedger(tmp_path, run_id=run_id)
    row_a, payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    partial_b = row_b.encode()[:90]
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + partial_b)
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("recovery must not append Fact")),
    )
    recovered = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b)
    ).receipt

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == row_a.encode() + row_b.encode()
    assert ledger.read_events() == [payload_a, payload_b]
    assert recovered["fact_receipt"]["event_id"] == fact_b.event_id


def test_public_append_partial_tail_rejects_extra_unprojected_nonmatching_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-extra-unprojected-fact"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    event_c = {"event_id": "event-c", "event_type": "gate_evaluated", "stage": "director"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    _append_control_plane_fact(tmp_path, run_id=run_id, event=event_c)
    ledger = RunLedger(tmp_path, run_id=run_id)
    row_a, _payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, _payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + row_b.encode()[:90])
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    projection_before = ledger.path.read_bytes()

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("incomplete recovery must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_unprojected_fact_ambiguity"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before


def test_public_append_partial_tail_rejects_projected_future_fact_before_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-projected-future-before-current"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    event_c = {"event_id": "event-c", "event_type": "gate_evaluated", "stage": "director"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    fact_c = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_c)
    ledger = RunLedger(tmp_path, run_id=run_id)
    row_a, _payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, _payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    row_c, _payload_c = _canonical_projection_row(ledger, event_c, recorded_at=fact_c.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + row_c.encode() + row_b.encode()[:90])
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    projection_before = ledger.path.read_bytes()

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("out-of-order recovery must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_projection_fact_order_mismatch"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before


def test_public_append_partial_tail_rejects_swapped_complete_projection_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-swapped-projection-prefix"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    event_c = {"event_id": "event-c", "event_type": "gate_evaluated", "stage": "director"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    fact_c = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_c)
    ledger = RunLedger(tmp_path, run_id=run_id)
    row_a, _payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, _payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    row_c, _payload_c = _canonical_projection_row(ledger, event_c, recorded_at=fact_c.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_b.encode() + row_a.encode() + row_c.encode()[:90])
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    projection_before = ledger.path.read_bytes()

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("swapped recovery must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_projection_fact_order_mismatch"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_c))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before


def test_public_append_partial_tail_rejects_current_fact_when_it_is_not_last_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-current-not-last-authority"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    event_c = {"event_id": "event-c", "event_type": "gate_evaluated", "stage": "director"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    _append_control_plane_fact(tmp_path, run_id=run_id, event=event_c)
    ledger = RunLedger(tmp_path, run_id=run_id)
    row_a, _payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, _payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + row_b.encode() + row_b.encode()[:90])
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    projection_before = ledger.path.read_bytes()

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("middle authority must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_after_existing_event"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before


def test_public_append_partial_tail_rejects_disguised_run_ledger_payload_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-disguised-ledger-shape"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    event_c = {"event_id": "event-c", "event_type": "gate_evaluated", "stage": "director"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    ledger = RunLedger(tmp_path, run_id=run_id)
    canonical_c = ledger.prepare_idempotent_event(event_c)
    _append_raw_control_plane_fact(
        tmp_path,
        run_id=run_id,
        payload={
            "schema_version": "disguised.fact.v1",
            "run_id": run_id,
            "event": canonical_c,
        },
        source="disguised.source",
        idempotency_key="disguised:event-c",
    )
    row_a, _payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, _payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + row_b.encode()[:90])
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    projection_before = ledger.path.read_bytes()

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("disguised recovery must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_fact_noncanonical"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before


def test_public_append_partial_tail_rejects_broken_shape_with_ledger_event_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-ledger-event-identity"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    event_c = {"event_id": "event-c", "event_type": "gate_evaluated", "stage": "director"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    ledger = RunLedger(tmp_path, run_id=run_id)
    canonical_c = ledger.prepare_idempotent_event(event_c)
    _append_raw_control_plane_fact(
        tmp_path,
        run_id=run_id,
        payload={
            "schema_version": "disguised.fact.v1",
            "run_id": run_id,
            "event": canonical_c,
            "extra": "break-the-run-ledger-envelope-shape",
        },
        source="disguised.source",
        idempotency_key="disguised:event-c",
    )
    row_a, _payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, _payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + row_b.encode()[:90])
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    projection_before = ledger.path.read_bytes()

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("identity recovery must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_fact_noncanonical"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before


def test_public_append_partial_tail_ignores_unrelated_control_plane_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-unrelated-control-plane-fact"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    _append_raw_control_plane_fact(
        tmp_path,
        run_id=run_id,
        payload={"schema_version": "unrelated.control_plane.v1", "message": "ordinary fact"},
    )
    ledger = RunLedger(tmp_path, run_id=run_id)
    row_a, payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + row_b.encode()[:90])
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("recovery must not append Fact")),
    )
    recovered = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b)
    ).receipt

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == row_a.encode() + row_b.encode()
    assert ledger.read_events() == [payload_a, payload_b]
    assert recovered["fact_receipt"]["event_id"] == fact_b.event_id


def test_public_append_partial_tail_rejects_duplicate_top_level_fact_event_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-duplicate-fact-event-id"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    fact_path = Path(resolve_logical_path(str(tmp_path), fact_b.storage_path))
    records = [json.loads(line) for line in fact_path.read_text(encoding="utf-8").splitlines()]
    record_b = next(record for record in records if record["event_id"] == fact_b.event_id)
    record_b["event_id"] = fact_a.event_id
    record_b["integrity_digest"] = EventEnvelope.integrity_digest_for_record(record_b)
    fact_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for record in records
        ),
        encoding="utf-8",
    )

    ledger = RunLedger(tmp_path, run_id=run_id)
    row_a, _payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, _payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + row_b.encode()[:90])
    facts_before = fact_path.read_bytes()
    projection_before = ledger.path.read_bytes()

    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("duplicate authority must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_fact_event_id_duplicate"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b))

    assert fact_path.read_bytes() == facts_before
    assert ledger.path.read_bytes() == projection_before


def test_public_append_partial_tail_rejects_duplicate_seq_from_unrelated_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-duplicate-fact-seq"
    event_a = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "pm"}
    event_b = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "qa"}
    fact_a = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_a)
    fact_b = _append_control_plane_fact(tmp_path, run_id=run_id, event=event_b)
    unrelated = _append_raw_control_plane_fact(
        tmp_path,
        run_id=run_id,
        payload={"schema_version": "unrelated.control_plane.v1", "message": "ordinary fact"},
    )
    fact_path = Path(resolve_logical_path(str(tmp_path), unrelated.storage_path))
    ledger = RunLedger(tmp_path, run_id=run_id)
    row_a, _payload_a = _canonical_projection_row(ledger, event_a, recorded_at=fact_a.appended_at)
    row_b, _payload_b = _canonical_projection_row(ledger, event_b, recorded_at=fact_b.appended_at)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(row_a.encode() + row_b.encode()[:90])
    facts_before = fact_path.read_bytes()
    projection_before = ledger.path.read_bytes()
    original_query = run_ledger_service.query_fact_events

    def query_with_duplicate_unrelated_seq(query: QueryFactEventsV1) -> Any:
        page = original_query(query)
        events = [dict(fact) for fact in page.events]
        unrelated_record = next(record for record in events if record["event_id"] == unrelated.event_id)
        unrelated_record["seq"] = fact_a.appended_seq
        return SimpleNamespace(events=tuple(events), next_offset=page.next_offset)

    monkeypatch.setattr(run_ledger_service, "query_fact_events", query_with_duplicate_unrelated_seq)
    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("duplicate authority must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_fact_seq_duplicate"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event_b))

    assert fact_path.read_bytes() == facts_before
    assert ledger.path.read_bytes() == projection_before


@pytest.mark.parametrize(
    ("next_offsets", "expected_error"),
    [
        ((2, 2), "partial_tail_fact_page_offset_stalled"),
        ((3, 2), "partial_tail_fact_page_offset_regressed"),
        ((2, 3, 2), "partial_tail_fact_page_offset_cycle"),
        ((1, 2, 3, 4, 5, 6), "partial_tail_fact_page_limit_exceeded"),
        ((True,), "partial_tail_fact_page_offset_noncanonical"),
    ],
)
def test_public_append_partial_tail_rejects_invalid_fact_pagination_without_lock_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    next_offsets: tuple[object, ...],
    expected_error: str,
) -> None:
    run_id = f"run-invalid-pagination-{expected_error}"
    event = {"event_type": "gate_evaluated", "stage": "qa"}
    ledger = RunLedger(tmp_path, run_id=run_id)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(b"{")
    projection_before = ledger.path.read_bytes()
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    query_calls = 0

    def invalid_pagination(_query: QueryFactEventsV1) -> Any:
        nonlocal query_calls
        if query_calls >= len(next_offsets):
            raise AssertionError("pagination proof must terminate before another query")
        next_offset = next_offsets[query_calls]
        query_calls += 1
        return SimpleNamespace(events=(), next_offset=next_offset)

    monkeypatch.setattr(run_ledger_service, "query_fact_events", invalid_pagination)
    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("invalid pagination must not append Fact")),
    )

    with pytest.raises(ValueError, match=expected_error):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before
    _assert_projection_flock_available(ledger)


def test_public_append_partial_tail_rejects_fact_record_limit_without_lock_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-fact-proof-record-limit"
    event = {"event_type": "gate_evaluated", "stage": "qa"}
    ledger = RunLedger(tmp_path, run_id=run_id)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_bytes(b"{")
    projection_before = ledger.path.read_bytes()
    facts_before = _control_plane_facts(tmp_path, run_id=run_id)
    oversized_page = tuple(
        {
            "event_id": f"unrelated-{index}",
            "seq": index + 1,
            "payload": {"schema_version": "unrelated.v1", "message": "ordinary fact"},
            "metadata": {},
        }
        for index in range(4097)
    )

    monkeypatch.setattr(
        run_ledger_service,
        "query_fact_events",
        lambda _query: SimpleNamespace(events=oversized_page, next_offset=0),
    )
    monkeypatch.setattr(
        run_ledger_service,
        "append_fact_event",
        lambda _command: (_ for _ in ()).throw(AssertionError("record overflow must not append Fact")),
    )

    with pytest.raises(ValueError, match="partial_tail_fact_record_limit_exceeded"):
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=run_id, event=event))

    assert _control_plane_facts(tmp_path, run_id=run_id) == facts_before
    assert ledger.path.read_bytes() == projection_before
    _assert_projection_flock_available(ledger)


def test_public_append_fact_callback_runs_under_projection_lock_and_publish_runs_after_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = RunLedger(tmp_path, run_id="run-lock-order")
    operations: list[str] = []

    def assert_projection_locked() -> SimpleNamespace:
        operations.append("fact")
        assert ledger.path.is_file()
        with ledger.path.open("a+", encoding="utf-8") as probe, pytest.raises(BlockingIOError):
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return SimpleNamespace(
            event_id="fact-lock-order",
            stream="execution.control_plane",
            storage_path="runtime/events/execution.control_plane.jsonl",
            appended_at="2026-07-19T00:00:00+00:00",
            appended_seq=1,
        )

    def assert_projection_unlocked(**_kwargs: Any) -> bool:
        operations.append("publish")
        with ledger.path.open("a+", encoding="utf-8") as probe:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        return True

    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(run_ledger_service, "append_fact_event", lambda _command: assert_projection_locked())
    monkeypatch.setattr(run_ledger_service, "_publish_run_ledger_projection_update", assert_projection_unlocked)

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-lock-order",
            event={"event_type": "gate_evaluated"},
        )
    )

    assert operations == ["fact", "publish"]


def test_run_ledger_append_once_rejects_duplicate_event_id_without_mutation_and_releases_lock(
    tmp_path: Path,
) -> None:
    ledger = RunLedger(tmp_path, run_id="run-duplicate-event-id")
    first_event = {"event_id": "duplicate-event", "event_type": "gate_evaluated", "stage": "qa"}
    second_event = {
        "event_id": "duplicate-event",
        "event_type": "gate_evaluated",
        "stage": "director",
    }
    first_row, first_payload = _canonical_projection_row(ledger, first_event)
    second_row, _payload = _canonical_projection_row(ledger, second_event)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(first_row + second_row, encoding="utf-8")
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt:duplicate_event_id"):
        ledger.append_event_once(first_event, recorded_at="2099-01-01T00:00:00+00:00")
    assert ledger.path.read_bytes() == before

    ledger.path.write_text(first_row, encoding="utf-8")
    replay = ledger.append_event_once(first_event, recorded_at="2099-01-01T00:00:00+00:00")
    assert replay["event"] == first_payload


def test_run_ledger_append_once_rejects_duplicate_append_id_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_hash = run_ledger_module._canonical_stable_hash

    def force_append_identity_collision(value: Any) -> str:
        if isinstance(value, dict) and set(value) == {"run_id", "event_id", "content_id"}:
            return "c" * 64
        return original_hash(value)

    monkeypatch.setattr(run_ledger_module, "_canonical_stable_hash", force_append_identity_collision)
    ledger = RunLedger(tmp_path, run_id="run-duplicate-append-id")
    first_event = {"event_id": "event-a", "event_type": "gate_evaluated", "stage": "qa"}
    second_event = {"event_id": "event-b", "event_type": "gate_evaluated", "stage": "director"}
    first_row, _payload = _canonical_projection_row(ledger, first_event)
    second_row, _payload = _canonical_projection_row(ledger, second_event)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(first_row + second_row, encoding="utf-8")
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="run_ledger_projection_corrupt:duplicate_append_id"):
        ledger.append_event_once(first_event, recorded_at="2099-01-01T00:00:00+00:00")

    assert ledger.path.read_bytes() == before


def test_run_ledger_legacy_append_event_preserves_repeat_append_compatibility(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="run-legacy-repeat")
    event = {"event_type": "gate_evaluated", "stage": "qa"}

    first = ledger.append_event({**event, "recorded_at": "2026-07-19T00:00:00+00:00"})
    second = ledger.append_event({**event, "recorded_at": "2026-07-19T00:00:01+00:00"})

    assert len(ledger.read_events()) == 2
    assert first["event"]["content_id"] == second["event"]["content_id"]
    assert first["event"]["event_id"] == second["event"]["event_id"]
    assert first["event"]["append_id"] != second["event"]["append_id"]


def test_prepare_idempotent_event_binds_run_event_and_content_identity(tmp_path: Path) -> None:
    event = {
        "event_id": "caller-event-a",
        "event_type": "gate_evaluated",
        "stage": "qa",
        "recorded_at": "1900-01-01T00:00:00+00:00",
    }
    run_a = RunLedger(tmp_path, run_id="run-a")
    run_b = RunLedger(tmp_path, run_id="run-b")

    prepared = run_a.prepare_idempotent_event(event)
    replay = run_a.prepare_idempotent_event(event)
    other_run = run_b.prepare_idempotent_event(event)
    distinct_event = run_a.prepare_idempotent_event({**event, "event_id": "caller-event-b"})

    assert replay == prepared
    assert "recorded_at" not in prepared
    assert other_run["content_id"] == prepared["content_id"]
    assert other_run["event_id"] == prepared["event_id"]
    assert other_run["append_id"] != prepared["append_id"]
    assert run_b.fact_idempotency_key(other_run) != run_a.fact_idempotency_key(prepared)
    assert distinct_event["content_id"] == prepared["content_id"]
    assert distinct_event["event_id"] != prepared["event_id"]
    assert distinct_event["append_id"] != prepared["append_id"]
    assert run_a.fact_idempotency_key(distinct_event) != run_a.fact_idempotency_key(prepared)


def test_append_run_ledger_event_rejects_ambiguous_run_id_before_fact_or_projection_write(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="-run")

    with pytest.raises(ValueError, match="invalid_canonical_run_id"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="-run",
                event={"event_type": "gate_evaluated"},
            )
        )

    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=str(tmp_path),
            stream="execution.control_plane",
            strict_integrity=True,
        )
    ).events
    assert facts == ()
    assert not ledger.path.exists()


def test_append_run_ledger_event_accepts_exact_safe_run_id(tmp_path: Path) -> None:
    result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run",
            event={"event_type": "gate_evaluated"},
        )
    )

    assert result.receipt["event"]["event_id"]
    assert len(_control_plane_facts(tmp_path, run_id="run")) == 1
    assert len(RunLedger(tmp_path, run_id="run").read_events()) == 1


@pytest.mark.parametrize("run_id", ("-run", "a/b"))
def test_direct_canonical_paths_reject_non_bijective_run_id(tmp_path: Path, run_id: str) -> None:
    ledger = RunLedger(tmp_path, run_id=run_id)
    event = {"event_type": "gate_evaluated"}

    with pytest.raises(ValueError, match="invalid_canonical_run_id"):
        ledger.prepare_idempotent_event(event)
    with pytest.raises(ValueError, match="invalid_canonical_run_id"):
        ledger.fact_idempotency_key(event)
    with pytest.raises(ValueError, match="invalid_canonical_run_id"):
        ledger.append_event_once(event, recorded_at="2026-07-19T00:00:00+00:00")

    assert not ledger.path.exists()


def test_legacy_append_event_keeps_ambiguous_run_id_compatibility(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path, run_id="-run")

    receipt = ledger.append_event(
        {
            "event_type": "legacy.recorded",
            "recorded_at": "2026-07-19T00:00:00+00:00",
        }
    )

    assert receipt["ledger_path"].endswith("/run.ndjson")
    assert ledger.read_events() == [receipt["event"]]


@pytest.mark.parametrize(
    "run_id",
    (
        "a" * 248,
        "界" * 82 + "aa",
    ),
    ids=("ascii_255_byte_basename", "unicode_under_255_byte_basename"),
)
def test_canonical_run_id_accepts_utf8_basename_at_or_under_255_bytes(tmp_path: Path, run_id: str) -> None:
    assert len(f"{run_id}.ndjson".encode()) <= 255
    ledger = RunLedger(tmp_path, run_id=run_id)

    prepared = ledger.prepare_idempotent_event({"event_type": "gate_evaluated"})
    receipt = ledger.append_event_once(prepared, recorded_at="2026-07-19T00:00:00+00:00")

    assert ledger.path.is_file()
    assert receipt["event"]["event_id"] == prepared["event_id"]


def test_public_append_rejects_oversized_ascii_run_id_before_fact_or_projection_write(tmp_path: Path) -> None:
    run_id = "a" * 249
    assert len(f"{run_id}.ndjson".encode()) == 256
    ledger = RunLedger(tmp_path, run_id=run_id)

    with pytest.raises(ValueError, match="invalid_canonical_run_id"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=run_id,
                event={"event_type": "gate_evaluated"},
            )
        )

    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=str(tmp_path),
            stream="execution.control_plane",
            strict_integrity=True,
        )
    ).events
    assert facts == ()
    assert not ledger.path.parent.exists()


@pytest.mark.parametrize(
    "run_id",
    (
        "a" * 249,
        "界" * 83,
    ),
    ids=("ascii_256_byte_basename", "unicode_256_byte_basename"),
)
def test_direct_canonical_paths_reject_run_id_basename_over_255_utf8_bytes(tmp_path: Path, run_id: str) -> None:
    assert len(f"{run_id}.ndjson".encode()) > 255
    ledger = RunLedger(tmp_path, run_id=run_id)
    event = {"event_type": "gate_evaluated"}

    with pytest.raises(ValueError, match="invalid_canonical_run_id"):
        ledger.prepare_idempotent_event(event)
    with pytest.raises(ValueError, match="invalid_canonical_run_id"):
        ledger.fact_idempotency_key(event)
    with pytest.raises(ValueError, match="invalid_canonical_run_id"):
        ledger.append_event_once(event, recorded_at="2026-07-19T00:00:00+00:00")

    assert not ledger.path.parent.exists()


def test_append_run_ledger_event_retries_after_projection_failure_without_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_append_serialized_row = RunLedger._append_serialized_row_locked
    append_attempts = 0

    def fail_projection_once(self: RunLedger, handle: Any, serialized_row: str) -> None:
        nonlocal append_attempts
        append_attempts += 1
        if append_attempts == 1:
            raise OSError("projection fsync failed")
        original_append_serialized_row(self, handle, serialized_row)

    monkeypatch.setattr(RunLedger, "_append_serialized_row_locked", fail_projection_once)
    command = AppendRunLedgerEventCommandV1(
        workspace=str(tmp_path),
        run_id="run-projection-retry",
        event={"event_type": "gate_evaluated", "stage": "qa"},
    )

    with pytest.raises(OSError, match="projection fsync failed"):
        append_run_ledger_event(command)
    fact_after_failure = _control_plane_facts(tmp_path, run_id="run-projection-retry")
    assert len(fact_after_failure) == 1
    assert RunLedger(tmp_path, run_id="run-projection-retry").read_events() == []

    replay = append_run_ledger_event(command).receipt
    fact_after_replay = _control_plane_facts(tmp_path, run_id="run-projection-retry")
    rows = RunLedger(tmp_path, run_id="run-projection-retry").read_events()

    assert fact_after_replay == fact_after_failure
    assert rows == [replay["event"]]
    assert replay["fact_receipt"]["event_id"] == fact_after_failure[0]["event_id"]
    assert replay["fact_receipt"]["appended_seq"] == fact_after_failure[0]["seq"]
    assert replay["event"]["recorded_at"] == fact_after_failure[0]["occurred_at"]
    assert "recorded_at" not in fact_after_failure[0]["payload"]["event"]
    rebuilt = run_ledger_service._read_execution_control_plane_facts(
        workspace=tmp_path,
        run_id="run-projection-retry",
    )
    assert rebuilt[0]["recorded_at"] == fact_after_failure[0]["occurred_at"]


def test_append_run_ledger_event_ack_loss_retry_reuses_fact_and_projection_identity(tmp_path: Path) -> None:
    command = AppendRunLedgerEventCommandV1(
        workspace=str(tmp_path),
        run_id="run-ack-loss",
        event={"event_type": "gate_evaluated", "stage": "qa"},
    )

    first = append_run_ledger_event(command).receipt
    first_facts = _control_plane_facts(tmp_path, run_id="run-ack-loss")
    first_rows = RunLedger(tmp_path, run_id="run-ack-loss").read_events()
    replay = append_run_ledger_event(command).receipt

    assert _control_plane_facts(tmp_path, run_id="run-ack-loss") == first_facts
    assert RunLedger(tmp_path, run_id="run-ack-loss").read_events() == first_rows
    assert replay == first
    assert len(first_facts) == 1
    assert len(first_rows) == 1


def test_append_run_ledger_event_concurrent_identical_retries_append_once(tmp_path: Path) -> None:
    workers = 8
    start = Barrier(workers)
    command = AppendRunLedgerEventCommandV1(
        workspace=str(tmp_path),
        run_id="run-concurrent-retry",
        event={"event_type": "gate_evaluated", "stage": "qa"},
    )

    def invoke(_index: int) -> dict[str, Any]:
        start.wait()
        return append_run_ledger_event(command).receipt

    with ThreadPoolExecutor(max_workers=workers) as executor:
        receipts = list(executor.map(invoke, range(workers)))

    facts = _control_plane_facts(tmp_path, run_id="run-concurrent-retry")
    rows = RunLedger(tmp_path, run_id="run-concurrent-retry").read_events()
    assert len(facts) == 1
    assert len(rows) == 1
    assert all(receipt == receipts[0] for receipt in receipts)
    assert receipts[0]["event"] == rows[0]
    assert receipts[0]["fact_receipt"]["event_id"] == facts[0]["event_id"]
    assert receipts[0]["fact_receipt"]["appended_seq"] == facts[0]["seq"]


def test_append_run_ledger_event_distinct_explicit_event_ids_remain_distinct(tmp_path: Path) -> None:
    receipts = [
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-distinct-events",
                event={
                    "event_id": event_id,
                    "event_type": "gate_evaluated",
                    "stage": "qa",
                },
            )
        ).receipt
        for event_id in ("caller-event-a", "caller-event-b")
    ]

    facts = _control_plane_facts(tmp_path, run_id="run-distinct-events")
    rows = RunLedger(tmp_path, run_id="run-distinct-events").read_events()
    assert len(facts) == 2
    assert len(rows) == 2
    assert receipts[0]["event"]["content_id"] == receipts[1]["event"]["content_id"]
    assert receipts[0]["event"]["event_id"] != receipts[1]["event"]["event_id"]
    assert receipts[0]["event"]["append_id"] != receipts[1]["event"]["append_id"]


def test_append_run_ledger_event_same_explicit_event_id_changed_semantics_fails_closed(tmp_path: Path) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-event-conflict",
            event={
                "event_id": "caller-event-fixed",
                "event_type": "gate_evaluated",
                "stage": "qa",
            },
        )
    )

    with pytest.raises(ValueError, match="event_id already exists with different semantic content"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-event-conflict",
                event={
                    "event_id": "caller-event-fixed",
                    "event_type": "gate_evaluated",
                    "stage": "director",
                },
            )
        )

    assert len(_control_plane_facts(tmp_path, run_id="run-event-conflict")) == 1
    assert len(RunLedger(tmp_path, run_id="run-event-conflict").read_events()) == 1


def test_append_tool_call_lifecycle_event_public_service_projects_event(tmp_path: Path) -> None:
    result = append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            task_id="TASK-1",
            turn_id="turn-1",
            role="director",
            lifecycle_receipt={
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "provider_response_hash": "hash-1",
                "native_tool_calls_count": 1,
                "decoded_tool_calls_count": 1,
                "dispatched_tool_calls_count": 0,
                "dropped_tool_calls": [{"tool_name": "write_file", "reason": "tool_dispatch_dropped"}],
                "dispatch_status": "dropped",
                "failure_class": "TOOL_DISPATCH_DROPPED",
            },
            stage="director_tool_dispatch",
            project_id="TASK-1",
        )
    )

    ledger_path = Path(str(result.receipt["ledger_path"]))
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection

    assert ledger_path.parent == tmp_path / "runtime" / "control_plane" / "ledger"
    assert result.receipt["event"]["event_type"] == "tool_call_lifecycle"
    assert result.receipt["event"]["tool_call_lifecycle_receipt"]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert projection["ok"] is False
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["tool_lifecycle"]["failure_evidence"][0]["failure_class"] == "TOOL_DISPATCH_DROPPED"


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

    projection = build_run_ledger_projection([base_event, _successful_tool_lifecycle_event()])
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
            },
            _successful_tool_lifecycle_event(),
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
    assert (
        projection["tool_lifecycle"]["events"][0]["failure_evidence"]
        == projection["tool_lifecycle"]["failure_evidence"][0]
    )
    assert summary["detail"] == "run ledger projection tool lifecycle failed: TOOL_DISPATCH_DROPPED"
    assert summary["missing"] == []
    assert summary["failed_control_plane_events"] == ["TOOL_DISPATCH_DROPPED"]
    assert summary["failure_evidence"][0]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert summary["failure_evidence"][0]["metadata"]["source"] == "tool_call_lifecycle_receipt.v1"


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
            },
            _successful_tool_lifecycle_event(),
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
            _successful_tool_lifecycle_event(),
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
    assert projection["task_boundary"]["historical_failed_count"] == 1
    assert projection["task_boundary"]["latest_by_task"]["TASK-1"]["failure_class"] == ("DEFERRED_FOLLOWUP_REQUIRED")
    assert projection["projects"][0]["tool_lifecycle"]["dropped_count"] == 1
    assert projection["projects"][0]["task_boundary"]["latest"]["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"


def test_read_run_ledger_projection_aggregates_factory_children_without_workspace_leakage(
    tmp_path: Path,
) -> None:
    factory_run_id = "factory-r26"
    project_id = "L1-01"
    parent_run_id = "ce65-parent-gate"
    child_run_ids = ("director-1", "director-2", "director-3", "director-4")

    for index, child_run_id in enumerate(child_run_ids, start=1):
        _append_task_runtime_execution_fact(
            tmp_path,
            run_id=child_run_id,
            factory_run_id=factory_run_id,
            project_id=project_id,
            task_id=f"TASK-{index}",
        )
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id=child_run_ids[0],
        factory_run_id=factory_run_id,
        project_id=project_id,
        task_id="TASK-1-RETRY",
    )
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="other-factory-director",
        factory_run_id="factory-r27",
        project_id=project_id,
        task_id="TASK-OTHER-FACTORY",
    )
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="other-project-director",
        factory_run_id=factory_run_id,
        project_id="L1-02",
        task_id="TASK-OTHER-PROJECT",
    )

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=parent_run_id,
            event={
                "event_type": "gate_evaluated",
                "stage": "real_run",
                "gate": {"name": "real_run_gate", "ok": True, "summary": "parent gate passed"},
                "job_token": {
                    "token_id": "parent-token",
                    "run_id": parent_run_id,
                    "project_id": project_id,
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=child_run_ids[0],
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
            run_id=child_run_ids[0],
            event={
                "event_type": "tool_call_lifecycle",
                "run_id": child_run_ids[0],
                "task_id": "TASK-1",
                "job_token": {"project_id": project_id, "capability_audit": {"ok": True, "issues": []}},
                "tool_call_lifecycle_receipt": lifecycle,
            },
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=child_run_ids[1],
            event={
                "event_type": "task_boundary_verdict",
                "run_id": child_run_ids[1],
                "task_id": "TASK-2",
                "job_token": {"project_id": project_id, "capability_audit": {"ok": True, "issues": []}},
                "task_boundary_verdict": {
                    "schema_version": "polaris.task_boundary_verdict.v1",
                    "task_id": "TASK-2",
                    "status": "deferred_followup_required",
                    "ok": False,
                    "failure_class": "DEFERRED_FOLLOWUP_REQUIRED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "needs follow-up",
                },
            },
        )
    )
    for child_run_id in child_run_ids[2:]:
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=child_run_id,
                event={
                    "event_type": "gate_evaluated",
                    "stage": "director",
                    "gate": {"name": child_run_id, "ok": True, "summary": "director completed"},
                    "job_token": {
                        "token_id": f"{child_run_id}-token",
                        "run_id": child_run_id,
                        "project_id": project_id,
                        "capability_audit": {"ok": True, "issues": []},
                        "gate_policy": {},
                    },
                    "physical_evidence": {},
                },
            )
        )
    for unrelated_run_id in ("other-factory-director", "other-project-director"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=unrelated_run_id,
                event={
                    "event_type": "gate_evaluated",
                    "stage": "director",
                    "gate": {"name": f"{unrelated_run_id}-gate", "ok": True, "summary": "must exclude"},
                    "job_token": {
                        "token_id": f"{unrelated_run_id}-token",
                        "run_id": unrelated_run_id,
                        "project_id": project_id,
                        "capability_audit": {"ok": True, "issues": []},
                        "gate_policy": {},
                    },
                    "physical_evidence": {},
                },
            )
        )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id=parent_run_id,
            factory_run_id=factory_run_id,
            project_id=project_id,
        )
    ).projection

    assert projection["query_scope"] == {
        "run_id": parent_run_id,
        "factory_run_id": factory_run_id,
        "project_id": project_id,
    }
    assert projection["consumed_run_ids"] == [parent_run_id, *child_run_ids]
    assert len(projection["projects"]) == 1
    assert projection["projects"][0]["project_id"] == project_id
    assert projection["run_projection"]["gate_count"] == 3
    assert [gate["name"] for gate in projection["run_projection"]["gates"]] == [
        "real_run_gate",
        "director-3",
        "director-4",
    ]
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["task_boundary"]["latest"]["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"
    rendered = json.dumps(projection, sort_keys=True)
    assert "other-factory-director-gate" not in rendered
    assert "other-project-director-gate" not in rendered


def test_read_run_ledger_projection_scoped_legacy_fallback_uses_selected_run_ids(tmp_path: Path) -> None:
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="director-legacy",
        factory_run_id="factory-r26",
        project_id="L1-01",
        task_id="TASK-LEGACY",
    )
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="director-legacy-unrelated",
        factory_run_id="factory-r27",
        project_id="L1-01",
        task_id="TASK-UNRELATED",
    )
    for run_id, gate_name in (
        ("director-legacy", "legacy-director-gate"),
        ("director-legacy-unrelated", "unrelated-legacy-gate"),
    ):
        RunLedger(tmp_path, run_id=run_id).append_event(
            {
                "event_type": "gate_evaluated",
                "gate": {"name": gate_name, "ok": True, "summary": "legacy event"},
                "job_token": {
                    "token_id": f"{run_id}-token",
                    "run_id": run_id,
                    "project_id": "L1-01",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            }
        )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            factory_run_id="factory-r26",
            project_id="L1-01",
        )
    ).projection

    assert projection["consumed_run_ids"] == ["director-legacy"]
    assert [gate["name"] for gate in projection["run_projection"]["gates"]] == ["legacy-director-gate"]


def test_read_run_ledger_projection_scope_miss_does_not_widen_to_workspace(tmp_path: Path) -> None:
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="other-factory-director",
        factory_run_id="factory-r27",
        project_id="L1-01",
        task_id="TASK-OTHER",
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="other-factory-director",
            event={
                "event_type": "gate_evaluated",
                "gate": {"name": "other-factory-gate", "ok": True, "summary": "must not leak"},
                "job_token": {
                    "token_id": "other-factory-token",
                    "run_id": "other-factory-director",
                    "project_id": "L1-01",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            factory_run_id="factory-r26",
            project_id="L1-01",
        )
    ).projection

    assert projection["available"] is False
    assert projection["consumed_run_ids"] == []
    assert projection["query_scope"] == {
        "run_id": "",
        "factory_run_id": "factory-r26",
        "project_id": "L1-01",
    }


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
    lifecycle_append_id = _append_successful_tool_lifecycle_event(tmp_path, run_id="run-barrier")
    event = result.receipt["event"]

    barrier_result = read_run_ledger_projection_barrier(
        ReadRunLedgerProjectionBarrierQueryV1(
            workspace=str(tmp_path),
            run_id="run-barrier",
            min_append_id=str(event["append_id"]),
        )
    )

    assert barrier_result.barrier["barrier_satisfied"] is True
    assert barrier_result.barrier["consumed_until_append_id"] == lifecycle_append_id
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


def test_append_run_ledger_event_publish_failure_is_visible_and_retry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_results = iter((False, True))
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(
        run_ledger_service,
        "_publish_run_ledger_projection_update",
        lambda **_kwargs: next(publish_results),
    )
    command = AppendRunLedgerEventCommandV1(
        workspace=str(tmp_path),
        run_id="run-publish-retry",
        event={"event_type": "gate_evaluated", "gate": {"name": "qa", "ok": True}},
    )

    with pytest.raises(RuntimeError, match="projection publish failed"):
        append_run_ledger_event(command)

    facts_after_failure = _control_plane_facts(tmp_path, run_id="run-publish-retry")
    rows_after_failure = RunLedger(tmp_path, run_id="run-publish-retry").read_events()
    assert len(facts_after_failure) == 1
    assert len(rows_after_failure) == 1

    replay = append_run_ledger_event(command)

    assert _control_plane_facts(tmp_path, run_id="run-publish-retry") == facts_after_failure
    assert RunLedger(tmp_path, run_id="run-publish-retry").read_events() == rows_after_failure
    assert replay.receipt["event"] == rows_after_failure[0]


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
                            "effect_receipt": _authoritative_directed_effect_receipt(run_id="run-1"),
                            "effect_receipt_commit": _authoritative_directed_effect_receipt_commit(run_id="run-1"),
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
    _append_successful_tool_lifecycle_event(tmp_path, run_id="run-1")

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



def test_summarize_run_ledger_projection_tool_result_failed_is_recoverable_not_integrity() -> None:
    """M08 (caller side): summarize_run_ledger_projection must respect failure_status.failed.
    A recoverable TOOL_RESULT_FAILED (tool ran, ok=False) is product-quality, not integrity.
    L1-01 r27 still died on TOOL_RESULT_FAILED because this caller checked the summary's
    raw ok flag instead of the M08 failed verdict.
    """
    from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import build_tool_call_lifecycle_receipt, project_tool_lifecycle_event
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1", task_id="TASK-1", turn_id="turn-1", role="director",
        native_tool_calls_count=1, decoded_tool_calls_count=1, dispatched_tool_calls_count=1,
        receipts=[{"batch_id": "batch-1", "failure_count": 1, "results": [{"tool_name": "write_file", "status": "failed", "reason": "deo_director_policy_denied"}]}],
        reason="deo_director_policy_denied",
    ).to_dict()
    projection = build_run_ledger_projection(
        [
            {"event_type": "gate_evaluated", "stage": "director", "gate": {"name": "director", "ok": True, "summary": "started"},
             "job_token": {"token_id": "token-1", "project_id": "P1", "capability_audit": {"ok": True, "issues": []}, "gate_policy": {}}, "physical_evidence": {}},
            {"event_type": "tool_call_lifecycle", "tool_call_lifecycle_receipt": receipt},
        ]
    )
    summary = summarize_run_ledger_projection(projection)
    assert projection["tool_lifecycle"]["ok"] is False
    assert summary["ok"] is True  # recoverable per M08, not integrity break
    assert summary["failed_control_plane_events"] == []
