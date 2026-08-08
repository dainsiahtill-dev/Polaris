"""A009B2a1 strict Factory event-chain and admission-genesis tests."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_store as factory_store_module
from polaris.cells.factory.pipeline.internal.factory_event_chain import (
    FACTORY_EVENT_CHAIN_MAX_BYTES,
    FACTORY_EVENT_CHAIN_MAX_RECORDS,
    FACTORY_EVENT_CHAIN_SCHEMA,
    FACTORY_EVENT_CHAIN_ZERO_HASH,
    FactoryEventChainError,
    FactoryRunAdmissionV1,
    build_factory_run_admitted_event,
    build_next_factory_event_record,
    decode_factory_event_chain,
    encode_factory_event_record,
    validate_factory_event_chain,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryRunService
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.kernelone.events.final_request_evidence import canonical_role_final_request_hash
from polaris.kernelone.fs import locked_regular_file as locked_regular_file_module
from polaris.kernelone.fs.locked_regular_file import (
    LockedRegularFileError,
    StreamLeaseV1,
)


def _admission_event(*, run_id: str = "factory-run-1") -> dict[str, Any]:
    admission = FactoryRunAdmissionV1(
        factory_run_id=run_id,
        created_at="2026-07-18T00:00:00+00:00",
        name="strict run",
        description="immutable intent",
    )
    return {
        **build_factory_run_admitted_event(admission),
        "run_id": run_id,
        "event_id": "evt-admission",
        "timestamp": admission.created_at,
    }


def _record(*, event: dict[str, Any], sequence: int, previous_hash: str) -> dict[str, Any]:
    without_hash = {
        **event,
        "chain_schema_version": FACTORY_EVENT_CHAIN_SCHEMA,
        "chain_sequence": sequence,
        "chain_previous_hash": previous_hash,
    }
    return {
        **without_hash,
        "chain_event_hash": canonical_role_final_request_hash(
            {"domain": "polaris.factory.event_chain.v1", "event": without_hash}
        ),
    }


def test_genesis_record_has_exact_frozen_chain_and_admission_hash() -> None:
    event = _admission_event()

    record = build_next_factory_event_record((), run_id="factory-run-1", event=event)

    assert record["chain_schema_version"] == FACTORY_EVENT_CHAIN_SCHEMA
    assert record["chain_sequence"] == 1
    assert record["chain_previous_hash"] == FACTORY_EVENT_CHAIN_ZERO_HASH
    expected_hash = canonical_role_final_request_hash(
        {
            "domain": "polaris.factory.event_chain.v1",
            "event": {key: value for key, value in record.items() if key != "chain_event_hash"},
        }
    )
    assert record["chain_event_hash"] == expected_hash
    assert record["payload"] == {
        "factory_run_id": "factory-run-1",
        "created_at": "2026-07-18T00:00:00+00:00",
        "name": "strict run",
        "description": "immutable intent",
    }
    assert record["canonical_sha256"] == canonical_role_final_request_hash(record["payload"])
    assert "metadata" not in record
    assert "metadata" not in record["payload"]


def test_strict_decoder_rejects_corruption_without_skipping_records() -> None:
    first = build_next_factory_event_record((), run_id="factory-run-1", event=_admission_event())
    second = build_next_factory_event_record(
        (first,),
        run_id="factory-run-1",
        event={
            "type": "stage_started",
            "run_id": "factory-run-1",
            "event_id": "evt-stage",
            "timestamp": "2026-07-18T00:00:01+00:00",
        },
    )
    corrupted = dict(first)
    corrupted["payload"] = {**corrupted["payload"], "name": "replaced"}
    raw = encode_factory_event_record(corrupted) + encode_factory_event_record(second)

    with pytest.raises(FactoryEventChainError) as exc_info:
        decode_factory_event_chain(raw, run_id="factory-run-1")

    assert exc_info.value.code == "factory_event_chain_hash_mismatch"


def test_validator_rejects_wrong_run_duplicate_id_and_sequence_drift() -> None:
    first = build_next_factory_event_record((), run_id="factory-run-1", event=_admission_event())
    duplicate = build_next_factory_event_record(
        (first,),
        run_id="factory-run-1",
        event={
            "type": "duplicate",
            "run_id": "factory-run-1",
            "event_id": "evt-second",
            "timestamp": "2026-07-18T00:00:01+00:00",
        },
    )
    duplicate["event_id"] = first["event_id"]
    duplicate["chain_event_hash"] = canonical_role_final_request_hash(
        {
            "domain": "polaris.factory.event_chain.v1",
            "event": {key: value for key, value in duplicate.items() if key != "chain_event_hash"},
        }
    )
    for records, code in (
        (({**first, "run_id": "other-run"},), "factory_event_chain_run_mismatch"),
        ((first, duplicate), "factory_event_chain_duplicate_event_id"),
        ((first, {**duplicate, "chain_sequence": 3}), "factory_event_chain_sequence_mismatch"),
    ):
        with pytest.raises(FactoryEventChainError) as exc_info:
            validate_factory_event_chain(records, run_id="factory-run-1")
        assert exc_info.value.code == code


def test_validator_rejects_second_admission_with_distinct_event_id() -> None:
    first = build_next_factory_event_record((), run_id="factory-run-1", event=_admission_event())
    second_event = _admission_event()
    second_event["event_id"] = "evt-admission-second"
    second = _record(
        event=second_event,
        sequence=2,
        previous_hash=str(first["chain_event_hash"]),
    )

    with pytest.raises(FactoryEventChainError) as exc_info:
        validate_factory_event_chain((first, second), run_id="factory-run-1")

    assert exc_info.value.code == "factory_event_chain_duplicate_admission"


@pytest.mark.asyncio
async def test_legacy_jsonl_is_readable_but_strict_append_and_read_reject_it(tmp_path: Path) -> None:
    store = FactoryStore(tmp_path / "factory")
    run_id = "legacy-run"
    event_file = store.get_run_dir(run_id) / "events" / "events.jsonl"
    event_file.parent.mkdir(parents=True)
    legacy = {"type": "stage_started", "run_id": run_id, "event_id": "legacy-event"}
    event_file.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    before = event_file.read_bytes()

    assert await store.get_events(run_id) == [legacy]
    with pytest.raises(FactoryEventChainError) as read_error:
        await store.get_authoritative_events(run_id, require_run_snapshot=False)
    assert read_error.value.code == "factory_event_chain_legacy_ineligible"
    with pytest.raises(FactoryEventChainError) as append_error:
        await store.append_authoritative_event(
            run_id,
            {"type": "probe", "run_id": run_id, "event_id": "new", "timestamp": "now"},
        )
    assert append_error.value.code == "factory_event_chain_legacy_ineligible"
    assert event_file.read_bytes() == before


@pytest.mark.asyncio
async def test_legacy_jsonl_with_valid_snapshot_remains_compatibility_only(tmp_path: Path) -> None:
    store = FactoryStore(tmp_path / "factory")
    run_id = "legacy-valid-snapshot-run"
    await store.save_run(
        FactoryRun(
            id=run_id,
            config=FactoryConfig(name="legacy snapshot"),
            status=FactoryRunStatus.PENDING,
            created_at="2026-07-18T00:00:00+00:00",
        )
    )
    legacy = {
        "type": "stage_completed",
        "run_id": run_id,
        "event_id": "legacy-success",
        "result": {"status": "success", "stage": "pm_planning"},
    }
    event_file = store.get_run_dir(run_id) / "events" / "events.jsonl"
    event_file.parent.mkdir(parents=True)
    event_file.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    before = event_file.read_bytes()

    assert store.list_runs() == [run_id]
    assert await store.get_events(run_id) == [legacy]
    with pytest.raises(FactoryEventChainError) as read_error:
        await store.get_authoritative_events(run_id)
    assert read_error.value.code == "factory_event_chain_legacy_ineligible"
    with pytest.raises(FactoryEventChainError) as append_error:
        await store.append_authoritative_event(
            run_id,
            {
                "type": "probe",
                "run_id": run_id,
                "event_id": "new",
                "timestamp": "now",
            },
        )
    assert append_error.value.code == "factory_event_chain_legacy_ineligible"
    assert event_file.read_bytes() == before


def test_two_process_append_cas_and_restart_preserve_one_chain(tmp_path: Path) -> None:
    base_dir = tmp_path / "factory"
    run_id = "concurrent-run"
    store = FactoryStore(base_dir)
    asyncio.run(store.append_authoritative_event(run_id, _admission_event(run_id=run_id)))

    start = tmp_path / "start"
    ready = [tmp_path / f"ready-{index}" for index in range(2)]
    script = """
