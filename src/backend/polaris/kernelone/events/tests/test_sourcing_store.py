from __future__ import annotations

import builtins
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.events.sourcing import (
    EventEnvelope,
    EventSourcingError,
    IdempotencyConflictError,
    JsonlEventStore,
    file_store,
)
from polaris.kernelone.fs import KernelFileSystem, set_default_adapter
from polaris.kernelone.fs.contracts import FileDurabilityError
from polaris.kernelone.fs.locked_regular_file import (
    LockedRegularFileSetV1,
    StreamLeaseV1,
    default_platform_lock_root,
)


def _provision_store(store: JsonlEventStore, paths: tuple[str, ...]) -> None:
    identity = store.storage_identity
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=default_platform_lock_root(),
        storage_identity_token=identity.token,
        runtime_root=identity.runtime_root,
    )
    LockedRegularFileSetV1.enroll_stream_lock_keys(
        platform_lock_root=default_platform_lock_root(),
        storage_identity_token=identity.token,
        runtime_root=identity.runtime_root,
        logical_paths=paths,
    )


def _prepared_store(workspace: Path, *streams: str) -> JsonlEventStore:
    workspace.mkdir(parents=True, exist_ok=True)
    store = JsonlEventStore(str(workspace))
    _provision_store(store, tuple(store.stream_logical_path(stream) for stream in streams))
    return store


@pytest.fixture(autouse=True)
def _inject_kernel_fs_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


def _append_from_process(workspace: str, index: int, output: multiprocessing.Queue[int]) -> None:
    """Append a distinct event from a spawned process for lock verification."""

    set_default_adapter(LocalFileSystemAdapter())
    store = JsonlEventStore(workspace)
    _provision_store(store, (store.stream_logical_path("multiprocess.strict"),))
    event = store.append(
        stream="multiprocess.strict",
        event_type="recorded",
        source="test",
        payload={"index": index},
        idempotency_key=f"process-{index}",
        strict_integrity=True,
        durability="fsync",
    )
    output.put(event.seq)


def test_jsonl_event_store_appends_monotonic_seq(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "taskboard.terminal.events")

    first = store.append(
        stream="taskboard.terminal.events",
        event_type="completed",
        source="runtime.task_runtime",
        payload={"task_id": "task-1"},
    )
    second = store.append(
        stream="taskboard.terminal.events",
        event_type="failed",
        source="runtime.task_runtime",
        payload={"task_id": "task-2"},
    )

    assert first.seq == 1
    assert second.seq == 2
    assert first.event_version == 1

    result = store.query(stream="taskboard.terminal.events", limit=20, offset=0)
    assert result.total == 2
    assert [event.seq for event in result.events] == [1, 2]
    assert result.storage_path == "runtime/events/taskboard.terminal.events.jsonl"


