"""Unit tests for `events/fact_stream` public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    FactEventAppendedV1,
    FactStreamError,
    FactStreamQueryResultV1,
    QueryFactEventsV1,
)


class TestAppendFactEventCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = AppendFactEventCommandV1(
            workspace="/repo",
            stream="audit",
            event_type="file.created",
            payload={"path": "/a.txt"},
            source="director",
        )
        assert cmd.workspace == "/repo"
        assert cmd.stream == "audit"
        assert cmd.event_type == "file.created"
        assert cmd.payload == {"path": "/a.txt"}
        assert cmd.source == "director"
        assert cmd.run_id is None
        assert cmd.task_id is None

    def test_with_optional_ids(self) -> None:
        cmd = AppendFactEventCommandV1(
            workspace="/repo",
            stream="audit",
            event_type="file.created",
            payload={"path": "/a.txt"},
            source="director",
            run_id="run-1",
            task_id="task-1",
            correlation_id="corr-1",
        )
        assert cmd.run_id == "run-1"
        assert cmd.task_id == "task-1"
        assert cmd.correlation_id == "corr-1"

    def test_payload_is_copied(self) -> None:
        original = {"path": "/a.txt"}
        cmd = AppendFactEventCommandV1(workspace="/repo", stream="audit", event_type="x", payload=original, source="y")
        original.clear()
        assert cmd.payload == {"path": "/a.txt"}


class TestAppendFactEventCommandV1EdgeCases:
    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            AppendFactEventCommandV1(workspace="", stream="audit", event_type="x", payload={}, source="y")

    def test_empty_stream_raises(self) -> None:
        with pytest.raises(ValueError, match="stream"):
            AppendFactEventCommandV1(workspace="/repo", stream="", event_type="x", payload={}, source="y")

    def test_empty_event_type_raises(self) -> None:
        with pytest.raises(ValueError, match="event_type"):
            AppendFactEventCommandV1(workspace="/repo", stream="audit", event_type="", payload={}, source="y")

    def test_empty_source_raises(self) -> None:
        with pytest.raises(ValueError, match="source"):
            AppendFactEventCommandV1(workspace="/repo", stream="audit", event_type="x", payload={}, source="")

    def test_empty_payload_raises(self) -> None:
        with pytest.raises(ValueError, match="payload"):
            AppendFactEventCommandV1(workspace="/repo", stream="audit", event_type="x", payload={}, source="y")


class TestQueryFactEventsV1HappyPath:
    def test_defaults(self) -> None:
        q = QueryFactEventsV1(workspace="/repo", stream="audit")
        assert q.limit == 100
        assert q.offset == 0
        assert q.event_type is None

    def test_with_filters(self) -> None:
        q = QueryFactEventsV1(
            workspace="/repo",
            stream="audit",
            limit=50,
            offset=10,
            event_type="file.created",
            run_id="run-1",
            task_id="task-1",
        )
        assert q.limit == 50
        assert q.offset == 10
        assert q.event_type == "file.created"
        assert q.run_id == "run-1"


class TestQueryFactEventsV1EdgeCases:
    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            QueryFactEventsV1(workspace="", stream="audit")

    def test_limit_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            QueryFactEventsV1(workspace="/repo", stream="audit", limit=0)

    def test_negative_offset_raises(self) -> None:
        with pytest.raises(ValueError, match="offset"):
            QueryFactEventsV1(workspace="/repo", stream="audit", offset=-1)


class TestFactEventAppendedV1HappyPath:
    def test_construction(self) -> None:
        evt = FactEventAppendedV1(
            event_id="evt-1",
            workspace="/repo",
            stream="audit",
            storage_path="/facts/evt-1.json",
            appended_at="2026-03-24T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.storage_path == "/facts/evt-1.json"


class TestFactEventAppendedV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            FactEventAppendedV1(
                event_id="",
                workspace="/repo",
                stream="audit",
                storage_path="/f",
                appended_at="2026-03-24T10:00:00Z",
            )

    def test_empty_storage_path_raises(self) -> None:
        with pytest.raises(ValueError, match="storage_path"):
            FactEventAppendedV1(
                event_id="e1",
                workspace="/repo",
                stream="audit",
                storage_path="",
                appended_at="2026-03-24T10:00:00Z",
            )


class TestFactStreamQueryResultV1HappyPath:
    def test_construction(self) -> None:
        res = FactStreamQueryResultV1(
            workspace="/repo",
            stream="audit",
            events=({"event_id": "e1"},),
            total=1,
            next_offset=1,
        )
        assert res.total == 1
        assert len(res.events) == 1
        assert res.next_offset == 1

    def test_events_tuple_is_copied(self) -> None:
        original = [{"event_id": "e1"}]
        res = FactStreamQueryResultV1(workspace="/repo", stream="audit", events=original)  # type: ignore[arg-type]
        original.clear()
        assert len(res.events) == 1


class TestFactStreamQueryResultV1EdgeCases:
    def test_negative_total_raises(self) -> None:
        with pytest.raises(ValueError, match="total"):
            FactStreamQueryResultV1(workspace="/repo", stream="audit", total=-1)

    def test_negative_next_offset_raises(self) -> None:
        with pytest.raises(ValueError, match="next_offset"):
            FactStreamQueryResultV1(workspace="/repo", stream="audit", next_offset=-1)


class TestFactStreamError:
    def test_default_values(self) -> None:
        err = FactStreamError("stream unavailable")
        assert str(err) == "stream unavailable"
        assert err.code == "fact_stream_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = FactStreamError(
            "append failed",
            code="append_rejected",
            details={"stream": "audit"},
        )
        assert err.code == "append_rejected"
        assert err.details == {"stream": "audit"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            FactStreamError("")
