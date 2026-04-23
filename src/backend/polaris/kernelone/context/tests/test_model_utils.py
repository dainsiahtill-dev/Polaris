"""Tests for validated_replace helper."""

from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os.model_utils import validated_replace
from polaris.kernelone.context.context_os.models_v2 import TranscriptEventV2


class TestValidatedReplace:
    def test_happy_path_replaces_content(self) -> None:
        event = TranscriptEventV2(
            event_id="evt_001",
            sequence=1,
            role="user",
            content="hello",
            metadata=(),
        )
        updated = validated_replace(event, content="world")
        assert updated.content == "world"
        assert updated.event_id == "evt_001"
        assert updated.sequence == 1

    def test_invalid_field_name_raises(self) -> None:
        event = TranscriptEventV2(
            event_id="evt_001",
            sequence=1,
            role="user",
            content="hello",
            metadata=(),
        )
        with pytest.raises(ValueError, match="Invalid field name"):
            validated_replace(event, _metadata={"key": "value"})

    def test_metadata_dict_converted_to_tuple(self) -> None:
        event = TranscriptEventV2(
            event_id="evt_001",
            sequence=1,
            role="user",
            content="hello",
            metadata=(),
        )
        updated = validated_replace(
            event,
            metadata={"jit_compressed": True, "compression_strategy": "tiered_slm"},
        )
        assert updated.metadata == (
            ("compression_strategy", "tiered_slm"),
            ("jit_compressed", True),
        )

    def test_jit_compressed_regression(self) -> None:
        event = TranscriptEventV2(
            event_id="evt_001",
            sequence=1,
            role="user",
            content="hello",
            metadata=(),
        )
        updated = validated_replace(
            event,
            content="compressed",
            metadata={"jit_compressed": True, "compression_strategy": "tiered_slm"},
        )
        assert updated.content == "compressed"
        assert dict(updated.metadata)["jit_compressed"] is True
        assert dict(updated.metadata)["compression_strategy"] == "tiered_slm"
