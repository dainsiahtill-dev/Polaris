"""Strict history reads authenticate all records without retaining all payloads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
)
from polaris.cells.events.fact_stream.public.service import (
    FactStreamError,
    append_fact_event,
    query_fact_events,
    query_fact_stream_head,
)
from polaris.cells.events.fact_stream.public.workspace_bootstrap import bootstrap_fact_stream_workspace
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.events.sourcing import EventSourcingError, JsonlEventStore
from polaris.kernelone.fs import StreamLeaseV1, set_default_adapter

_STREAM = "task_runtime.execution"
_BUDGET = 8 * 1024 * 1024


def _prepare(workspace: Path, count: int, *, payload_size: int = 20) -> Path:
    workspace.mkdir(parents=True)
    set_default_adapter(LocalFileSystemAdapter())
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace), streams=(_STREAM,), maintenance_reason="strict_history_read_regression"
        )
    )
    for index in range(count):
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream=_STREAM,
                event_type="heartbeat_renewed" if index % 2 else "execution_closed",
                source="runtime.task_runtime",
                payload={"index": index, "snapshot": "x" * payload_size},
                run_id=f"run-{index % 2}",
                task_id=f"task-{index % 3}",
            )
        )
    return (
        Path(JsonlEventStore(str(workspace), read_only=True).storage_identity.runtime_root)
        / "events"
        / f"{_STREAM}.jsonl"
    )


@pytest.fixture(scope="module")
def large_history(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    workspace = tmp_path_factory.mktemp("strict-large") / "workspace"
    path = _prepare(workspace, 218, payload_size=48_000)
    assert path.stat().st_size > _BUDGET
    return workspace, path


def test_public_strict_query_paginates_complete_history_larger_than_memory_budget(
    large_history: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, path = large_history
    physical_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    expected = [json.loads(line) for line in physical_lines]
    record_bytes = {
        record["event_id"]: len(line.encode("utf-8"))
        for record, line in zip(expected, physical_lines, strict=True)
    }

    def full_history_read_forbidden(_lease: StreamLeaseV1) -> bytes:
        raise AssertionError("strict query retained complete history bytes")

    # First prove the public history can be read, then require bounded descriptor I/O.
    first = query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream=_STREAM, limit=1000, strict_integrity=True)
    )
    assert first.total == 218
    assert first.next_offset > 0
    assert sum(record_bytes[event["event_id"]] for event in first.events) <= _BUDGET
    monkeypatch.setattr(StreamLeaseV1, "read_bytes", full_history_read_forbidden)
    actual = []
    offset = 0
    while True:
        page = query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace), stream=_STREAM, limit=1000, offset=offset, strict_integrity=True
            )
        )
        assert page.total == 218
        assert sum(record_bytes[event["event_id"]] for event in page.events) <= _BUDGET
        actual.extend(page.events)
        if page.next_offset == 0:
            break
        assert page.next_offset > offset
        offset = page.next_offset
    assert actual == expected
    assert (
        query_fact_stream_head(
            QueryFactStreamHeadV1(workspace=str(workspace), stream=_STREAM, strict_integrity=True)
        ).current_seq
        == 218
    )


def test_public_strict_filters_keep_original_records_and_digest(large_history: tuple[Path, Path]) -> None:
    workspace, path = large_history
    expected = [
        record
        for line in path.read_text(encoding="utf-8").splitlines()
        if (record := json.loads(line))["event_type"] == "execution_closed"
        and record["metadata"]["run_id"] == "run-0"
        and record["metadata"]["task_id"] == "task-0"
    ]
    page = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream=_STREAM,
            event_type="execution_closed",
            run_id="run-0",
            task_id="task-0",
            limit=1000,
            strict_integrity=True,
        )
    )
    assert list(page.events) == expected
    assert page.total == len(expected)
    assert page.next_offset == 0


@pytest.mark.parametrize(
    "damage", ["same_inode_tamper", "torn_tail", "missing_record", "blank_record", "foreign_stream"]
)
def test_strict_page_validates_unselected_history_before_returning(tmp_path: Path, damage: str) -> None:
    workspace = tmp_path / "workspace"
    path = _prepare(workspace, 4)
    query = QueryFactEventsV1(
        workspace=str(workspace), stream=_STREAM, limit=1, task_id="task-0", strict_integrity=True
    )
    assert query_fact_events(query).events
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    if damage == "same_inode_tamper":
        inode = path.stat().st_ino
        lines[2] = lines[2].replace('"index":2', '"index":9')
        path.write_text("".join(lines), encoding="utf-8")
        assert path.stat().st_ino == inode
    elif damage == "torn_tail":
        path.write_text(content[:-1], encoding="utf-8")
    elif damage == "missing_record":
        path.write_text("".join(lines[:2] + lines[3:]), encoding="utf-8")
    elif damage == "blank_record":
        path.write_text("".join(lines[:2]) + "\n" + "".join(lines[2:]), encoding="utf-8")
    else:
        lines[2] = lines[2].replace(_STREAM, "foreign.execution")
        path.write_text("".join(lines), encoding="utf-8")
    with pytest.raises(FactStreamError) as rejected:
        query_fact_events(query)
    assert rejected.value.code == "strict_stream_corruption"


def test_strict_streaming_keeps_record_and_count_guards(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    path = _prepare(workspace, 3, payload_size=1000)
    limited = JsonlEventStore(str(workspace), strict_max_bytes=512)
    with pytest.raises(EventSourcingError) as rejected:
        limited.query(stream=_STREAM, strict_integrity=True)
    assert rejected.value.code == "strict_scan_limit_exceeded"
    assert rejected.value.details["limit"] == "max_bytes"
    assert path.stat().st_size > 512
    counted = JsonlEventStore(str(workspace), strict_max_records=2)
    with pytest.raises(EventSourcingError) as rejected:
        counted.query(stream=_STREAM, limit=1, strict_integrity=True)
    assert rejected.value.code == "strict_scan_limit_exceeded"
    assert rejected.value.details["limit"] == "max_records"
