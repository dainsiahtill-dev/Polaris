"""Unit tests for `events/fact_stream` public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.events.fact_stream.public.catalog import fact_stream_bootstrap_streams
from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    FactEventAppendedV1,
    FactStreamError,
    FactStreamHeadV1,
    FactStreamProvenanceV1,
    FactStreamQueryResultV1,
    ProvisionFactStreamLockAuthorityCommandV1,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
)


def test_bootstrap_catalog_is_static_and_nonempty() -> None:
    streams = fact_stream_bootstrap_streams()

    assert streams
    assert len(streams) == len(set(streams))
    assert streams == tuple(sorted(streams))
    assert "factory.settlement" in streams
    assert "task_runtime.execution" in streams


def test_bootstrap_catalog_excludes_reserved_segmented_namespaces() -> None:
    streams = fact_stream_bootstrap_streams()
    reserved_prefixes = (
        "roles.kernel.provider_attempts.factory.",
        "roles.kernel.provider_attempts.session.",
        "factory.role_evidence_authority.",
    )

    assert all(not stream.startswith(reserved_prefixes) for stream in streams)
    assert all(".segmented" not in stream for stream in streams)


def test_authority_provision_command_requires_explicit_maintenance_intent() -> None:
    command = ProvisionFactStreamLockAuthorityCommandV1(
        workspace="/repo",
        streams=fact_stream_bootstrap_streams(),
        maintenance_reason="http_app_lifespan_startup",
    )

    assert command.streams == fact_stream_bootstrap_streams()
    with pytest.raises(ValueError, match="maintenance_reason"):
        ProvisionFactStreamLockAuthorityCommandV1(
            workspace="/repo",
            streams=("task_runtime.execution",),
            maintenance_reason=" ",
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
        assert cmd.durability == "buffered"
        assert cmd.strict_integrity is False

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


class TestAppendFactEventCommandV1ExpectedSeq:
    def test_default_is_none(self) -> None:
        cmd = AppendFactEventCommandV1(
            workspace="/repo",
            stream="audit",
            event_type="x",
            payload={"k": "v"},
            source="src",
        )
        assert cmd.expected_seq is None

    def test_valid_expected_seq_kept(self) -> None:
        cmd = AppendFactEventCommandV1(
            workspace="/repo",
            stream="audit",
            event_type="x",
            payload={"k": "v"},
            source="src",
            expected_seq=7,
        )
        assert cmd.expected_seq == 7

    def test_zero_expected_seq_raises(self) -> None:
        with pytest.raises(ValueError, match="expected_seq"):
            AppendFactEventCommandV1(
                workspace="/repo",
                stream="audit",
                event_type="x",
                payload={"k": "v"},
                source="src",
                expected_seq=0,
            )

    def test_negative_expected_seq_raises(self) -> None:
        with pytest.raises(ValueError, match="expected_seq"):
            AppendFactEventCommandV1(
                workspace="/repo",
                stream="audit",
                event_type="x",
                payload={"k": "v"},
                source="src",
                expected_seq=-1,
            )

    def test_bool_expected_seq_raises(self) -> None:
        with pytest.raises(ValueError, match="expected_seq"):
            AppendFactEventCommandV1(
                workspace="/repo",
                stream="audit",
                event_type="x",
                payload={"k": "v"},
                source="src",
                expected_seq=True,  # type: ignore[arg-type]
            )

    def test_str_expected_seq_raises(self) -> None:
        with pytest.raises(ValueError, match="expected_seq"):
            AppendFactEventCommandV1(
                workspace="/repo",
                stream="audit",
                event_type="x",
                payload={"k": "v"},
                source="src",
                expected_seq="7",  # type: ignore[arg-type]
            )


class TestQueryFactEventsV1HappyPath:
    def test_defaults(self) -> None:
        q = QueryFactEventsV1(workspace="/repo", stream="audit")
        assert q.limit == 100
        assert q.offset == 0
        assert q.event_type is None
        assert q.strict_integrity is False

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


class TestFactStreamHeadContracts:
    def test_query_requires_workspace_and_stream(self) -> None:
        query = QueryFactStreamHeadV1(workspace="/repo", stream="audit")
        assert query.workspace == "/repo"
        assert query.stream == "audit"
        assert query.strict_integrity is False

        with pytest.raises(ValueError, match="workspace"):
            QueryFactStreamHeadV1(workspace="", stream="audit")

    def test_head_requires_adjacent_sequences(self) -> None:
        head = FactStreamHeadV1(
            workspace="/repo",
            stream="audit",
            storage_path="runtime/events/audit.jsonl",
            current_seq=3,
            next_expected_seq=4,
        )
        assert head.current_seq == 3
        assert head.next_expected_seq == 4

        with pytest.raises(ValueError, match=r"current_seq \+ 1"):
            FactStreamHeadV1(
                workspace="/repo",
                stream="audit",
                storage_path="runtime/events/audit.jsonl",
                current_seq=3,
                next_expected_seq=5,
            )


def test_turn_transition_provenance_requires_all_identity_fields() -> None:
    provenance = FactStreamProvenanceV1(
        workspace="/workspace",
        run_id="run-1",
        task_id="task-1",
        turn_id="turn-1",
        transition_id="transition-1",
    )

    assert provenance.to_record()["transition_id"] == "transition-1"
    with pytest.raises(ValueError, match="transition_id"):
        FactStreamProvenanceV1(
            workspace="/workspace",
            run_id="run-1",
            task_id="task-1",
            turn_id="turn-1",
            transition_id="",
        )


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

    def test_default_appended_seq_is_none(self) -> None:
        evt = FactEventAppendedV1(
            event_id="e1",
            workspace="/repo",
            stream="audit",
            storage_path="/f",
            appended_at="2026-03-24T10:00:00Z",
        )
        assert evt.appended_seq is None

    def test_valid_appended_seq_kept(self) -> None:
        evt = FactEventAppendedV1(
            event_id="e1",
            workspace="/repo",
            stream="audit",
            storage_path="/f",
            appended_at="2026-03-24T10:00:00Z",
            appended_seq=42,
        )
        assert evt.appended_seq == 42

    def test_zero_appended_seq_raises(self) -> None:
        with pytest.raises(ValueError, match="appended_seq"):
            FactEventAppendedV1(
                event_id="e1",
                workspace="/repo",
                stream="audit",
                storage_path="/f",
                appended_at="2026-03-24T10:00:00Z",
                appended_seq=0,
            )

    def test_bool_appended_seq_raises(self) -> None:
        with pytest.raises(ValueError, match="appended_seq"):
            FactEventAppendedV1(
                event_id="e1",
                workspace="/repo",
                stream="audit",
                storage_path="/f",
                appended_at="2026-03-24T10:00:00Z",
                appended_seq=True,  # type: ignore[arg-type]
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

    def test_details_recursively_detach_nested_mappings_and_sequences(self) -> None:
        nested_mapping: dict[str, list[dict[str, str]]] = {"entries": [{"state": "before"}]}
        nested_tuple_mapping: dict[str, list[str]] = {"nested": ["before"]}
        source = {
            "mapping": nested_mapping,
            "tuple": ("fixed", nested_tuple_mapping),
        }

        err = FactStreamError("append failed", details=source)
        nested_mapping["entries"][0]["state"] = "after"
        nested_tuple_mapping["nested"].append("after")
        err.details["mapping"]["entries"].append({"state": "error-only"})

        assert err.details == {
            "mapping": {"entries": [{"state": "before"}, {"state": "error-only"}]},
            "tuple": ("fixed", {"nested": ["before"]}),
        }
        assert source == {
            "mapping": {"entries": [{"state": "after"}]},
            "tuple": ("fixed", {"nested": ["before", "after"]}),
        }

    def test_details_normalize_unknown_values_with_public_serialization_policy(self) -> None:
        class ExternalDetail:
            def __str__(self) -> str:
                return "external-detail"

        err = FactStreamError("append failed", details={"external": ExternalDetail()})

        assert err.details == {"external": "external-detail"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            FactStreamError("")
