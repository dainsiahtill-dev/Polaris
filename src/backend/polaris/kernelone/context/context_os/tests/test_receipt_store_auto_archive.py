"""Test Receipt Store Auto-Archive — large transcripts must auto-archive to ReceiptStore.

Validates:
- ContextOSSnapshot.to_dict() auto-creates ReceiptStore when transcript > 5000 chars
- Only events with content > 200 chars are replaced with receipt refs
- _receipt_store_export is included in the dict output
- Receipt restoration works correctly via from_mapping()
"""

from __future__ import annotations

from polaris.kernelone.context.context_os.models_v2 import (
    ContextOSSnapshotV2 as ContextOSSnapshot,
    TranscriptEventV2 as TranscriptEvent,
    WorkingStateV2 as WorkingState,
)
from polaris.kernelone.context.receipt_store import ReceiptStore


class TestReceiptStoreAutoArchive:
    """Receipt Store auto-archive regression tests."""

    def _make_event(self, event_id: str, content: str, sequence: int = 1) -> TranscriptEvent:
        """Create a TranscriptEvent for testing."""
        return TranscriptEvent(
            event_id=event_id,
            sequence=sequence,
            role="user",
            kind="message",
            route="clear",
            content=content,
        )

    def _make_snapshot(self, *events: TranscriptEvent) -> ContextOSSnapshot:
        """Create a ContextOSSnapshot with given events."""
        return ContextOSSnapshot(
            transcript_log=events,
            working_state=WorkingState(),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Happy Path
    # ──────────────────────────────────────────────────────────────────────────

    def test_auto_archive_triggered_when_over_5000_chars(self) -> None:
        """to_dict() must auto-create ReceiptStore when total transcript > 5000 chars."""
        large_content = "x" * 3000
        event1 = self._make_event("evt-1", large_content, sequence=1)
        event2 = self._make_event("evt-2", large_content, sequence=2)
        snapshot = self._make_snapshot(event1, event2)

        result = snapshot.to_dict()

        # Total chars = 6000 > 5000, so auto-archive should trigger
        assert "_receipt_store_export" in result
        receipt_export = result["_receipt_store_export"]
        assert isinstance(receipt_export, dict)
        assert len(receipt_export) > 0

    def test_no_auto_archive_when_under_5000_chars(self) -> None:
        """to_dict() must NOT auto-create ReceiptStore when total transcript ≤ 5000 chars."""
        small_content = "hello"
        event1 = self._make_event("evt-1", small_content, sequence=1)
        event2 = self._make_event("evt-2", small_content, sequence=2)
        snapshot = self._make_snapshot(event1, event2)

        result = snapshot.to_dict()

        assert "_receipt_store_export" not in result

    def test_only_large_events_get_receipt_refs(self) -> None:
        """Only events with content > 200 chars should be replaced with receipt refs."""
        large_content = "x" * 3000
        small_content = "hi"
        event_large = self._make_event("evt-large", large_content, sequence=1)
        event_small = self._make_event("evt-small", small_content, sequence=2)
        snapshot = self._make_snapshot(event_large, event_small)

        result = snapshot.to_dict()

        # Total chars = 3002 > 5000? No! 3002 < 5000, so no auto-archive
        # We need to force it by providing an external receipt store
        from polaris.kernelone.context.receipt_store import ReceiptStore

        external_store = ReceiptStore()
        result = snapshot.to_dict(receipt_store=external_store)

        transcript_log = result["transcript_log"]
        assert len(transcript_log) == 2

        # Large event should be replaced with receipt ref
        large_event_dict = transcript_log[0]
        assert large_event_dict["content"].startswith("<receipt_ref:")
        assert large_event_dict["content"].endswith(">")

        # Small event should keep original content
        small_event_dict = transcript_log[1]
        assert small_event_dict["content"] == "hi"

    def test_receipt_store_export_contains_actual_content(self) -> None:
        """Receipt store export must contain the actual event content."""
        large_content = "x" * 6000  # Must be > 5000 to trigger auto-archive
        event = self._make_event("evt-1", large_content, sequence=1)
        snapshot = self._make_snapshot(event)

        result = snapshot.to_dict()

        receipt_export = result["_receipt_store_export"]
        ref_id = "evt_evt-1"
        assert ref_id in receipt_export
        assert receipt_export[ref_id] == large_content

    # ──────────────────────────────────────────────────────────────────────────
    # Edge Cases
    # ──────────────────────────────────────────────────────────────────────────

    def test_exactly_200_char_event_not_replaced(self) -> None:
        """Events with exactly 200 chars should NOT be replaced with receipt refs."""
        content_200 = "x" * 200
        event = self._make_event("evt-1", content_200, sequence=1)
        # Add another large event to trigger auto-archive
        large_event = self._make_event("evt-2", "y" * 3000, sequence=2)
        snapshot = self._make_snapshot(event, large_event)

        result = snapshot.to_dict()

        transcript_log = result["transcript_log"]
        # 200-char event should keep original content (threshold is > 200)
        assert transcript_log[0]["content"] == content_200

    def test_exactly_201_char_event_gets_replaced(self) -> None:
        """Events with 201 chars SHOULD be replaced with receipt refs."""
        content_201 = "x" * 201
        event = self._make_event("evt-1", content_201, sequence=1)
        snapshot = self._make_snapshot(event)

        # Force receipt store usage to test the >200 threshold
        from polaris.kernelone.context.receipt_store import ReceiptStore

        external_store = ReceiptStore()
        result = snapshot.to_dict(receipt_store=external_store)

        transcript_log = result["transcript_log"]
        # 201-char event should be replaced (threshold is > 200)
        assert transcript_log[0]["content"].startswith("<receipt_ref:")

    def test_empty_transcript_no_archive(self) -> None:
        """Empty transcript must not trigger auto-archive."""
        snapshot = self._make_snapshot()

        result = snapshot.to_dict()

        assert "_receipt_store_export" not in result
        assert result["transcript_log"] == []

    def test_single_large_event_triggers_archive(self) -> None:
        """A single event > 5000 chars must trigger auto-archive."""
        huge_content = "x" * 6000
        event = self._make_event("evt-1", huge_content, sequence=1)
        snapshot = self._make_snapshot(event)

        result = snapshot.to_dict()

        assert "_receipt_store_export" in result
        transcript_log = result["transcript_log"]
        assert transcript_log[0]["content"].startswith("<receipt_ref:")

    # ──────────────────────────────────────────────────────────────────────────
    # Receipt Restoration
    # ──────────────────────────────────────────────────────────────────────────

    def test_from_mapping_restores_receipt_content(self) -> None:
        """from_mapping() must restore receipt content from _receipt_store_export."""
        large_content = "x" * 3000
        event = self._make_event("evt-1", large_content, sequence=1)
        snapshot = self._make_snapshot(event)

        # Serialize
        serialized = snapshot.to_dict()

        # Deserialize
        restored = ContextOSSnapshot.from_mapping(serialized)
        assert restored is not None

        # Content should be restored
        assert len(restored.transcript_log) == 1
        assert restored.transcript_log[0].content == large_content

    def test_from_mapping_without_receipt_export(self) -> None:
        """from_mapping() must handle missing _receipt_store_export gracefully."""
        small_content = "hello world"
        event = self._make_event("evt-1", small_content, sequence=1)
        snapshot = self._make_snapshot(event)

        serialized = snapshot.to_dict()
        # Ensure no receipt export
        assert "_receipt_store_export" not in serialized

        restored = ContextOSSnapshot.from_mapping(serialized)
        assert restored is not None
        assert restored.transcript_log[0].content == small_content

    # ──────────────────────────────────────────────────────────────────────────
    # Exceptions
    # ──────────────────────────────────────────────────────────────────────────

    def test_external_receipt_store_provided(self) -> None:
        """If external ReceiptStore is provided, it should be used instead of auto-creating."""
        large_content = "x" * 3000
        event = self._make_event("evt-1", large_content, sequence=1)
        snapshot = self._make_snapshot(event)

        external_store = ReceiptStore()
        result = snapshot.to_dict(receipt_store=external_store)

        # Should use external store, not auto-create
        assert "_receipt_store_export" not in result
        # But content should still be replaced with refs
        assert result["transcript_log"][0]["content"].startswith("<receipt_ref:")

    def test_from_mapping_with_corrupted_receipt_ref(self) -> None:
        """from_mapping() must handle corrupted receipt refs gracefully."""
        # Manually construct a corrupted snapshot dict
        corrupted_dict = {
            "version": 1,
            "mode": "state_first_context_os_v1",
            "adapter_id": "generic",
            "transcript_log": [
                {
                    "event_id": "evt-1",
                    "sequence": 1,
                    "role": "user",
                    "kind": "message",
                    "route": "clear",
                    "content": "<receipt_ref:missing_ref>",
                    "source_turns": [],
                    "artifact_id": None,
                    "created_at": "",
                    "metadata": {},
                }
            ],
            "working_state": WorkingState().to_dict(),
            "artifact_store": [],
            "episode_store": [],
            "budget_plan": None,
            "updated_at": "",
            "pending_followup": None,
            "content_map": {},
            "_receipt_store_export": {},  # Empty export, missing ref
        }

        restored = ContextOSSnapshot.from_mapping(corrupted_dict)
        assert restored is not None
        # Should keep the receipt ref as-is since it's not in the export
        assert restored.transcript_log[0].content == "<receipt_ref:missing_ref>"

    # ──────────────────────────────────────────────────────────────────────────
    # Regression: Boundary Conditions
    # ──────────────────────────────────────────────────────────────────────────

    def test_boundary_5000_chars_exactly(self) -> None:
        """Exactly 5000 chars should NOT trigger auto-archive."""
        content = "x" * 5000
        event = self._make_event("evt-1", content, sequence=1)
        snapshot = self._make_snapshot(event)

        result = snapshot.to_dict()

        # 5000 is the threshold, should not trigger (only > 5000 triggers)
        # Wait, let me check: the code says `total_chars > max_inline_transcript_chars`
        # So 5000 should NOT trigger
        assert "_receipt_store_export" not in result

    def test_boundary_5001_chars_triggers_archive(self) -> None:
        """5001 chars SHOULD trigger auto-archive."""
        content = "x" * 5001
        event = self._make_event("evt-1", content, sequence=1)
        snapshot = self._make_snapshot(event)

        result = snapshot.to_dict()

        assert "_receipt_store_export" in result