def test_current_seq_reads_only_tail_record_in_non_strict_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Head queries must not deserialize every historical event payload."""

    store = _prepared_store(tmp_path / "workspace", "task_runtime.execution")
    for index in range(3):
        store.append(
            stream="task_runtime.execution",
            event_type="heartbeat",
            source="runtime.task_runtime",
            payload={"task_id": "task-1", "large_snapshot": "x" * 20_000, "index": index},
        )

    def _full_scan_forbidden(*_args: object, **_kwargs: object) -> list[EventEnvelope]:
        raise AssertionError("current_seq performed a full historical JSON parse")

    monkeypatch.setattr(store, "_parse_records", _full_scan_forbidden)

    assert store.current_seq("task_runtime.execution", strict_integrity=False) == 3


def test_jsonl_event_store_query_filters_by_event_type_run_and_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "task_runtime.execution")

    store.append(
        stream="task_runtime.execution",
        event_type="claimed",
        source="runtime.task_runtime",
        payload={"task_id": "task-1", "run_id": "run-A"},
    )
    store.append(
        stream="task_runtime.execution",
        event_type="completed",
        source="runtime.task_runtime",
        payload={"task_id": "task-1", "run_id": "run-A"},
    )
    store.append(
        stream="task_runtime.execution",
        event_type="failed",
        source="runtime.task_runtime",
        payload={"task_id": "task-2", "run_id": "run-B"},
    )

    filtered = store.query(
        stream="task_runtime.execution",
        limit=10,
        offset=0,
        event_type="completed",
        run_id="run-A",
        task_id="task-1",
    )
    assert filtered.total == 1
    assert len(filtered.events) == 1
    assert filtered.events[0].event_type == "completed"
    assert filtered.events[0].payload["task_id"] == "task-1"


def test_jsonl_event_store_expected_seq_does_not_commit_seq_when_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "task_runtime.execution")
    logical_path = store.stream_logical_path("task_runtime.execution")
    absolute_path = str(store._kernel_fs.resolve_path(logical_path))

    def fail_append_bytes(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(StreamLeaseV1, "append_bytes", fail_append_bytes)

    with pytest.raises(EventSourcingError, match="failed to append event"):
        store.append(
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            payload={"task_id": "task-1"},
            expected_seq=1,
        )

    assert not Path(f"{absolute_path}.seq").exists()


def test_jsonl_event_store_rejects_stream_path_outside_workspace_runtime_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _prepared_store(workspace, "roles.kernel.turn_outcomes")

    class EscapingKernelFileSystem:
        def resolve_path(self, _logical_path: str) -> Path:
            return tmp_path / "another-workspace" / "runtime" / "events" / "escaped.jsonl"

    store = JsonlEventStore(str(workspace), kernel_fs=EscapingKernelFileSystem())

    with pytest.raises(EventSourcingError, match="escaped workspace runtime root"):
        store.append(
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            source="roles.kernel.transaction",
            payload={"outcome": "completed"},
        )


def test_jsonl_event_store_concurrent_idempotent_append_commits_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _prepared_store(workspace, "roles.kernel.turn_outcomes")

    def append_once() -> tuple[str, int]:
        store = JsonlEventStore(str(workspace))
        event = store.append(
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            source="roles.kernel",
            payload={"run_id": "run-1", "turn_id": "turn-1"},
            metadata={"idempotency_key": "run-1:turn-1"},
            idempotency_key="run-1:turn-1",
        )
        return event.event_id, event.seq

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: append_once(), range(16)))

    assert len({event_id for event_id, _seq in results}) == 1
    assert {seq for _event_id, seq in results} == {1}
    queried = _prepared_store(workspace, "roles.kernel.turn_outcomes").query(
        stream="roles.kernel.turn_outcomes",
        limit=20,
    )
    assert queried.total == 1


def test_jsonl_event_store_identical_semantic_replay_returns_original_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "roles.kernel.turn_outcomes")
    metadata: dict[str, Any] = {
        "idempotency_key": "transition-1",
        "provenance": {
            "workspace": str(workspace.resolve()),
            "run_id": "run-1",
            "task_id": "TASK-1",
            "turn_id": "turn-1",
            "transition_id": "transition-1",
        },
    }

    def append_once() -> Any:
        return store.append(
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            event_version=2,
            source="roles.kernel.transaction",
            payload={"outcome": {"status": "completed"}},
            aggregate_id="TASK-1",
            correlation_id="turn-1",
            causation_id="request-1",
            metadata=metadata,
            idempotency_key="transition-1",
        )

    first = append_once()
    replay = append_once()

    assert replay.event_id == first.event_id
    assert replay.seq == first.seq
    assert replay.occurred_at == first.occurred_at
    assert store.query(stream="roles.kernel.turn_outcomes").total == 1


def test_jsonl_event_store_same_payload_with_different_provenance_conflicts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "roles.kernel.turn_outcomes")
    base_metadata: dict[str, Any] = {
        "idempotency_key": "transition-1",
        "provenance": {
            "workspace": str(workspace.resolve()),
            "run_id": "run-1",
            "task_id": "TASK-1",
            "turn_id": "turn-1",
            "transition_id": "transition-1",
        },
    }
    store.append(
        stream="roles.kernel.turn_outcomes",
        event_type="turn_outcome_committed",
        source="roles.kernel.transaction",
        payload={"outcome": {"status": "completed"}},
        aggregate_id="TASK-1",
        correlation_id="turn-1",
        metadata=base_metadata,
        idempotency_key="transition-1",
    )
    drifted_metadata = {
        **base_metadata,
        "provenance": {
            **base_metadata["provenance"],
            "transition_id": "different-transition",
        },
    }

    with pytest.raises(EventSourcingError, match=r"idempotency conflict:.*fields=metadata") as caught:
        store.append(
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            source="roles.kernel.transaction",
            payload={"outcome": {"status": "completed"}},
            aggregate_id="TASK-1",
            correlation_id="turn-1",
            metadata=drifted_metadata,
            idempotency_key="transition-1",
        )

    assert isinstance(caught.value, IdempotencyConflictError)
    assert caught.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    ("field", "override"),
    [
        ("event_type", "turn_outcome_rejected"),
        ("event_version", 2),
        ("source", "roles.kernel.other"),
        ("aggregate_id", "TASK-2"),
        ("correlation_id", "turn-2"),
        ("causation_id", "request-2"),
    ],
)
def test_jsonl_event_store_idempotency_compares_full_semantic_envelope(
    tmp_path: Path,
    field: str,
    override: object,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "roles.kernel.turn_outcomes")
    append: dict[str, object] = {
        "stream": "roles.kernel.turn_outcomes",
        "event_type": "turn_outcome_committed",
        "event_version": 1,
        "source": "roles.kernel.transaction",
        "payload": {"outcome": "completed"},
        "aggregate_id": "TASK-1",
        "correlation_id": "turn-1",
        "causation_id": "request-1",
        "metadata": {"idempotency_key": "transition-1", "run_id": "run-1"},
        "idempotency_key": "transition-1",
    }
    store.append(**append)  # type: ignore[arg-type]
    append[field] = override

    with pytest.raises(EventSourcingError, match=rf"idempotency conflict:.*fields={field}"):
        store.append(**append)  # type: ignore[arg-type]


def test_jsonl_event_store_concurrent_unique_appends_have_monotonic_sequences(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _prepared_store(workspace, "roles.kernel.turn_outcomes.unique")

    def append_once(index: int) -> int:
        event = JsonlEventStore(str(workspace)).append(
            stream="roles.kernel.turn_outcomes.unique",
            event_type="turn_outcome_committed",
            source="roles.kernel",
            payload={"run_id": "run-1", "turn_id": f"turn-{index}"},
            metadata={"idempotency_key": f"run-1:turn-{index}"},
            idempotency_key=f"run-1:turn-{index}",
        )
        return event.seq

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(append_once, range(16)))

    assert sorted(sequences) == list(range(1, 17))


@pytest.mark.parametrize(
    ("durability", "expected_calls"),
    [
        ("buffered", ["open", "write", "close"]),
        ("flush", ["open", "write", "flush", "close"]),
        ("fsync", ["open", "write", "flush", "fileno", "fsync", "close"]),
    ],
)
def test_local_adapter_append_durability_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    durability: str,
    expected_calls: list[str],
) -> None:
    """The adapter must not claim a durable append before its stage completes."""

    calls: list[str] = []

    class RecordingHandle:
        def __enter__(self) -> RecordingHandle:
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append("close")

        def write(self, _data: str) -> int:
            calls.append("write")
            return 1

        def flush(self) -> None:
            calls.append("flush")

        def fileno(self) -> int:
            calls.append("fileno")
            return 37

    def fake_open(*_args: object, **_kwargs: object) -> RecordingHandle:
        calls.append("open")
        return RecordingHandle()

    target = tmp_path / "events.jsonl"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(
        "polaris.infrastructure.storage.local_fs_adapter.os.fsync",
        lambda _fd: calls.append("fsync"),
    )
    LocalFileSystemAdapter().append_text(str(target), "{}\n", durability=durability)  # type: ignore[arg-type]

    assert calls == expected_calls


def test_local_adapter_buffered_append_uses_legacy_native_newline_parameter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class RecordingHandle:
        def __enter__(self) -> RecordingHandle:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write(self, _data: str) -> int:
            return 1

    def fake_open(*_args: object, **kwargs: object) -> RecordingHandle:
        captured_kwargs.update(kwargs)
        return RecordingHandle()

    target = tmp_path / "events.jsonl"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(builtins, "open", fake_open)

    LocalFileSystemAdapter().append_text(str(target), "{}\n", durability="buffered")

    assert captured_kwargs == {"encoding": "utf-8"}


def test_local_adapter_fsyncs_parent_after_creating_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if file_store.os.name == "nt":
        pytest.skip("POSIX directory fsync has no Windows equivalent")
    calls: list[str] = []

    class RecordingHandle:
        def __enter__(self) -> RecordingHandle:
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append("close_file")

        def write(self, _data: str) -> int:
            calls.append("write")
            return 1

        def flush(self) -> None:
            calls.append("flush")

        def fileno(self) -> int:
            calls.append("fileno")
            return 37

    def fake_open(*_args: object, **_kwargs: object) -> RecordingHandle:
        calls.append("open_file")
        return RecordingHandle()

    def fake_open_parent(*_args: object) -> int:
        calls.append("open_parent")
        return 91

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(
        "polaris.infrastructure.storage.local_fs_adapter.os.open",
        fake_open_parent,
    )
    monkeypatch.setattr(
        "polaris.infrastructure.storage.local_fs_adapter.os.fsync",
        lambda fd: calls.append(f"fsync_{fd}"),
    )
    monkeypatch.setattr(
        "polaris.infrastructure.storage.local_fs_adapter.os.close",
        lambda fd: calls.append(f"close_{fd}"),
    )

    LocalFileSystemAdapter().append_text(str(tmp_path / "created.jsonl"), "{}\n", durability="fsync")

    assert calls == [
        "open_file",
        "write",
        "flush",
        "fileno",
        "fsync_37",
        "close_file",
        "open_parent",
        "fsync_91",
        "close_91",
    ]


def test_local_adapter_windows_contract_does_not_attempt_posix_parent_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(*_args: object) -> int:
        raise AssertionError("Windows must not open a directory for POSIX fsync")

    monkeypatch.setattr("polaris.infrastructure.storage.local_fs_adapter.os.name", "nt")
    monkeypatch.setattr("polaris.infrastructure.storage.local_fs_adapter.os.open", fail_open)

    LocalFileSystemAdapter._fsync_created_file_parent(tmp_path)


def test_local_adapter_fsync_skips_parent_for_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "existing.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    calls: list[Path] = []
    monkeypatch.setattr(
        LocalFileSystemAdapter,
        "_fsync_created_file_parent",
        lambda parent: calls.append(parent),
    )

    LocalFileSystemAdapter().append_text(str(target), "{}\n", durability="fsync")

    assert calls == []


def test_local_adapter_parent_fsync_failure_has_typed_exception_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_parent_fsync(_parent: Path) -> None:
        raise OSError("simulated parent fsync failure")

    monkeypatch.setattr(
        LocalFileSystemAdapter,
        "_fsync_created_file_parent",
        staticmethod(fail_parent_fsync),
    )

    with pytest.raises(FileDurabilityError) as caught:
        LocalFileSystemAdapter().append_text(str(tmp_path / "events.jsonl"), "{}\n", durability="fsync")

    assert caught.value.stage == "parent_fsync"
    assert isinstance(caught.value.__cause__, OSError)


def test_local_adapter_fsync_failure_has_typed_exception_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_fsync(_fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr("polaris.infrastructure.storage.local_fs_adapter.os.fsync", fail_fsync)

    with pytest.raises(FileDurabilityError) as caught:
        LocalFileSystemAdapter().append_text(str(tmp_path / "events.jsonl"), "{}\n", durability="fsync")

    assert caught.value.stage == "fsync"
    assert isinstance(caught.value.__cause__, OSError)


def test_kernel_filesystem_append_jsonl_preserves_buffered_default_and_utf8(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str, str]] = []

    class RecordingAdapter(LocalFileSystemAdapter):
        def append_text(
            self,
            path: str,
            content: str,
            *,
            encoding: str = "utf-8",
            durability: str = "buffered",
        ) -> int:
            calls.append((path, content, encoding, durability))
            return super().append_text(path, content, encoding=encoding, durability=durability)  # type: ignore[arg-type]

    fs = KernelFileSystem(str(tmp_path), RecordingAdapter())
    fs.append_jsonl("runtime/events/utf8.jsonl", {"message": "中文"})

    assert calls[0][2:] == ("utf-8", "buffered")
    assert "中文" in fs.resolve_path("runtime/events/utf8.jsonl").read_text(encoding="utf-8")


def test_jsonl_event_store_strict_reader_accepts_valid_stream(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.valid")
    store.append(
        stream="strict.valid",
        event_type="recorded",
        source="test",
        payload={"n": 1},
        strict_integrity=True,
    )

    result = store.query(stream="strict.valid", strict_integrity=True)

    assert [event.seq for event in result.events] == [1]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_jsonl_event_store_strict_reader_preserves_unicode_line_separators_inside_json_strings(
    tmp_path: Path,
    separator: str,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.unicode_line_separator")
    message = f"before{separator}after"
    store.append(
        stream="strict.unicode_line_separator",
        event_type="recorded",
        source="test",
        payload={"message": message},
        strict_integrity=True,
    )

    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.unicode_line_separator"))
    persisted = path.read_bytes()
    result = store.query(stream="strict.unicode_line_separator", strict_integrity=True)

    assert separator.encode("utf-8") in persisted
    assert persisted.count(b"\n") == 1
    assert result.events[0].payload["message"] == message


def test_jsonl_event_store_strict_reader_accepts_crlf_without_creating_empty_tail(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.crlf")
    for index in range(2):
        store.append(
            stream="strict.crlf",
            event_type="recorded",
            source="test",
            payload={"index": index},
            strict_integrity=True,
        )
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.crlf"))
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace("\n", "\r\n"), encoding="utf-8", newline="")

    result = store.query(stream="strict.crlf", strict_integrity=True)

    assert [event.seq for event in result.events] == [1, 2]


def test_jsonl_event_store_strict_reader_treats_bare_cr_as_record_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.bare_cr")
    first = store.append(
        stream="strict.bare_cr",
        event_type="recorded",
        source="test",
        payload={"index": 1},
        strict_integrity=True,
    )
    second = store.append(
        stream="strict.bare_cr",
        event_type="recorded",
        source="test",
        payload={"index": 2},
        strict_integrity=True,
    )
    first_record = _strict_record_json(first.to_record(include_integrity_digest=True))
    second_record = _strict_record_json(second.to_record(include_integrity_digest=True))
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.bare_cr"))
    path.write_text(f"{first_record}\r{second_record}\n", encoding="utf-8", newline="")

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.bare_cr", strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "middle_corruption"
    assert caught.value.details["reason_code"] == "stream_corruption"
    assert getattr(caught.value.__cause__, "code", None) == "strict_record_corruption"
    assert caught.value.details["physical_line"] == 1


def test_jsonl_event_store_strict_reader_preserves_torn_tail_after_crlf_record(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.crlf_torn_tail")
    event = store.append(
        stream="strict.crlf_torn_tail",
        event_type="recorded",
        source="test",
        payload={"message": "before\u2028after"},
        strict_integrity=True,
    )
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.crlf_torn_tail"))
    first_record = _strict_record_json(event.to_record(include_integrity_digest=True))
    path.write_text(f'{first_record}\r\n{{"schema_version":1', encoding="utf-8", newline="")

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.crlf_torn_tail", strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "torn_tail"
    assert caught.value.details["reason_code"] == "torn_tail"
    assert caught.value.details["strict_failure_code"] == "torn_tail"
    assert caught.value.details["physical_line"] == 2
    assert caught.value.details["recovery_required"] is True


def test_jsonl_event_store_strict_reader_rejects_extra_empty_crlf_record(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.empty_crlf_tail")
    event = store.append(
        stream="strict.empty_crlf_tail",
        event_type="recorded",
        source="test",
        payload={"value": 1},
        strict_integrity=True,
    )
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.empty_crlf_tail"))
    record = _strict_record_json(event.to_record(include_integrity_digest=True))
    path.write_text(f"{record}\r\n\r\n", encoding="utf-8", newline="")

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.empty_crlf_tail", strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "middle_corruption"
    assert caught.value.details["physical_line"] == 2


def test_jsonl_event_store_strict_reader_marks_torn_tail_and_blocks_append(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.torn")
    logical_path = store.stream_logical_path("strict.torn")
    path = store._kernel_fs.resolve_path(logical_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema_version":1', encoding="utf-8")

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.torn", strict_integrity=True)
    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "torn_tail"
    assert caught.value.details["recovery_required"] is True

    with pytest.raises(EventSourcingError, match="malformed non-newline-terminated tail"):
        store.append(
            stream="strict.torn",
            event_type="recorded",
            source="test",
            payload={"n": 2},
            strict_integrity=True,
        )


def test_jsonl_event_store_strict_reader_rejects_middle_corruption(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.corrupt")
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.corrupt"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.corrupt", strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "middle_corruption"


def test_jsonl_event_store_strict_reader_projects_unknown_schema_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.unknown_schema")
    stream = "strict.unknown_schema"
    record = _strict_record(store, stream)
    record["schema_version"] = 2
    _refresh_strict_digest(record)
    _write_strict_record(store, stream, record)

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream=stream, strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "unknown_schema"
    assert caught.value.details["reason_code"] == "unknown_schema_version"
    assert caught.value.details["strict_failure_code"] == "unknown_schema_version"


@pytest.mark.parametrize("sequences", [(1, 1), (1, 3)])
def test_jsonl_event_store_strict_reader_rejects_duplicate_and_gapped_sequences(
    tmp_path: Path,
    sequences: tuple[int, int],
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.sequence")
    first = store.append(
        stream="strict.sequence",
        event_type="recorded",
        source="test",
        payload={"n": 1},
        strict_integrity=True,
    )
    second = store.append(
        stream="strict.sequence",
        event_type="recorded",
        source="test",
        payload={"n": 2},
        strict_integrity=True,
    )
    first_record = first.to_record(include_integrity_digest=True)
    second_record = second.to_record(include_integrity_digest=True)
    first_record["seq"], second_record["seq"] = sequences
    first_record["integrity_digest"] = first.integrity_digest_for_record(first_record)
    second_record["integrity_digest"] = second.integrity_digest_for_record(second_record)
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.sequence"))
    path.write_text(
        f"{file_store.json.dumps(first_record)}\n{file_store.json.dumps(second_record)}\n",
        encoding="utf-8",
    )

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.sequence", strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "sequence_violation"
    assert caught.value.details["reason_code"] == "sequence_violation"
    assert caught.value.details["strict_failure_code"] == "sequence_violation"


def test_jsonl_event_store_legacy_reader_keeps_skipping_malformed_records(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "legacy.compat")
    path = store._kernel_fs.resolve_path(store.stream_logical_path("legacy.compat"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json}\n", encoding="utf-8")

    assert store.query(stream="legacy.compat").events == ()


def test_jsonl_event_store_strict_reader_rejects_digest_tampering(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.digest")
    store.append(
        stream="strict.digest",
        event_type="recorded",
        source="test",
        payload={"n": 1},
        strict_integrity=True,
    )
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.digest"))
    record = file_store.json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["n"] = 2
    path.write_text(file_store.json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.digest", strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "middle_corruption"
    assert caught.value.details["reason_code"] == "integrity_digest_mismatch"


def test_jsonl_event_store_strict_reader_rejects_missing_digest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.missing_digest")
    store.append(
        stream="strict.missing_digest",
        event_type="recorded",
        source="test",
        payload={"n": 1},
        strict_integrity=True,
    )
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.missing_digest"))
    record = file_store.json.loads(path.read_text(encoding="utf-8"))
    record.pop("integrity_digest")
    path.write_text(file_store.json.dumps(record) + "\n", encoding="utf-8")

    assert len(store.query(stream="strict.missing_digest").events) == 1

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.missing_digest", strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "middle_corruption"
    assert caught.value.details["reason_code"] == "missing_integrity_digest"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("event_version", True),
        ("event_version", 1.0),
        ("event_version", "1"),
        ("seq", True),
        ("seq", 1.0),
        ("seq", "1"),
    ],
)
def test_jsonl_event_store_strict_reader_rejects_non_exact_integer_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.integer_fields")
    event = store.append(
        stream="strict.integer_fields",
        event_type="recorded",
        source="test",
        payload={"n": 1},
        strict_integrity=True,
    )
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.integer_fields"))
    record = event.to_record(include_integrity_digest=True)
    record[field] = value
    record["integrity_digest"] = event.integrity_digest_for_record(record)
    path.write_text(file_store.json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream="strict.integer_fields", strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "middle_corruption"
    assert caught.value.details["reason_code"] == "invalid_raw_integer"
    assert caught.value.details["field"] == field


def _strict_record_json(record: dict[str, Any]) -> str:
    return file_store.json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _write_strict_record(store: JsonlEventStore, stream: str, record: dict[str, Any]) -> None:
    path = store._kernel_fs.resolve_path(store.stream_logical_path(stream))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_strict_record_json(record) + "\n", encoding="utf-8")


def _strict_record(store: JsonlEventStore, stream: str) -> dict[str, Any]:
    event = store.append(
        stream=stream,
        event_type="recorded",
        source="test",
        payload={"value": 1},
        strict_integrity=True,
    )
    return event.to_record(include_integrity_digest=True)


def _refresh_strict_digest(record: dict[str, Any]) -> None:
    record["integrity_digest"] = EventEnvelope.integrity_digest_for_record(record)


@pytest.mark.parametrize(
    ("case", "expected_field"),
    [
        ("event_id_int", "event_id"),
        ("source_bool", "source"),
        ("occurred_at_list", "occurred_at"),
        ("payload_list", "payload"),
        ("metadata_null", "metadata"),
        ("extra_field", "unexpected"),
        ("missing_event_id", "event_id"),
        ("digest_type", "integrity_digest"),
        ("digest_case", "integrity_digest"),
        ("digest_length", "integrity_digest"),
    ],
)
def test_jsonl_event_store_strict_decoder_rejects_invalid_raw_shapes(
    tmp_path: Path,
    case: str,
    expected_field: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stream = "strict.raw_shape"
    store = _prepared_store(workspace, stream)
    record = _strict_record(store, stream)

    if case == "event_id_int":
        record["event_id"] = 7
        _refresh_strict_digest(record)
    elif case == "source_bool":
        record["source"] = True
        _refresh_strict_digest(record)
    elif case == "occurred_at_list":
        record["occurred_at"] = ["2026-07-14T00:00:00Z"]
        _refresh_strict_digest(record)
    elif case == "payload_list":
        record["payload"] = ["not", "an", "object"]
        _refresh_strict_digest(record)
    elif case == "metadata_null":
        record["metadata"] = None
        _refresh_strict_digest(record)
    elif case == "extra_field":
        record["unexpected"] = "field"
        _refresh_strict_digest(record)
    elif case == "missing_event_id":
        record.pop("event_id")
        _refresh_strict_digest(record)
    elif case == "digest_type":
        record["integrity_digest"] = 7
    elif case == "digest_case":
        record["integrity_digest"] = str(record["integrity_digest"]).upper()
    elif case == "digest_length":
        record["integrity_digest"] = "a" * 63
    else:  # pragma: no cover - keeps the table exhaustive if a case is added.
        raise AssertionError(f"unsupported strict decoder case: {case}")
    _write_strict_record(store, stream, record)

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream=stream, strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "middle_corruption"
    assert caught.value.details["field"] == expected_field
    assert caught.value.details["reason"]


@pytest.mark.parametrize("location", ["top", "payload", "metadata"])
def test_jsonl_event_store_strict_decoder_rejects_duplicate_keys_at_every_depth(
    tmp_path: Path,
    location: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stream = "strict.duplicate_keys"
    store = _prepared_store(workspace, stream)
    record = _strict_record(store, stream)
    if location == "top":
        encoded = _strict_record_json(record).replace(
            '{"schema_version":1,',
            '{"schema_version":1,"schema_version":1,',
            1,
        )
    else:
        record[location] = {"nested": {"value": 1}}
        _refresh_strict_digest(record)
        encoded = _strict_record_json(record).replace(
            '"nested":{"value":1}',
            '"nested":{"value":1,"value":1}',
            1,
        )
    path = store._kernel_fs.resolve_path(store.stream_logical_path(stream))
    path.write_text(encoded + "\n", encoding="utf-8")

    with pytest.raises(EventSourcingError) as caught:
        store.query(stream=stream, strict_integrity=True)

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_reason"] == "middle_corruption"
    assert caught.value.details["reason"] == "duplicate_key"


def test_jsonl_event_store_strict_decoder_preserves_legal_unicode_without_normalization(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stream = "strict.unicode"
    store = _prepared_store(workspace, stream)
    record = _strict_record(store, stream)
    decomposed = "e\u0301 中文"
    record["payload"] = {"message": decomposed}
    record["metadata"] = {"label": decomposed}
    _refresh_strict_digest(record)
    _write_strict_record(store, stream, record)

    result = store.query(stream=stream, strict_integrity=True)

    assert result.events[0].payload["message"] == decomposed
    assert result.events[0].metadata["label"] == decomposed


@pytest.mark.parametrize("stale_cursor", [0, 999])
def test_jsonl_event_store_strict_append_uses_verified_stream_head_not_cursor(
    tmp_path: Path,
    stale_cursor: int,
) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.cursor")
    store.append(
        stream="strict.cursor",
        event_type="recorded",
        source="test",
        payload={"n": 1},
        strict_integrity=True,
    )
    absolute_path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.cursor"))
    Path(f"{absolute_path}.seq").write_text(str(stale_cursor), encoding="utf-8")

    second = store.append(
        stream="strict.cursor",
        event_type="recorded",
        source="test",
        payload={"n": 2},
        expected_seq=2,
        strict_integrity=True,
    )

    assert second.seq == 2
    assert Path(f"{absolute_path}.seq").read_text(encoding="utf-8").strip() == str(stale_cursor)


def test_jsonl_event_store_strict_scan_limits_are_typed_and_legacy_is_unbounded(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    writer = _prepared_store(workspace, "strict.limits")
    for index in range(2):
        writer.append(
            stream="strict.limits",
            event_type="recorded",
            source="test",
            payload={"n": index},
            strict_integrity=True,
        )

    limited = JsonlEventStore(str(workspace), strict_max_records=1)
    with pytest.raises(EventSourcingError) as caught:
        limited.query(stream="strict.limits", strict_integrity=True)

    assert caught.value.code == "strict_scan_limit_exceeded"
    assert caught.value.details["limit"] == "max_records"
    assert len(limited.query(stream="strict.limits").events) == 2

    byte_limited = JsonlEventStore(str(workspace), strict_max_bytes=1)
    with pytest.raises(EventSourcingError) as caught:
        byte_limited.query(stream="strict.limits", strict_integrity=True)

    assert caught.value.code == "strict_scan_limit_exceeded"
    assert caught.value.details["limit"] == "max_bytes"


def test_jsonl_event_store_reports_lock_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = _prepared_store(workspace, "strict.lock")
    lock_path = store.stream_logical_path("strict.lock")
    with (
        LockedRegularFileSetV1.acquire(
            runtime_root=store.storage_identity.runtime_root,
            storage_identity_token=store.storage_identity.token,
            logical_paths=(lock_path,),
        ),
        pytest.raises(EventSourcingError) as caught,
    ):
        store.append(stream="strict.lock", event_type="recorded", source="test", payload={"n": 1})
    assert caught.value.code == "lock_acquisition_timeout"


def test_jsonl_event_store_strict_append_is_multiprocess_monotonic(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("this integration test requires a POSIX fork process context")
    context = multiprocessing.get_context("fork")
    output: multiprocessing.Queue[int] = context.Queue()
    processes = [
        context.Process(target=_append_from_process, args=(str(workspace), index, output)) for index in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sorted(output.get(timeout=2) for _ in processes) == [1, 2, 3, 4]
    result = _prepared_store(workspace, "multiprocess.strict").query(stream="multiprocess.strict", strict_integrity=True)
    assert [event.seq for event in result.events] == [1, 2, 3, 4]
