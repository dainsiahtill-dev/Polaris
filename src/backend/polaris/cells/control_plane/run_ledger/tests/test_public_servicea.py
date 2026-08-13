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


def test_task_execution_capability_ignores_later_qa_token() -> None:
    director_token = {
        "token_id": "director-task-2-token",
        "stage": "pending_exec",
        "contract_hash": "c" * 64,
        "blueprint_hash": "b" * 64,
        "project_id": "P1",
        "capability_audit": {"ok": True, "issues": []},
    }
    qa_token = {
        "token_id": "qa-task-2-token",
        "task_id": "TASK-2",
        "stage": "quality_gate",
        "contract_hash": "d" * 64,
        "blueprint_hash": "e" * 64,
        "project_id": "P1",
        "capability_audit": {"ok": True, "issues": []},
    }
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "real_run_gate",
                "gate": {"name": "real_run_gate", "ok": True, "summary": "baseline passed"},
                "job_token": {
                    "token_id": "prerequisite-token",
                    "stage": "real_run_gate",
                    "contract_hash": "a" * 64,
                    "blueprint_hash": "a" * 64,
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                },
                "physical_evidence": {},
            },
            {
                "event_type": "gate_evaluated",
                "stage": "pending_exec",
                "gate": {"name": "tool_receipt", "ok": True, "summary": "Director tools settled"},
                "job_token": director_token,
                "physical_evidence": {"metadata": {"task_id": "TASK-2"}},
            },
            _successful_tool_lifecycle_event(task_id="TASK-2"),
            {
                "event_type": "gate_evaluated",
                "stage": "quality_gate",
                "gate": {"name": "qa_role_evidence", "ok": True, "summary": "QA passed"},
                "job_token": qa_token,
                "physical_evidence": {},
            },
        ]
    )

    assert projection["capability"]["latest_token_id"] == "qa-task-2-token"
    assert projection["execution_capability_by_task"] == {
        "TASK-2": {
            "ok": True,
            "issues": [],
            "latest_token_id": "director-task-2-token",
            "latest_contract_hash": "c" * 64,
            "latest_blueprint_hash": "b" * 64,
            "job_token_ids": ["prerequisite-token", "director-task-2-token"],
            "stage": "pending_exec",
            "task_id": "TASK-2",
        }
    }


def test_qa_verdict_effective_only_for_latest_task_boundary_epoch() -> None:
    def boundary(run_id: str) -> dict[str, Any]:
        return {
            "event_type": "task_boundary_verdict",
            "task_id": "TASK-2",
            "run_id": run_id,
            "task_boundary_verdict": {
                "schema_version": "polaris.task_boundary_verdict.v1",
                "task_id": "TASK-2",
                "run_id": run_id,
                "status": "completed_verified",
                "ok": True,
                "failure_class": "PASSED",
                "responsible_layer": "execution_control_plane",
            },
        }

    def qa_verdict(run_id: str, *, ok: bool) -> dict[str, Any]:
        return {
            "event_type": "gate_evaluated",
            "stage": "qa",
            "task_id": "TASK-2",
            "run_id": run_id,
            "gate": {
                "name": "qa_verdict",
                "ok": ok,
                "summary": "Canonical QA verdict",
            },
            "job_token": {
                "token_id": f"qa-{run_id}",
                "run_id": run_id,
                "project_id": "P1",
                "capability_audit": {"ok": True, "issues": []},
                "gate_policy": {},
            },
            "physical_evidence": {"task_id": "TASK-2", "run_id": run_id},
        }

    events = [
        boundary("director-old"),
        qa_verdict("director-old", ok=False),
        boundary("director-new"),
    ]
    before_fresh_qa = build_run_ledger_projection(events)

    assert before_fresh_qa["gates"][0]["effective"] is False
    assert before_fresh_qa["gates"][0]["task_id"] == "TASK-2"
    assert before_fresh_qa["gates"][0]["run_id"] == "director-old"
    assert before_fresh_qa["effective_gates"] == []
    assert before_fresh_qa["historical_failed_gate_count"] == 1

    after_fresh_qa = build_run_ledger_projection([*events, qa_verdict("director-new", ok=True)])

    assert len(after_fresh_qa["gates"]) == 2
    assert after_fresh_qa["historical_failed_gate_count"] == 1
    assert [gate["run_id"] for gate in after_fresh_qa["effective_gates"]] == ["director-new"]
    assert after_fresh_qa["failed_gates"] == []


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