import asyncio
import json
import sys
import time
from pathlib import Path
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore

base_dir, run_id, event_id, ready_path, start_path = sys.argv[1:]
Path(ready_path).write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 10
while not Path(start_path).exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("start barrier timed out")
    time.sleep(0.01)
record = asyncio.run(
    FactoryStore(Path(base_dir)).append_authoritative_event(
        run_id,
        {
            "type": "concurrent_probe",
            "run_id": run_id,
            "event_id": event_id,
            "timestamp": "2026-07-18T00:00:01+00:00",
        },
    )
)
print(json.dumps({"sequence": record["chain_sequence"], "hash": record["chain_event_hash"]}))
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(base_dir), run_id, f"evt-worker-{index}", str(ready[index]), str(start)],
            cwd=Path(__file__).parents[5],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    deadline = time.monotonic() + 10
    while not all(path.exists() for path in ready):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    start.write_text("start", encoding="utf-8")
    results: list[dict[str, Any]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        results.append(json.loads(stdout.strip().splitlines()[-1]))
    assert sorted(result["sequence"] for result in results) == [2, 3]

    restarted = FactoryStore(base_dir)
    fourth = asyncio.run(
        restarted.append_authoritative_event(
            run_id,
            {
                "type": "restart_probe",
                "run_id": run_id,
                "event_id": "evt-restart",
                "timestamp": "2026-07-18T00:00:02+00:00",
            },
        )
    )
    raw = (restarted.get_run_dir(run_id) / "events" / "events.jsonl").read_bytes()
    records = decode_factory_event_chain(raw, run_id=run_id)
    assert [record["chain_sequence"] for record in records] == [1, 2, 3, 4]
    assert fourth == records[-1]


@pytest.mark.asyncio
async def test_create_run_orders_durable_admission_then_save_then_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FactoryRunService(tmp_path / "workspace", cache_root=tmp_path / "runtime")
    operations: list[tuple[str, object]] = []

    async def append_authoritative_event(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        record = build_next_factory_event_record((), run_id=run_id, event=event)
        operations.append(("append", record))
        return record

    async def save_run(run: object) -> None:
        operations.append(("save", run))

    async def publish_event(run_id: str, event: dict[str, Any]) -> None:
        operations.append(("publish", (run_id, event)))

    monkeypatch.setattr(service.store, "append_authoritative_event", append_authoritative_event)
    monkeypatch.setattr(service.store, "save_run", save_run)
    monkeypatch.setattr(service, "_publish_factory_event", publish_event)

    run = await service.create_run(FactoryConfig(name="strict", description="intent"))

    assert [operation for operation, _ in operations] == ["append", "save", "publish"]
    admission = operations[0][1]
    assert admission["chain_sequence"] == 1
    assert admission["payload"] == {
        "factory_run_id": run.id,
        "created_at": run.created_at,
        "name": "strict",
        "description": "intent",
    }
    assert operations[2][1] == (run.id, admission)


@pytest.mark.asyncio
async def test_create_run_save_failure_never_publishes_or_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = FactoryRunService(tmp_path / "workspace", cache_root=tmp_path / "runtime")
    published = False

    async def save_run(run: object) -> None:
        del run
        raise OSError("snapshot failed")

    async def publish_event(run_id: str, event: dict[str, Any]) -> None:
        del run_id, event
        nonlocal published
        published = True

    monkeypatch.setattr(service.store, "save_run", save_run)
    monkeypatch.setattr(service, "_publish_factory_event", publish_event)

    with pytest.raises(OSError, match="snapshot failed"):
        await service.create_run(FactoryConfig(name="half-run"))

    assert published is False
    assert service.store.list_runs() == []
    run_dirs = [path for path in service.store.base_dir.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    event_file = run_dirs[0] / "events" / "events.jsonl"
    records = decode_factory_event_chain(event_file.read_bytes(), run_id=run_dirs[0].name)
    assert [record["type"] for record in records] == ["factory_run_admitted"]
    with pytest.raises(FactoryEventChainError) as strict_read:
        await service.store.get_authoritative_events(run_dirs[0].name)
    assert strict_read.value.code == "factory_event_chain_run_snapshot_missing"


def test_record_bound_is_inclusive_at_4096_and_rejects_4097() -> None:
    run_id = "bounded-run"
    first = build_next_factory_event_record((), run_id=run_id, event=_admission_event(run_id=run_id))
    records = [first]
    previous_hash = first["chain_event_hash"]
    for sequence in range(2, FACTORY_EVENT_CHAIN_MAX_RECORDS + 1):
        current = _record(
            event={
                "type": "bounded_probe",
                "run_id": run_id,
                "event_id": f"evt-{sequence}",
                "timestamp": "2026-07-18T00:00:01+00:00",
            },
            sequence=sequence,
            previous_hash=previous_hash,
        )
        records.append(current)
        previous_hash = current["chain_event_hash"]

    validated = validate_factory_event_chain(records, run_id=run_id)
    assert len(validated) == FACTORY_EVENT_CHAIN_MAX_RECORDS
    with pytest.raises(FactoryEventChainError) as exc_info:
        build_next_factory_event_record(
            validated,
            run_id=run_id,
            event={
                "type": "overflow",
                "run_id": run_id,
                "event_id": "evt-overflow",
                "timestamp": "2026-07-18T00:00:02+00:00",
            },
        )
    assert exc_info.value.code == "factory_event_chain_record_limit_exceeded"


@pytest.mark.asyncio
async def test_byte_bound_is_inclusive_and_overflow_does_not_mutate(tmp_path: Path) -> None:
    store = FactoryStore(tmp_path / "factory")
    run_id = "byte-bounded-run"
    base_event = {**_admission_event(run_id=run_id), "padding": ""}
    base_record = build_next_factory_event_record((), run_id=run_id, event=base_event)
    padding_size = FACTORY_EVENT_CHAIN_MAX_BYTES - len(encode_factory_event_record(base_record))
    assert padding_size > 2 * 1024 * 1024
    exact_event = {**base_event, "padding": "x" * padding_size}

    committed = await store.append_authoritative_event(run_id, exact_event)
    event_file = store.get_run_dir(run_id) / "events" / "events.jsonl"
    before = event_file.read_bytes()
    assert len(before) == FACTORY_EVENT_CHAIN_MAX_BYTES
    assert decode_factory_event_chain(before, run_id=run_id) == (committed,)

    with pytest.raises(FactoryEventChainError) as exc_info:
        await store.append_authoritative_event(
            run_id,
            {
                "type": "one-byte-too-many",
                "run_id": run_id,
                "event_id": "evt-overflow",
                "timestamp": "2026-07-18T00:00:02+00:00",
            },
        )
    assert exc_info.value.code == "factory_event_chain_byte_limit_exceeded"
    assert event_file.read_bytes() == before


@pytest.mark.asyncio
async def test_multi_mebibyte_stage_payload_is_authoritatively_chained(tmp_path: Path) -> None:
    store = FactoryStore(tmp_path / "factory")
    run_id = "large-stage-run"
    await store.append_authoritative_event(run_id, _admission_event(run_id=run_id))
    stage = await store.append_authoritative_event(
        run_id,
        {
            "type": "stage_completed",
            "run_id": run_id,
            "event_id": "evt-large-stage",
            "timestamp": "2026-07-18T00:00:02+00:00",
            "result": {"stage": "director_dispatch", "output": "x" * (2 * 1024 * 1024)},
        },
    )

    records = await store.get_authoritative_events(run_id, require_run_snapshot=False)
    assert records[-1] == stage
    assert records[-1]["chain_sequence"] == 2


@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (lambda raw: raw[:-1], "factory_event_chain_half_record"),
        (lambda raw: raw + b"\n", "factory_event_chain_blank_record"),
        (
            lambda raw: raw.replace(b'"type":"factory_run_admitted"', b'"type":"changed"', 1),
            "factory_event_chain_hash_mismatch",
        ),
        (
            lambda raw: raw.replace(
                b'"chain_schema_version":"factory.event_chain.v1"', b'"chain_schema_version":"v0"', 1
            ),
            "factory_event_chain_schema_mismatch",
        ),
    ],
)
@pytest.mark.asyncio
async def test_corrupt_prefix_rejects_append_without_mutation(
    tmp_path: Path,
    mutate: Any,
    error_code: str,
) -> None:
    store = FactoryStore(tmp_path / error_code)
    run_id = "corrupt-run"
    await store.append_authoritative_event(run_id, _admission_event(run_id=run_id))
    event_file = store.get_run_dir(run_id) / "events" / "events.jsonl"
    corrupt = mutate(event_file.read_bytes())
    event_file.write_bytes(corrupt)

    with pytest.raises(FactoryEventChainError) as exc_info:
        await store.append_authoritative_event(
            run_id,
            {
                "type": "must-not-append",
                "run_id": run_id,
                "event_id": "evt-rejected",
                "timestamp": "2026-07-18T00:00:03+00:00",
            },
        )
    assert exc_info.value.code == error_code
    assert event_file.read_bytes() == corrupt


@pytest.mark.parametrize(
    "raw",
    [
        b'{"chain_schema_version":"factory.event_chain.v1","chain_sequence":1,"chain_sequence":1}\n',
        b'{"chain_schema_version":"factory.event_chain.v1","chain_sequence":NaN}\n',
        b"[]\n",
        b"\xff\n",
    ],
)
def test_strict_decoder_rejects_duplicate_nan_nonobject_and_non_utf8(raw: bytes) -> None:
    with pytest.raises(FactoryEventChainError):
        decode_factory_event_chain(raw, run_id="strict-run")


@pytest.mark.asyncio
async def test_durability_failure_propagates_before_create_save_or_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FactoryRunService(tmp_path / "workspace", cache_root=tmp_path / "runtime")
    observed_flags: list[tuple[bool, bool]] = []
    saved = False
    published = False

    def fail_durable_append(
        lease: StreamLeaseV1,
        payload: bytes,
        *,
        fsync_file: bool,
        fsync_parent_on_create: bool,
    ) -> None:
        del lease, payload
        observed_flags.append((fsync_file, fsync_parent_on_create))
        raise OSError("injected fsync failure")

    async def save_run(run: object) -> None:
        del run
        nonlocal saved
        saved = True

    async def publish_event(run_id: str, event: dict[str, Any]) -> None:
        del run_id, event
        nonlocal published
        published = True

    monkeypatch.setattr(StreamLeaseV1, "append_bytes", fail_durable_append)
    monkeypatch.setattr(service.store, "save_run", save_run)
    monkeypatch.setattr(service, "_publish_factory_event", publish_event)

    with pytest.raises(OSError, match="injected fsync failure"):
        await service.create_run(FactoryConfig(name="durability-fail"))

    assert observed_flags == [(True, True)]
    assert saved is False
    assert published is False


async def _recovery_candidate(
    tmp_path: Path,
    *,
    run_id_suffix: str,
    status: FactoryRunStatus,
) -> tuple[FactoryRunService, str]:
    service = FactoryRunService(
        tmp_path / f"workspace-{run_id_suffix}",
        cache_root=tmp_path / f"runtime-{run_id_suffix}",
    )
    run = await service.create_run(
        FactoryConfig(
            name=f"recovery-{run_id_suffix}",
            stages=["pm_planning", "chief_engineer_review", "director_dispatch"],
        )
    )
    run.status = status
    run.recovery_point = None
    run.metadata.pop("last_successful_stage", None)
    await service.store.save_run(run)
    return service, run.id


def _tamper_successful_stage(service: FactoryRunService, run_id: str, *, stage: str) -> None:
    event_file = service.store.get_run_dir(run_id) / "events" / "events.jsonl"
    records = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
    records[-1]["result"]["stage"] = stage
    event_file.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_corrupt_success_event_cannot_change_recovery_point(tmp_path: Path) -> None:
    service, run_id = await _recovery_candidate(
        tmp_path,
        run_id_suffix="recover-corrupt",
        status=FactoryRunStatus.RUNNING,
    )
    await service.store.append_authoritative_event(
        run_id,
        {
            "type": "stage_completed",
            "run_id": run_id,
            "event_id": "evt-success-corrupt-recover",
            "timestamp": "2026-07-18T00:00:01+00:00",
            "result": {"status": "success", "stage": "pm_planning"},
        },
    )
    _tamper_successful_stage(service, run_id, stage="chief_engineer_review")

    with pytest.raises(FactoryEventChainError) as exc_info:
        await service.recover_run(run_id)

    assert exc_info.value.code == "factory_event_chain_hash_mismatch"
    persisted = await service.store.get_run(run_id)
    assert persisted is not None
    assert persisted.recovery_point is None


@pytest.mark.asyncio
async def test_corrupt_success_event_cannot_select_retry_checkpoint(tmp_path: Path) -> None:
    service, run_id = await _recovery_candidate(
        tmp_path,
        run_id_suffix="retry-corrupt",
        status=FactoryRunStatus.FAILED,
    )
    await service.store.append_authoritative_event(
        run_id,
        {
            "type": "stage_completed",
            "run_id": run_id,
            "event_id": "evt-success-corrupt-retry",
            "timestamp": "2026-07-18T00:00:01+00:00",
            "result": {"status": "success", "stage": "pm_planning"},
        },
    )
    _tamper_successful_stage(service, run_id, stage="chief_engineer_review")

    with pytest.raises(FactoryEventChainError) as exc_info:
        await service.retry_run_from_stage(run_id)

    assert exc_info.value.code == "factory_event_chain_hash_mismatch"
    persisted = await service.store.get_run(run_id)
    assert persisted is not None
    assert persisted.recovery_point is None
    assert "retry_execution_stage" not in persisted.metadata


@pytest.mark.asyncio
async def test_legacy_success_event_cannot_drive_recovery(tmp_path: Path) -> None:
    service, run_id = await _recovery_candidate(
        tmp_path,
        run_id_suffix="recover-legacy",
        status=FactoryRunStatus.RUNNING,
    )
    legacy = {
        "type": "stage_completed",
        "run_id": run_id,
        "event_id": "legacy-success",
        "result": {"status": "success", "stage": "chief_engineer_review"},
    }
    event_file = service.store.get_run_dir(run_id) / "events" / "events.jsonl"
    assert (service.store.get_run_dir(run_id) / "run.json").is_file()
    event_file.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    assert await service.get_run_events(run_id) == [legacy]

    with pytest.raises(FactoryEventChainError) as exc_info:
        await service.recover_run(run_id)

    assert exc_info.value.code == "factory_event_chain_legacy_ineligible"
    persisted = await service.store.get_run(run_id)
    assert persisted is not None
    assert persisted.recovery_point is None


@pytest.mark.asyncio
async def test_create_run_admits_before_creating_run_subdirectories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FactoryRunService(tmp_path / "workspace", cache_root=tmp_path / "runtime")
    observed_run_dir_exists: list[bool] = []

    async def append_authoritative_event(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        observed_run_dir_exists.append(service.store.get_run_dir(run_id).exists())
        return build_next_factory_event_record((), run_id=run_id, event=event)

    monkeypatch.setattr(service.store, "append_authoritative_event", append_authoritative_event)
    monkeypatch.setattr(service, "_publish_factory_event", lambda *_args: asyncio.sleep(0))

    run = await service.create_run(FactoryConfig(name="admission-first"))

    assert observed_run_dir_exists == [False]
    assert (service.store.get_run_dir(run.id) / "artifacts").is_dir()
    assert (service.store.get_run_dir(run.id) / "checkpoints").is_dir()


@pytest.mark.asyncio
async def test_create_run_detaches_mutable_config_before_first_await(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FactoryRunService(tmp_path / "workspace", cache_root=tmp_path / "runtime")
    entered = threading.Event()
    release = threading.Event()
    original_append = service.store._append_authoritative_event_sync

    def blocked_append(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(timeout=5)
        return original_append(run_id, event)

    monkeypatch.setattr(service.store, "_append_authoritative_event_sync", blocked_append)
    caller_config = FactoryConfig(
        name="original",
        description="original-description",
        stages=["pm_planning"],
    )
    task = asyncio.create_task(service.create_run(caller_config))
    assert await asyncio.to_thread(entered.wait, 5)
    caller_config.name = "mutated"
    caller_config.description = "mutated-description"
    caller_config.stages.append("director_dispatch")
    release.set()

    run = await task

    assert run.config is not caller_config
    assert run.config.name == "original"
    assert run.config.description == "original-description"
    assert run.config.stages == ["pm_planning"]
    persisted = await service.store.get_run(run.id)
    assert persisted is not None
    assert persisted.config.stages == ["pm_planning"]
    admission = (await service.store.get_authoritative_events(run.id))[0]
    assert admission["payload"]["name"] == "original"


@pytest.mark.asyncio
async def test_admission_append_fsyncs_real_directory_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FactoryStore(tmp_path / "factory")
    run_id = "fsync-chain-run"
    run_dir = store.get_run_dir(run_id)
    real_fsync = os.fsync
    directory_fsyncs: list[Path] = []

    def recording_fsync(fd: int) -> None:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            target = Path(os.readlink(f"/proc/self/fd/{fd}")).resolve()
            if target == store.base_dir or store.base_dir in target.parents:
                directory_fsyncs.append(target)
        real_fsync(fd)

    monkeypatch.setattr(locked_regular_file_module.os, "fsync", recording_fsync)

    await store.append_authoritative_event(run_id, _admission_event(run_id=run_id))

    assert directory_fsyncs == [run_dir / "events", run_dir, store.base_dir]


@pytest.mark.asyncio
async def test_admission_directory_fsync_failure_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FactoryStore(tmp_path / "factory")
    run_id = "fsync-failure-run"
    run_dir = store.get_run_dir(run_id)
    real_fsync = os.fsync

    def failing_fsync(fd: int) -> None:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            target = Path(os.readlink(f"/proc/self/fd/{fd}")).resolve()
            if target == run_dir / "events":
                raise OSError(errno.EIO, "injected directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(locked_regular_file_module.os, "fsync", failing_fsync)

    with pytest.raises(LockedRegularFileError) as exc_info:
        await store.append_authoritative_event(run_id, _admission_event(run_id=run_id))

    assert exc_info.value.code == "post_fsync_authority_reconciliation_required"
    assert exc_info.value.details["cause_code"] == "stream_directory_fsync_failed"
    assert (run_dir / "events" / "events.jsonl").is_file()


@pytest.mark.asyncio
async def test_cancelled_create_waits_for_worker_then_leaves_admission_only_and_unlocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FactoryRunService(tmp_path / "workspace", cache_root=tmp_path / "runtime")
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    run_ids: list[str] = []
    published: list[tuple[str, dict[str, Any]]] = []
    original_append = service.store._append_authoritative_event_sync

    def blocked_append(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        run_ids.append(run_id)
        entered.set()
        assert release.wait(timeout=5)
        try:
            return original_append(run_id, event)
        finally:
            completed.set()

    monkeypatch.setattr(service.store, "_append_authoritative_event_sync", blocked_append)

    async def publish(run_id: str, event: dict[str, Any]) -> None:
        published.append((run_id, event))

    monkeypatch.setattr(service, "_publish_factory_event", publish)
    task = asyncio.create_task(service.create_run(FactoryConfig(name="cancel-during-admission")))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release_timer = threading.Timer(0.2, release.set)
    release_timer.start()

    with pytest.raises(asyncio.CancelledError):
        await task
    completed_when_cancel_propagated = completed.is_set()
    release_timer.join(timeout=1)
    assert await asyncio.to_thread(completed.wait, 5)
    assert completed_when_cancel_propagated is True

    assert len(run_ids) == 1
    run_id = run_ids[0]
    run_dir = service.store.get_run_dir(run_id)
    assert sorted(path.name for path in run_dir.iterdir()) == ["events"]
    assert not (run_dir / "run.json").exists()
    records = await service.store.get_authoritative_events(run_id, require_run_snapshot=False)
    assert [record["type"] for record in records] == ["factory_run_admitted"]
    assert published == []
    assert service.store.list_runs() == []


@pytest.mark.asyncio
async def test_cancelled_append_consumes_terminal_worker_failure_without_masking_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FactoryStore(tmp_path / "factory")
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    loop_failures: list[dict[str, Any]] = []

    def failing_worker(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        del run_id, event
        entered.set()
        assert release.wait(timeout=5)
        completed.set()
        raise OSError("injected worker failure after cancellation")

    monkeypatch.setattr(store, "_append_authoritative_event_sync", failing_worker)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_failures.append(dict(context)))
    try:
        task = asyncio.create_task(
            store.append_authoritative_event(
                "cancelled-worker-failure",
                _admission_event(run_id="cancelled-worker-failure"),
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        release_timer = threading.Timer(0.2, release.set)
        release_timer.start()

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        completed_when_cancel_propagated = completed.is_set()
        release_timer.join(timeout=1)
        await asyncio.sleep(0)

        assert completed_when_cancel_propagated is True
        assert isinstance(exc_info.value.__cause__, OSError)
        assert str(exc_info.value.__cause__) == "injected worker failure after cancellation"
        assert loop_failures == []
    finally:
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_repeated_cancellation_still_waits_for_worker_and_releases_stream_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FactoryStore(tmp_path / "factory")
    run_id = "repeated-cancellation"
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    original_append = store._append_authoritative_event_sync

    def blocked_worker(worker_run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(timeout=5)
        try:
            return original_append(worker_run_id, event)
        finally:
            completed.set()

    monkeypatch.setattr(store, "_append_authoritative_event_sync", blocked_worker)
    task = asyncio.create_task(store.append_authoritative_event(run_id, _admission_event(run_id=run_id)))
    assert await asyncio.to_thread(entered.wait, 5)

    assert task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task

    assert completed.is_set()
    assert exc_info.value.__cause__ is None
    second = await store.append_authoritative_event(
        run_id,
        {
            "type": "stage_started",
            "run_id": run_id,
            "event_id": "evt-after-repeated-cancel",
            "timestamp": "2026-07-18T00:00:01+00:00",
        },
    )
    assert second["chain_sequence"] == 2
    records = await store.get_authoritative_events(run_id, require_run_snapshot=False)
    assert [record["chain_sequence"] for record in records] == [1, 2]


@pytest.mark.asyncio
async def test_first_admission_fresh_anchor_contention_times_out_without_factory_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FactoryRunService(tmp_path / "workspace", cache_root=tmp_path / "runtime")
    published: list[tuple[str, dict[str, Any]]] = []
    original_flock = locked_regular_file_module._flock
    flock_calls = 0
    release_timer: threading.Timer | None = None

    async def publish(run_id: str, event: dict[str, Any]) -> None:
        published.append((run_id, event))

    def contend_fresh_anchor(fd: int, operation: int, *, deadline: float | None = None) -> None:
        nonlocal flock_calls, release_timer
        flock_calls += 1
        if flock_calls != 2:
            original_flock(fd, operation, deadline=deadline)
            return

        anchor_path = os.readlink(f"/proc/self/fd/{fd}")
        assert anchor_path.endswith("anchor.lock")
        blocker_fd = os.open(anchor_path, os.O_RDWR)
        assert locked_regular_file_module.fcntl is not None
        locked_regular_file_module.fcntl.flock(blocker_fd, locked_regular_file_module.fcntl.LOCK_EX)

        def release_blocker() -> None:
            assert locked_regular_file_module.fcntl is not None
            locked_regular_file_module.fcntl.flock(blocker_fd, locked_regular_file_module.fcntl.LOCK_UN)
            os.close(blocker_fd)

        release_timer = threading.Timer(0.25, release_blocker)
        release_timer.start()
        original_flock(fd, operation, deadline=deadline)

    monkeypatch.setattr(service, "_publish_factory_event", publish)
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_ACQUIRE_ATTEMPTS", 1)
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_RETRY_SLEEP_SECONDS", 0.0)
    monkeypatch.setattr(locked_regular_file_module, "_flock", contend_fresh_anchor)

    started = time.monotonic()
    with pytest.raises(LockedRegularFileError) as exc_info:
        await service.create_run(FactoryConfig(name="fresh-anchor-contention"))
    elapsed = time.monotonic() - started
    if release_timer is not None:
        release_timer.join(timeout=1)

    assert exc_info.value.code == "lock_acquisition_timeout"
    assert 0.03 <= elapsed < 0.2
    assert service.store.list_runs() == []
    assert list(service.store.base_dir.rglob("events.jsonl")) == []
    assert list(service.store.base_dir.rglob("run.json")) == []
    assert published == []


def test_authoritative_append_obeys_configured_cross_process_lock_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dir = tmp_path / "factory"
    run_id = "lock-timeout-run"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    # Keep the contention budget short for unit time; production default is higher (R186).
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_ACQUIRE_ATTEMPTS", 1)
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_RETRY_SLEEP_SECONDS", 0.0)
    store = FactoryStore(base_dir)
    asyncio.run(store.append_authoritative_event(run_id, _admission_event(run_id=run_id)))
    script = """
import time
import sys
from pathlib import Path
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.kernelone.fs.locked_regular_file import LockedRegularFileSetV1

base_dir, run_id, ready_path, release_path = sys.argv[1:]
store = FactoryStore(Path(base_dir))
logical_path = store._authoritative_event_logical_path(run_id)
store._provision_authoritative_event_lock(logical_path)
with LockedRegularFileSetV1.acquire(
    runtime_root=str(store.base_dir),
    storage_identity_token=store._event_storage_identity,
    logical_paths=(logical_path,),
    platform_lock_root=str(store._event_lock_root),
    timeout_seconds=2.0,
):
    Path(ready_path).write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 15
    while not Path(release_path).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("release barrier timed out")
        time.sleep(0.01)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(base_dir), run_id, str(ready), str(release)],
        cwd=Path(__file__).parents[5],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        started = time.monotonic()
        with pytest.raises(LockedRegularFileError) as exc_info:
            asyncio.run(
                store.append_authoritative_event(
                    run_id,
                    {
                        "type": "must-time-out",
                        "run_id": run_id,
                        "event_id": "evt-timeout",
                        "timestamp": "2026-07-18T00:00:01+00:00",
                    },
                )
            )
        elapsed = time.monotonic() - started
        assert exc_info.value.code == "lock_acquisition_timeout"
        assert 4.5 <= elapsed < 7.5
    finally:
        release.write_text("release", encoding="utf-8")
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, f"{stdout}\n{stderr}"


@pytest.mark.asyncio
async def test_first_admission_uses_one_configured_budget_while_other_stream_holds_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FactoryRunService(tmp_path / "workspace", cache_root=tmp_path / "runtime")
    published: list[tuple[str, dict[str, Any]]] = []

    async def publish(run_id: str, event: dict[str, Any]) -> None:
        published.append((run_id, event))

    monkeypatch.setattr(service, "_publish_factory_event", publish)
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_ACQUIRE_ATTEMPTS", 1)
    monkeypatch.setattr(factory_store_module, "_FACTORY_EVENT_LOCK_RETRY_SLEEP_SECONDS", 0.0)
    run_a = await service.create_run(FactoryConfig(name="run-a"))
    published.clear()
    base_dir = service.store.base_dir
    ready = tmp_path / "different-stream-ready"
    release = tmp_path / "different-stream-release"
    realm_dir = service.store._event_lock_root / service.store._event_storage_identity / "realm"
    keys_before = {path.name for path in realm_dir.iterdir()}
    script = """
import time
import sys
from pathlib import Path
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.kernelone.fs.locked_regular_file import LockedRegularFileSetV1

base_dir, run_id, ready_path, release_path = sys.argv[1:]
store = FactoryStore(Path(base_dir))
logical_path = store._authoritative_event_logical_path(run_id)
with LockedRegularFileSetV1.acquire(
    runtime_root=str(store.base_dir),
    storage_identity_token=store._event_storage_identity,
    logical_paths=(logical_path,),
    platform_lock_root=str(store._event_lock_root),
    timeout_seconds=2.0,
):
    Path(ready_path).write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 10
    while not Path(release_path).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(base_dir), run_a.id, str(ready), str(release)],
        cwd=Path(__file__).parents[5],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready_deadline = time.monotonic() + 5
        while not ready.exists():
            assert process.poll() is None
            assert time.monotonic() < ready_deadline
            await asyncio.sleep(0.01)

        started = time.monotonic()
        failure: LockedRegularFileError | None = None
        try:
            await service.create_run(FactoryConfig(name="run-b-blocked"))
        except LockedRegularFileError as exc:
            failure = exc
        elapsed = time.monotonic() - started

        assert failure is not None
        assert failure.code == "lock_acquisition_timeout"
        assert 4.5 <= elapsed < 7.5
        assert {path.name for path in realm_dir.iterdir()} == keys_before
        assert service.store.list_runs() == [run_a.id]
        assert published == []
        assert [path.name for path in base_dir.iterdir()] == [run_a.id]
    finally:
        release.write_text("release", encoding="utf-8")
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, f"{stdout}\n{stderr}"

    run_b = await service.create_run(FactoryConfig(name="run-b-success"))
    assert sorted(service.store.list_runs()) == sorted([run_a.id, run_b.id])
    assert [run_id for run_id, _event in published] == [run_b.id]
    assert len({path.name for path in realm_dir.iterdir()}) == len(keys_before) + 1


@pytest.mark.asyncio
async def test_partial_admission_write_is_strictly_rejected_and_never_self_heals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FactoryStore(tmp_path / "factory")
    run_id = "partial-write-run"
    logical_path = store._authoritative_event_logical_path(run_id)
    store._provision_authoritative_event_lock(logical_path)
    real_write = os.write
    injected = False
    fail_next = False

    def partial_then_fail(fd: int, payload: bytes | memoryview) -> int:
        nonlocal injected, fail_next
        target = os.readlink(f"/proc/self/fd/{fd}")
        if target.endswith("events.jsonl"):
            if fail_next:
                fail_next = False
                raise OSError(errno.EIO, "injected partial append failure")
            if not injected:
                injected = True
                fail_next = True
                partial_size = max(1, len(payload) // 2)
                return real_write(fd, payload[:partial_size])
        return real_write(fd, payload)

    monkeypatch.setattr(locked_regular_file_module.os, "write", partial_then_fail)

    with pytest.raises(LockedRegularFileError) as write_error:
        await store.append_authoritative_event(run_id, _admission_event(run_id=run_id))
    assert write_error.value.code == "append_write_failed"
    event_file = store.get_run_dir(run_id) / "events" / "events.jsonl"
    partial_bytes = event_file.read_bytes()
    assert partial_bytes and not partial_bytes.endswith(b"\n")

    with pytest.raises(FactoryEventChainError) as read_error:
        await store.get_authoritative_events(run_id, require_run_snapshot=False)
    assert read_error.value.code == "factory_event_chain_half_record"
    with pytest.raises(FactoryEventChainError) as append_error:
        await store.append_authoritative_event(run_id, _admission_event(run_id=run_id))
    assert append_error.value.code == "factory_event_chain_half_record"
    assert event_file.read_bytes() == partial_bytes
