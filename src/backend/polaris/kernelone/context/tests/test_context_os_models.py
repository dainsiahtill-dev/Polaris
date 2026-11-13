"""Tests for Context OS models - metadata immutability."""

from __future__ import annotations

from polaris.kernelone.context.context_os.models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    DialogActResultV2 as DialogActResult,
    TranscriptEventV2 as TranscriptEvent,
)


class TestMetadataImmutability:
    """Tests that metadata fields are truly immutable in frozen dataclasses."""

    def test_transcript_event_metadata_is_tuple(self) -> None:
        """TranscriptEvent.metadata should be tuple, not dict."""
        # model_validate exercises the before-validator that lifts a mapping
        # into the declared tuple-of-tuples form.
        event = TranscriptEvent.model_validate(
            {
                "event_id": "e1",
                "sequence": 1,
                "role": "user",
                "kind": "message",
                "route": "test",
                "content": "hello",
                "metadata": {"key": "value"},
            }
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
            metadata=(("key", "value"),),
        )

        # metadata property returns tuple, which is immutable
        assert isinstance(event.metadata, tuple)

    def test_artifact_record_metadata_is_tuple(self) -> None:
        """ArtifactRecord.metadata should be tuple, not dict."""
        record = ArtifactRecord.model_validate(
            {
                "artifact_id": "a1",
                "artifact_type": "code_block",
                "mime_type": "text/plain",
                "token_count": 100,
                "char_count": 500,
                "peek": "def foo()",
                "metadata": {"format": "python"},
            }
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
            metadata=(("format", "python"),),
        )

        assert isinstance(record.metadata, tuple)

    def test_dialog_act_result_metadata_is_tuple(self) -> None:
        """DialogActResult.metadata should be tuple, not dict."""
        result = DialogActResult.model_validate(
            {
                "act": "affirm",
                "confidence": 0.95,
                "metadata": {"source": "classifier"},
            }
        )

        assert isinstance(result.metadata, tuple)
        assert result.metadata == (("source", "classifier"),)

    def test_dialog_act_result_metadata_immutable(self) -> None:
        """DialogActResult.metadata should not be modifiable."""
        result = DialogActResult(
            act="affirm",
            confidence=0.95,
            metadata=(("source", "classifier"),),
        )

        assert isinstance(result.metadata, tuple)

    def test_from_mapping_creates_tuple_metadata(self) -> None:
        """model_validate should convert dict metadata to tuple."""
        payload = {
            "event_id": "e1",
            "sequence": 1,
            "role": "user",
            "kind": "message",
            "route": "test",
            "content": "hello",
            "metadata": {"key": "value", "other": 123},
        }

        event = TranscriptEvent.model_validate(payload)

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
            metadata=(("key", "value"),),
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
        """model_validate with None metadata should result in empty tuple."""
        payload = {
            "event_id": "e1",
            "sequence": 1,
            "role": "user",
            "kind": "message",
            "route": "test",
            "content": "hello",
            "metadata": None,
        }

        event = TranscriptEvent.model_validate(payload)

        assert event.metadata == ()

    def test_frozen_model_copies_via_model_copy(self) -> None:
        """A frozen Pydantic model yields a new instance via model_copy."""
        event = TranscriptEvent(
            event_id="e1",
            sequence=1,
            role="user",
            kind="message",
            route="test",
            content="hello",
            metadata=(("key", "value"),),
        )

        # model_copy creates a new instance with the updated field
        new_event = event.model_copy(update={"event_id": "e2"})
        assert new_event.event_id == "e2"
        assert new_event.metadata == event.metadata