def test_repaired_gate_revision_supersedes_historical_failure_for_current_outcome() -> None:
    failed = {
        "event_type": "gate_evaluated",
        "stage": "workspace_validation",
        "gate_obligation_id": "factory-1:workspace-validation",
        "gate_subject_kind": "factory_run",
        "gate_subject_id": "factory-1",
        "gate_revision": 1,
        "content_id": "a" * 64,
        "gate": {"name": "workspace_validation", "ok": False, "summary": "npm test failed"},
        "job_token": {
            "token_id": "token-1",
            "run_id": "factory-1",
            "factory_run_id": "factory-1",
            "project_id": "P1",
            "target_files": ["tests/verify.test.ts", "src/verify.ts"],
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {"required_evidence_modalities": ["command"]},
        },
        "physical_evidence": {"modalities": {"command": {"present": True, "ok": False, "detail": "npm test failed"}}},
    }
    repaired = {
        **failed,
        "gate_revision": 2,
        "supersedes_content_id": "a" * 64,
        "content_id": "b" * 64,
        "gate": {"name": "workspace_validation", "ok": True, "summary": "npm test passed after repair"},
        "physical_evidence": {"modalities": {"command": {"present": True, "ok": True, "detail": "npm test passed"}}},
    }

    projection = build_run_ledger_projection([failed, repaired, _successful_tool_lifecycle_event()])

    assert projection["gate_count"] == 2
    assert projection["effective_gate_count"] == 1
    assert projection["historical_failed_gate_count"] == 1
    assert projection["gates"][0]["effective"] is False
    assert projection["gates"][1]["effective"] is True
    assert projection["failed_gates"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == []
    assert projection["outcome_ok"] is True
    assert projection["ok"] is True


def test_gate_revision_does_not_supersede_a_different_target_scope() -> None:
    base_token = {
        "token_id": "token-1",
        "run_id": "factory-1",
        "factory_run_id": "factory-1",
        "project_id": "P1",
        "capability_audit": {"ok": True, "issues": []},
        "gate_policy": {},
    }
    first = {
        "event_type": "gate_evaluated",
        "stage": "director",
        "gate": {"name": "task_delivery", "ok": False, "summary": "TASK-1 failed"},
        "job_token": {**base_token, "target_files": ["src/a.ts"]},
        "physical_evidence": {},
    }
    second = {
        **first,
        "gate": {"name": "task_delivery", "ok": True, "summary": "TASK-2 passed"},
        "job_token": {**base_token, "target_files": ["src/b.ts"]},
    }

    projection = build_run_ledger_projection([first, second, _successful_tool_lifecycle_event()])

    assert projection["effective_gate_count"] == 2
    assert [gate["summary"] for gate in projection["failed_gates"]] == ["TASK-1 failed"]
    assert projection["outcome_ok"] is False


def test_gate_revision_does_not_supersede_a_different_task_owner_with_same_scope() -> None:
    base = {
        "event_type": "gate_evaluated",
        "stage": "director",
        "gate_obligation_id": "TASK-A:delivery",
        "gate_subject_kind": "director_task",
        "gate_subject_id": "TASK-A",
        "gate_revision": 1,
        "content_id": "a" * 64,
        "gate": {"name": "task_delivery", "ok": False, "summary": "TASK-A failed"},
        "job_token": {
            "token_id": "token-1",
            "run_id": "factory-1",
            "factory_run_id": "factory-1",
            "project_id": "P1",
            "target_files": ["src/shared.ts"],
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {},
        },
        "physical_evidence": {},
    }
    sibling = {
        **base,
        "task_id": "TASK-B",
        "gate_obligation_id": "TASK-B:delivery",
        "gate_subject_id": "TASK-B",
        "content_id": "b" * 64,
        "gate": {"name": "task_delivery", "ok": True, "summary": "TASK-B passed"},
    }
    base["task_id"] = "TASK-A"

    projection = build_run_ledger_projection([base, sibling, _successful_tool_lifecycle_event()])

    assert projection["effective_gate_count"] == 2
    assert [gate["summary"] for gate in projection["failed_gates"]] == ["TASK-A failed"]
    assert projection["outcome_ok"] is False


def test_latest_failed_gate_revision_supersedes_historical_success() -> None:
    passed = {
        "event_type": "gate_evaluated",
        "stage": "workspace_validation",
        "gate_obligation_id": "factory-1:workspace-validation",
        "gate_subject_kind": "factory_run",
        "gate_subject_id": "factory-1",
        "gate_revision": 1,
        "content_id": "a" * 64,
        "gate": {"name": "workspace_validation", "ok": True, "summary": "npm test passed"},
        "job_token": {
            "token_id": "token-1",
            "run_id": "factory-1",
            "factory_run_id": "factory-1",
            "project_id": "P1",
            "target_files": ["tests/verify.test.ts", "src/verify.ts"],
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {"required_evidence_modalities": ["command"]},
        },
        "physical_evidence": {"modalities": {"command": {"present": True, "ok": True, "detail": "npm test passed"}}},
    }
    regressed = {
        **passed,
        "gate_revision": 2,
        "supersedes_content_id": "a" * 64,
        "content_id": "b" * 64,
        "gate": {"name": "workspace_validation", "ok": False, "summary": "npm test regressed"},
        "physical_evidence": {
            "modalities": {"command": {"present": True, "ok": False, "detail": "npm test regressed"}}
        },
    }

    projection = build_run_ledger_projection([passed, regressed, _successful_tool_lifecycle_event()])

    assert projection["effective_gate_count"] == 1
    assert [gate["summary"] for gate in projection["failed_gates"]] == ["npm test regressed"]
    assert projection["evidence_policy"]["failed_required_modalities"] == ["command"]
    assert projection["outcome_ok"] is False
    assert projection["ok"] is False


def test_repaired_gate_revision_cannot_shrink_required_evidence_contract() -> None:
    failed = {
        "event_type": "gate_evaluated",
        "stage": "workspace_validation",
        "gate_obligation_id": "factory-1:workspace-validation",
        "gate_subject_kind": "factory_run",
        "gate_subject_id": "factory-1",
        "gate_revision": 1,
        "content_id": "a" * 64,
        "gate": {"name": "workspace_validation", "ok": False, "summary": "npm test failed"},
        "job_token": {
            "token_id": "token-1",
            "run_id": "factory-1",
            "factory_run_id": "factory-1",
            "project_id": "P1",
            "target_files": ["tests/verify.test.ts", "src/verify.ts"],
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {"required_evidence_modalities": ["command"]},
        },
        "physical_evidence": {"modalities": {"command": {"present": True, "ok": False, "detail": "npm test failed"}}},
    }
    invalid_repair = {
        **failed,
        "gate_revision": 2,
        "supersedes_content_id": "a" * 64,
        "content_id": "b" * 64,
        "gate": {"name": "workspace_validation", "ok": True, "summary": "claimed repaired"},
        "job_token": {
            **failed["job_token"],
            "gate_policy": {"required_evidence_modalities": []},
        },
        "physical_evidence": {},
    }

    projection = build_run_ledger_projection([failed, invalid_repair, _successful_tool_lifecycle_event()])

    assert projection["effective_gate_count"] == 1
    assert projection["effective_gates"][0]["required_evidence_modalities"] == ["command"]
    assert projection["evidence_policy"]["missing_required_modalities"] == ["command"]
    assert projection["integrity_ok"] is False
    assert projection["ok"] is False


def test_gate_revision_fork_fails_projection_integrity() -> None:
    first = {
        "event_type": "gate_evaluated",
        "stage": "workspace_validation",
        "gate_obligation_id": "factory-1:workspace-validation",
        "gate_subject_kind": "factory_run",
        "gate_subject_id": "factory-1",
        "gate_revision": 1,
        "content_id": "a" * 64,
        "gate": {"name": "workspace_validation", "ok": False, "summary": "initial failure"},
        "job_token": {
            "token_id": "token-1",
            "run_id": "factory-1",
            "factory_run_id": "factory-1",
            "project_id": "P1",
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {},
        },
        "physical_evidence": {},
    }
    repaired = {
        **first,
        "gate_revision": 2,
        "supersedes_content_id": "a" * 64,
        "content_id": "b" * 64,
        "gate": {"name": "workspace_validation", "ok": True, "summary": "valid repair"},
    }
    fork = {
        **repaired,
        "content_id": "c" * 64,
        "gate": {"name": "workspace_validation", "ok": True, "summary": "stale fork"},
    }

    projection = build_run_ledger_projection([first, repaired, fork, _successful_tool_lifecycle_event()])

    assert projection["gate_revisions"]["integrity_ok"] is False
    assert projection["gate_revisions"]["issues"] == ["gate_revision_chain_fork_or_stale:2"]
    assert projection["integrity_ok"] is False
    assert projection["ok"] is False


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


