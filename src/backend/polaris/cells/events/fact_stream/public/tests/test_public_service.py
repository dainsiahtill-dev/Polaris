from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
from polaris.cells.events.fact_stream.public.service import (
    AppendFactEventCommandV1,
    FactStreamError,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_append_fact_event_and_query_roundtrip(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            payload={"task_id": "task-1", "run_id": "run-1"},
            source="runtime.task_runtime",
            task_id="task-1",
            run_id="run-1",
        )
    )
    assert appended.workspace == str(workspace)
    assert appended.stream == "task_runtime.execution"
    assert appended.storage_path == "runtime/events/task_runtime.execution.jsonl"
    assert str(appended.event_id).strip()

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            limit=50,
            offset=0,
            task_id="task-1",
        )
    )
    assert queried.total == 1
    assert len(queried.events) == 1
    assert queried.events[0]["event_type"] == "claimed"
    assert queried.events[0]["task_id"] == "task-1"


def test_append_fact_event_is_idempotent_by_key(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    command = AppendFactEventCommandV1(
        workspace=str(workspace),
        stream="task_runtime.execution",
        event_type="claimed",
        payload={"task_id": "task-idem", "run_id": "run-idem"},
        source="runtime.task_runtime",
        task_id="task-idem",
        run_id="run-idem",
        idempotency_key="outbox-idem-1",
    )

    first = append_fact_event(command)
    second = append_fact_event(command)

    assert second.event_id == first.event_id

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            limit=50,
            offset=0,
            task_id="task-idem",
        )
    )
    assert queried.total == 1


def test_query_fact_events_pagination(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    for idx in range(3):
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="taskboard.terminal.events",
                event_type="completed",
                payload={"task_id": f"task-{idx}"},
                source="runtime.task_runtime.task_board",
                task_id=f"task-{idx}",
            )
        )

    first_page = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="taskboard.terminal.events",
            limit=2,
            offset=0,
        )
    )
    assert first_page.total == 3
    assert len(first_page.events) == 2
    assert first_page.next_offset == 2

    second_page = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="taskboard.terminal.events",
            limit=2,
            offset=first_page.next_offset,
        )
    )
    assert second_page.total == 3
    assert len(second_page.events) == 1
    assert second_page.next_offset == 0


def test_append_fact_event_default_expected_seq_is_none_and_assigns_seq_one(
    tmp_path: Path,
) -> None:
    """Default append behaviour is unchanged: no expected_seq → next free seq."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            payload={"task_id": "task-default", "run_id": "run-default"},
            source="runtime.task_runtime",
            task_id="task-default",
            run_id="run-default",
        )
    )

    # appended_seq is filled in by the service for callers that care; old
    # callers that don't read it must still work.
    assert appended.appended_seq == 1
    assert appended.event_id

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 1


def test_append_fact_event_expected_seq_match_succeeds(tmp_path: Path) -> None:
    """CAS path: caller supplies expected_seq matching next free → append lands."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    first = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq",
            event_type="claimed",
            payload={"task_id": "task-cas", "run_id": "run-cas"},
            source="runtime.task_runtime",
            task_id="task-cas",
            run_id="run-cas",
        )
    )
    assert first.appended_seq == 1

    second = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq",
            event_type="completed",
            payload={"task_id": "task-cas", "run_id": "run-cas"},
            source="runtime.task_runtime",
            task_id="task-cas",
            run_id="run-cas",
            expected_seq=2,
        )
    )
    assert second.appended_seq == 2

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="ledger.expected_seq",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 2
    assert [evt["seq"] for evt in queried.events] == [1, 2]


