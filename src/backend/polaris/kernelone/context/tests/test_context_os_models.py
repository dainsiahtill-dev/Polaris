"""Tests for Context OS models - metadata immutability."""

from __future__ import annotations

from polaris.kernelone.context.context_os.models import (
    ArtifactRecord,
    DialogActResult,
    TranscriptEvent,
)


class TestMetadataImmutability:
    """Tests that metadata fields are truly immutable in frozen dataclasses."""

    def test_transcript_event_metadata_is_tuple(self) -> None:
        """TranscriptEvent.metadata should be tuple, not dict."""
        event = TranscriptEvent(
            event_id="e1",
            sequence=1,
            role="user",
            kind="message",
            route="test",
            content="hello",
            _metadata={"key": "value"},
        )

        # metadata should be stored as tuple of tuples
        assert isinstance(event.metadata, tuple)
        assert event.metadata == (("key", "value"),)

    def test_transcript_event_metadata_immutable(self) -> None:
        """TranscriptEvent.metadata should not be modifiable."""
        event = TranscriptEvent(
            event_id="e1",
            sequence=1,
            role="user",
            kind="message",
            route="test",
            content="hello",
            _metadata={"key": "value"},
        )

        # metadata property returns tuple, which is immutable
        assert isinstance(event.metadata, tuple)

    def test_artifact_record_metadata_is_tuple(self) -> None:
        """ArtifactRecord.metadata should be tuple, not dict."""
        record = ArtifactRecord(
            artifact_id="a1",
            artifact_type="code_block",
            mime_type="text/plain",
            token_count=100,
            char_count=500,
            peek="def foo()",
            _metadata={"format": "python"},
        )

        assert isinstance(record.metadata, tuple)
        assert record.metadata == (("format", "python"),)

    def test_artifact_record_metadata_immutable(self) -> None:
        """ArtifactRecord.metadata should not be modifiable."""
        record = ArtifactRecord(
            artifact_id="a1",
            artifact_type="code_block",
            mime_type="text/plain",
            token_count=100,
            char_count=500,
            peek="def foo()",
            _metadata={"format": "python"},
        )

        assert isinstance(record.metadata, tuple)

    def test_dialog_act_result_metadata_is_tuple(self) -> None:
        """DialogActResult.metadata should be tuple, not dict."""
        result = DialogActResult(
            act="affirm",
            confidence=0.95,
            _metadata={"source": "classifier"},
        )

        assert isinstance(result.metadata, tuple)
        assert result.metadata == (("source", "classifier"),)

    def test_dialog_act_result_metadata_immutable(self) -> None:
        """DialogActResult.metadata should not be modifiable."""
        result = DialogActResult(
            act="affirm",
            confidence=0.95,
            _metadata={"source": "classifier"},
        )

        assert isinstance(result.metadata, tuple)

    def test_from_mapping_creates_tuple_metadata(self) -> None:
        """from_mapping should convert dict metadata to tuple."""
        payload = {
            "event_id": "e1",
            "sequence": 1,
            "role": "user",
            "kind": "message",
            "route": "test",
            "content": "hello",
            "metadata": {"key": "value", "other": 123},
        }

        event = TranscriptEvent.from_mapping(payload)

        assert isinstance(event.metadata, tuple)
        assert ("key", "value") in event.metadata
        assert ("other", 123) in event.metadata

    def test_to_dict_returns_dict_metadata(self) -> None:
        """to_dict should convert tuple metadata back to dict for serialization."""
        event = TranscriptEvent(
            event_id="e1",
            sequence=1,
            role="user",
            kind="message",
            route="test",
            content="hello",
            _metadata={"key": "value"},
        )

        result = event.to_dict()

        assert isinstance(result["metadata"], dict)
        assert result["metadata"] == {"key": "value"}

    def test_empty_metadata_default(self) -> None:
        """Empty metadata should default to empty tuple."""
        event = TranscriptEvent(
            event_id="e1",
            sequence=1,
            role="user",
            kind="message",
            route="test",
            content="hello",
        )

        assert event.metadata == ()
        assert isinstance(event.metadata, tuple)

    def test_none_metadata_from_mapping(self) -> None:
        """from_mapping with None metadata should result in empty tuple."""
        payload = {
            "event_id": "e1",
            "sequence": 1,
            "role": "user",
            "kind": "message",
            "route": "test",
            "content": "hello",
            "metadata": None,
        }

        event = TranscriptEvent.from_mapping(payload)

        assert event.metadata == ()

    def test_frozen_dataclass_not_replaceable(self) -> None:
        """Dataclass fields should be replaceable via dataclasses.replace."""
        from dataclasses import replace

        event = TranscriptEvent(
            event_id="e1",
            sequence=1,
            role="user",
            kind="message",
            route="test",
            content="hello",
            _metadata={"key": "value"},
        )

        # This should work - replace creates a new instance
        new_event = replace(event, event_id="e2")
        assert new_event.event_id == "e2"
        assert new_event.metadata == event.metadata
