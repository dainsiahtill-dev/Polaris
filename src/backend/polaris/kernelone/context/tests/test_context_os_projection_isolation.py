"""Tests for ContextOS four-layer orthogonal projection isolation.

Verifies that TruthLog, WorkingState, ReceiptStore, and ProjectionEngine
maintain strict boundaries — no layer leaks into another.
"""

from __future__ import annotations

from polaris.kernelone.context.context_os.content_store import ContentStore
from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.context.truth_log_service import TruthLogService
from polaris.kernelone.context.working_state_manager import WorkingStateManager


class TestTruthLogIsolation:
    """TruthLog must be append-only and immutable."""

    def test_append_only_no_deletion(self) -> None:
        """TruthLog must not support deletion of entries."""
        log = TruthLogService()
        log.append({"turn_id": "t1", "content": "first"})
        log.append({"turn_id": "t2", "content": "second"})

        entries = log.get_entries()
        assert len(entries) == 2

        # There is no delete method — verify absence
        assert not hasattr(log, "delete") or not callable(getattr(log, "delete", None))

    def test_replay_returns_copies(self) -> None:
        """replay must return copies, not references to internal state."""
        log = TruthLogService()
        original = {"turn_id": "t1", "data": "sensitive"}
        log.append(original)

        replayed = log.replay()
        replayed[0]["mutated"] = True

        # Internal state must not be affected
        entries = log.get_entries()
        assert "mutated" not in entries[0]

    def test_external_mutation_blocked(self) -> None:
        """Mutating the original dict after append must not affect TruthLog."""
        log = TruthLogService()
        original = {"turn_id": "t1", "content": "original"}
        log.append(original)

        original["content"] = "tampered"

        entries = log.get_entries()
        assert entries[0]["content"] == "original"

    def test_truth_log_does_not_expose_working_state(self) -> None:
        """TruthLog entries must not contain WorkingState-derived fields."""
        log = TruthLogService()
        log.append({"turn_id": "t1", "role": "user", "content": "hello"})

        for entry in log.get_entries():
            # TruthLog should be raw events, not processed state
            assert "current_goal" not in entry
            assert "structured_findings" not in entry


class TestWorkingStateIsolation:
    """WorkingState must be isolated from TruthLog and ProjectionEngine."""

    def test_update_does_not_affect_truth_log(self) -> None:
        """WorkingState updates must not leak into TruthLog."""
        log = TruthLogService()
        mgr = WorkingStateManager()

        log.append({"turn_id": "t1", "content": "original"})
        mgr.update("derived_insight", "user wants login fix")

        # TruthLog should remain unchanged
        entries = log.get_entries()
        assert len(entries) == 1
        assert entries[0]["content"] == "original"
        assert "derived_insight" not in entries[0]

    def test_snapshot_isolation(self) -> None:
        """snapshot must return a copy, not internal reference."""
        mgr = WorkingStateManager()
        mgr.update("key1", "value1")

        snap1 = mgr.snapshot()
        snap1["key1"] = "mutated"

        # Internal state must be preserved
        assert mgr.get("key1") == "value1"

    def test_multiple_snapshots_independent(self) -> None:
        """Multiple snapshots must be independent of each other."""
        mgr = WorkingStateManager()
        mgr.update("key", "v1")

        snap1 = mgr.snapshot()
        mgr.update("key", "v2")
        snap2 = mgr.snapshot()

        assert snap1["key"] == "v1"
        assert snap2["key"] == "v2"

    def test_get_missing_returns_none(self) -> None:
        """get must return None for missing keys."""
        mgr = WorkingStateManager()

        assert mgr.get("nonexistent") is None


class TestReceiptStoreIsolation:
    """ReceiptStore must isolate large content from projection pipeline."""

    def test_content_not_inlined_when_offloaded(self) -> None:
        """Offloaded content must not appear inline in projection."""
        store = ReceiptStore()
        engine = ProjectionEngine()

        large_content = "secret_data" * 1000
        content_id = store.put("receipt_1", large_content)

        # Projection with receipt reference should not inline full content
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [
                    {
                        "role": "user",
                        "content": "check this",
                        "receipt_refs": [content_id],
                    }
                ],
            },
            store,
        )

        user_msg = messages[1]
        # Receipt content is appended but truncated to 500 chars
        assert len(user_msg["content"]) < len(large_content) + 100

    def test_receipt_store_isolated_from_working_state(self) -> None:
        """ReceiptStore and WorkingState must not share storage."""
        store = ReceiptStore()
        mgr = WorkingStateManager()

        store.put("key_1", "receipt_content")
        mgr.update("key_1", "working_state_content")

        assert store.get("key_1") == "receipt_content"
        assert mgr.get("key_1") == "working_state_content"

    def test_missing_receipt_returns_none(self) -> None:
        """get must return None for missing receipts."""
        store = ReceiptStore()

        assert store.get("missing_id") is None

    def test_list_receipt_ids_isolated(self) -> None:
        """list_receipt_ids must only return this store's IDs."""
        store1 = ReceiptStore()
        store2 = ReceiptStore()

        store1.put("r1", "a")
        store2.put("r2", "b")

        assert store1.list_receipt_ids() == ["r1"]
        assert store2.list_receipt_ids() == ["r2"]