def test_append_fact_event_expected_seq_drift_fails_closed_and_does_not_append(
    tmp_path: Path,
) -> None:
    """CAS drift must raise FactStreamError and not produce any new event."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.drift",
            event_type="claimed",
            payload={"task_id": "task-drift", "run_id": "run-drift"},
            source="runtime.task_runtime",
            task_id="task-drift",
            run_id="run-drift",
        )
    )

    # Stream already holds seq=1, so expected_seq=99 must fail-closed.
    with pytest.raises(FactStreamError) as exc_info:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="ledger.expected_seq.drift",
                event_type="completed",
                payload={"task_id": "task-drift", "run_id": "run-drift"},
                source="runtime.task_runtime",
                task_id="task-drift",
                run_id="run-drift",
                expected_seq=99,
            )
        )

    assert exc_info.value.code == "expected_seq_drift"
    assert exc_info.value.details.get("expected_seq") == 99

    # Crucially: no second event was written.
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.drift",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 1
    assert queried.events[0]["event_type"] == "claimed"


def test_append_fact_event_idempotent_hit_with_mismatched_expected_seq_fails(
    tmp_path: Path,
) -> None:
    """Idempotent hit + CAS drift must fail-closed instead of silently
    returning the original event.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    command = AppendFactEventCommandV1(
        workspace=str(workspace),
        stream="ledger.expected_seq.idem",
        event_type="claimed",
        payload={"task_id": "task-idem-cas", "run_id": "run-idem-cas"},
        source="runtime.task_runtime",
        task_id="task-idem-cas",
        run_id="run-idem-cas",
        idempotency_key="idem-key-cas-1",
    )

    first = append_fact_event(command)
    assert first.appended_seq == 1

    # Replay with mismatched expected_seq must fail-closed.
    with pytest.raises(FactStreamError) as exc_info:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="ledger.expected_seq.idem",
                event_type="claimed",
                payload={"task_id": "task-idem-cas", "run_id": "run-idem-cas"},
                source="runtime.task_runtime",
                task_id="task-idem-cas",
                run_id="run-idem-cas",
                idempotency_key="idem-key-cas-1",
                expected_seq=42,
            )
        )

    assert exc_info.value.code == "expected_seq_drift"
    assert exc_info.value.details.get("existing_seq") == 1
    assert exc_info.value.details.get("expected_seq") == 42

    # Confirm we did NOT write a duplicate event.
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.idem",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 1


def test_append_fact_event_idempotent_hit_with_matching_expected_seq_succeeds(
    tmp_path: Path,
) -> None:
    """Idempotent hit + matching expected_seq must return the original
    event and not produce a duplicate write.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    first = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.idem.match",
            event_type="claimed",
            payload={"task_id": "task-idem-match", "run_id": "run-idem-match"},
            source="runtime.task_runtime",
            task_id="task-idem-match",
            run_id="run-idem-match",
            idempotency_key="idem-key-match-1",
            expected_seq=1,
        )
    )
    assert first.appended_seq == 1

    # Same idempotency key + matching expected_seq → idempotent return.
    second = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.idem.match",
            event_type="claimed",
            payload={"task_id": "task-idem-match", "run_id": "run-idem-match"},
            source="runtime.task_runtime",
            task_id="task-idem-match",
            run_id="run-idem-match",
            idempotency_key="idem-key-match-1",
            expected_seq=1,
        )
    )
    assert second.event_id == first.event_id
    assert second.appended_seq == 1

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.idem.match",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 1


def test_append_fact_event_concurrent_idempotency_is_atomic(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    command = AppendFactEventCommandV1(
        workspace=str(workspace),
        stream="roles.kernel.turn_outcomes",
        event_type="turn_outcome_committed",
        payload={"run_id": "run-atomic", "turn_id": "turn-atomic"},
        source="roles.kernel",
        run_id="run-atomic",
        task_id="task-atomic",
        idempotency_key="run-atomic:task-atomic:turn-atomic",
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: append_fact_event(command), range(16)))

    assert len({result.event_id for result in results}) == 1
    assert {result.appended_seq for result in results} == {1}
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="roles.kernel.turn_outcomes",
            limit=20,
        )
    )
    assert queried.total == 1


def test_append_fact_event_rejects_idempotency_key_payload_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            source="roles.kernel",
            run_id="run-conflict",
            task_id="task-conflict",
            idempotency_key="run-conflict:task-conflict:turn-conflict",
            payload={"run_id": "run-conflict", "outcome_status": "completed"},
        )
    )

    with pytest.raises(FactStreamError) as exc_info:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="roles.kernel.turn_outcomes",
                event_type="turn_outcome_committed",
                source="roles.kernel",
                run_id="run-conflict",
                task_id="task-conflict",
                idempotency_key="run-conflict:task-conflict:turn-conflict",
                payload={"run_id": "run-conflict", "outcome_status": "failed"},
            )
        )

    assert exc_info.value.code == "idempotency_conflict"