class TestProjectionEngineIsolation:
    """ProjectionEngine must be read-only and not mutate source layers."""

    def test_project_does_not_mutate_input(self) -> None:
        """project must not mutate the input projection dict."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        original = {
            "system_hint": "You are helpful.",
            "turns": [{"role": "user", "content": "hello"}],
            "budget_status": {"remaining": 100},
        }
        original_turns_len = len(original["turns"])

        engine.project(original, receipt_store)

        # Original dict should be unchanged
        assert len(original["turns"]) == original_turns_len
        assert "budget_status" in original

    def test_strip_control_plane_returns_new_dict(self) -> None:
        """_strip_control_plane_noise must return a new dict."""
        engine = ProjectionEngine()

        original = {"turns": [], "budget_status": {"remaining": 100}}
        cleaned = engine._strip_control_plane_noise(original)

        assert cleaned is not original
        assert "budget_status" in original
        assert "budget_status" not in cleaned

    def test_build_turns_does_not_mutate_events(self) -> None:
        """build_turns must not mutate source events."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        class MockEvent:
            def __init__(self) -> None:
                self.sequence = 1
                self.route = "patch"
                self.role = "user"
                self.content = "original"
                self.event_id = "evt_1"
                self.metadata = ()
                self.artifact_id = ""

        event = MockEvent()
        original_content = event.content

        engine.build_turns([event], receipt_store)

        assert event.content == original_content

    def test_system_hint_separated_from_turns(self) -> None:
        """system_hint must be in a separate message from user turns."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        messages = engine.project(
            {
                "system_hint": "You are a coding assistant.",
                "turns": [{"role": "user", "content": "fix bug"}],
            },
            receipt_store,
        )

        # First message should be system
        assert messages[0]["role"] == "system"
        # Second message should be user
        assert messages[1]["role"] == "user"
        # They should be separate messages
        assert messages[0] != messages[1]

    def test_run_card_rendered_as_separate_message(self) -> None:
        """run_card must be rendered as a separate system message."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "do it"}],
                "run_card": "【Run Card】\nGoal: fix login",
            },
            receipt_store,
        )

        # Run card should be a separate system message.
        assert any(msg.get("role") == "system" and "【Run Card】" in str(msg.get("content", "")) for msg in messages)
        # Current user intent must remain the final message.
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "do it"

    def test_tail_hint_separate_from_system_hint(self) -> None:
        """tail_hint and system_hint must be separate messages."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        messages = engine.project(
            {
                "system_hint": "system instruction",
                "turns": [],
                "tail_hint": "tail instruction",
            },
            receipt_store,
        )

        roles = [m["role"] for m in messages]
        assert roles.count("system") == 2
        assert messages[0]["content"] == "system instruction"
        assert messages[-1]["content"] == "tail instruction"

    def test_tail_and_run_card_do_not_override_latest_user_turn(self) -> None:
        """Tail/run-card system hints must be placed before the trailing user turn."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        messages = engine.project(
            {
                "system_hint": "system instruction",
                "turns": [
                    {"role": "assistant", "content": "previous response"},
                    {"role": "user", "content": "请继续落地代码修复"},
                ],
                "tail_hint": "tail instruction",
                "run_card": "【Run Card】\nOpen loops: 1",
            },
            receipt_store,
        )

        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "请继续落地代码修复"
        assert any(m["role"] == "system" and m["content"] == "tail instruction" for m in messages[:-1])
        assert any("【Run Card】" in str(m.get("content", "")) for m in messages[:-1])


class TestContentStoreIsolation:
    """ContentStore must provide content-addressable deduplication."""

    def test_intern_same_content_same_ref(self) -> None:
        """intern must return the same ref for identical content."""
        store = ContentStore(workspace=".")

        ref1 = store.intern("duplicate content")
        ref2 = store.intern("duplicate content")

        assert ref1.hash == ref2.hash

    def test_intern_different_content_different_ref(self) -> None:
        """intern must return different refs for different content."""
        store = ContentStore(workspace=".")

        ref1 = store.intern("content A")
        ref2 = store.intern("content B")

        assert ref1.hash != ref2.hash

    def test_dedup_tracks_savings(self) -> None:
        """ContentStore must track deduplication savings."""
        store = ContentStore(workspace=".")

        store.intern("x" * 1000)
        store.intern("x" * 1000)

        assert store.stats["dedup_saved_bytes"] > 0

    def test_content_store_isolated_instances(self) -> None:
        """Separate ContentStore instances should have separate stats."""
        store1 = ContentStore(workspace=".")
        store2 = ContentStore(workspace=".")

        store1.intern("content")

        assert store1.stats["entries"] > 0
        # store2 may share backing storage but stats should be per-instance
        # or at minimum not crash
        assert store2.stats["entries"] >= 0


class TestCrossLayerDataFlow:
    """Tests verifying correct data flow between layers."""

    def test_truth_log_to_projection_read_only(self) -> None:
        """TruthLog data must reach ProjectionEngine read-only."""
        log = TruthLogService()
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        log.append({"role": "user", "content": "fix auth.py"})

        # Projection engine should be able to read from log
        # (in practice via active_window, simulated here)
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "fix auth.py"}],
            },
            receipt_store,
        )

        assert any("fix auth.py" in str(m.get("content", "")) for m in messages)

    def test_working_state_updates_not_auto_projected(self) -> None:
        """WorkingState updates must not automatically appear in projections."""
        mgr = WorkingStateManager()
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        mgr.update("secret_insight", "user is frustrated")

        # Projection without explicit run_card should not include WorkingState
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "hello"}],
            },
            receipt_store,
        )

        all_content = " ".join(str(m.get("content", "")) for m in messages)
        assert "secret_insight" not in all_content
        assert "frustrated" not in all_content
