"""Hardening tests for ContextOS four-layer truth model.

Verifies that no side channels exist between TruthLog, WorkingState,
ReceiptStore, and ProjectionEngine. Tests focus on:

1. Deep-copy isolation for all read APIs (replay, get_entries, query_*, snapshot)
2. TruthLogIndex query methods returning isolated copies (GAP-1: known defect)
3. WorkingState nested-value snapshot independence
4. ProjectionEngine self-mutation isolation from projection output
5. TruthLogService.replace() properly normalizes/deep-copies entries

GAP Summary (2026-04-25):
  GAP-1 (CRITICAL): TruthLogIndex.query_by_role / query_by_event_type /
      query_by_time_range / get_recent return mutable references to the
      internal _entries_by_id dict entries instead of deep copies.
      _index_entry() stores `entry=entry` (reference) at line 149.
      Callers can mutate returned dicts and corrupt both the index and
      the shared _entries list.
  GAP-2 (MEDIUM): runtime._take_snapshot() at line 570 creates a tuple
      wrapping the same dict objects from _entries. The pipeline
      ThreadPoolExecutor could mutate snapshot dicts and corrupt the log.
  GAP-3 (LOW): ProjectionEngine.record_outcome() mutates internal adaptive
      weights. This does not violate inter-layer isolation but means the
      engine is not purely read-only.
"""

from __future__ import annotations

import copy
from typing import Any

from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.context.truth_log_service import TruthLogIndex, TruthLogService
from polaris.kernelone.context.working_state_manager import WorkingStateManager

# ---------------------------------------------------------------------------
# TruthLog replay / get_entries deep-copy hardening
# ---------------------------------------------------------------------------


class TestTruthLogReplayHardening:
    """Verify that replay() and get_entries() return truly independent copies,
    including for deeply nested structures."""

    def test_replay_nested_dict_isolation(self) -> None:
        """Mutating a nested dict inside a replayed entry must not
        corrupt the underlying TruthLog."""
        log = TruthLogService()
        log.append({"turn_id": "t1", "payload": {"inner_key": "original_value", "items": [1, 2, 3]}})

        replayed = log.replay()
        # Mutate nested dict
        replayed[0]["payload"]["inner_key"] = "TAMPERED"
        replayed[0]["payload"]["items"].append(999)

        entries = log.get_entries()
        assert entries[0]["payload"]["inner_key"] == "original_value"
        assert entries[0]["payload"]["items"] == [1, 2, 3]

    def test_get_entries_nested_list_isolation(self) -> None:
        """Mutating a nested list inside get_entries output must not
        corrupt the underlying TruthLog."""
        log = TruthLogService()
        log.append({"turn_id": "t1", "tags": ["a", "b"]})

        entries = log.get_entries()
        # get_entries returns a tuple of dicts -- mutate the nested list
        entries[0]["tags"].append("INJECTED")

        fresh = log.get_entries()
        assert fresh[0]["tags"] == ["a", "b"]

    def test_replay_two_calls_independent(self) -> None:
        """Two successive replay() calls must return independent copies."""
        log = TruthLogService()
        log.append({"turn_id": "t1", "val": "x"})

        r1 = log.replay()
        r2 = log.replay()
        r1[0]["val"] = "MUTATED"

        assert r2[0]["val"] == "x"

    def test_replace_entries_are_deep_copied(self) -> None:
        """TruthLogService.replace() must deep-copy its input so that
        later mutation of the original iterable does not affect the log."""
        entries: list[dict[str, Any]] = [
            {"turn_id": "t1", "nested": {"k": "orig"}},
            {"turn_id": "t2", "nested": {"k": "orig2"}},
        ]
        log = TruthLogService()
        log.replace(entries)

        # Mutate the original list
        entries[0]["nested"]["k"] = "TAMPERED"

        stored = log.get_entries()
        assert stored[0]["nested"]["k"] == "orig"

    def test_replace_clears_previous_entries(self) -> None:
        """replace() must fully overwrite previous entries."""
        log = TruthLogService()
        log.append({"turn_id": "t1"})
        log.append({"turn_id": "t2"})
        assert len(log.get_entries()) == 2

        log.replace([{"turn_id": "t3"}])
        entries = log.get_entries()
        assert len(entries) == 1
        assert entries[0]["turn_id"] == "t3"


# ---------------------------------------------------------------------------
# TruthLogIndex query-method isolation  (GAP-1)
# ---------------------------------------------------------------------------


class TestTruthLogIndexQueryIsolation:
    """GAP-1: TruthLogIndex.query_by_role / query_by_event_type /
    query_by_time_range / get_recent return `e.entry` directly, which is
    a mutable reference to the dict stored inside _entries_by_id.

    These tests assert the CORRECT behaviour (mutation must not leak).
    If the underlying code does not deep-copy on read, these tests will
    FAIL, flagging the side channel for repair.
    """

    @staticmethod
    def _make_index_with_entry(entry: dict[str, Any], entry_id: str = "e1") -> TruthLogIndex:
        """Helper: create a TruthLogIndex and directly populate it."""
        index = TruthLogIndex(enable_vector_search=False)
        # Use the internal sync method to populate without async
        index._index_entry(entry_id, entry)
        return index

    def test_query_by_role_returns_isolated_copy(self) -> None:
        entry = {"role": "user", "content": "hello", "event_type": "message"}
        index = self._make_index_with_entry(entry)

        results = index.query_by_role("user")
        assert len(results) == 1
        results[0]["content"] = "TAMPERED"

        # Internal state must be unaffected
        internal = index._entries_by_id["e1"].entry
        assert internal["content"] == "hello"

    def test_query_by_event_type_returns_isolated_copy(self) -> None:
        entry = {"role": "assistant", "event_type": "tool_call", "content": "read file"}
        index = self._make_index_with_entry(entry)

        results = index.query_by_event_type("tool_call")
        assert len(results) == 1
        results[0]["content"] = "TAMPERED"

        internal = index._entries_by_id["e1"].entry
        assert internal["content"] == "read file"

    def test_get_recent_returns_isolated_copy(self) -> None:
        entry = {"role": "user", "content": "original", "event_type": "msg"}
        index = self._make_index_with_entry(entry)

        results = index.get_recent(10)
        assert len(results) == 1
        results[0]["content"] = "TAMPERED"

        internal = index._entries_by_id["e1"].entry
        assert internal["content"] == "original"

    def test_index_entry_stores_deep_copy(self) -> None:
        """_index_entry must deep-copy the entry dict so that external
        mutation of the original does not corrupt the index."""
        original = {"role": "user", "content": "safe"}
        index = self._make_index_with_entry(original)

        # Mutate the original dict AFTER indexing
        original["content"] = "TAMPERED_EXTERNALLY"

        internal = index._entries_by_id["e1"].entry
        assert internal["content"] == "safe"


# ---------------------------------------------------------------------------
# WorkingState deep isolation
# ---------------------------------------------------------------------------


class TestWorkingStateDeepIsolation:
    """Verify that WorkingStateManager snapshots and get() return
    truly independent deep copies, even for nested mutable structures."""

    def test_snapshot_nested_dict_isolation(self) -> None:
        """Mutating a nested dict inside a snapshot must not
        affect internal state."""
        mgr = WorkingStateManager()
        mgr.update("config", {"db": {"host": "localhost", "port": 5432}})

        snap = mgr.snapshot()
        snap["config"]["db"]["host"] = "TAMPERED"

        assert mgr.get("config")["db"]["host"] == "localhost"

    def test_snapshot_nested_list_isolation(self) -> None:
        """Mutating a nested list inside a snapshot must not
        affect internal state."""
        mgr = WorkingStateManager()
        mgr.update("items", [{"id": 1}, {"id": 2}])

        snap = mgr.snapshot()
        snap["items"].append({"id": 999})
        snap["items"][0]["id"] = -1

        stored = mgr.get("items")
        assert len(stored) == 2
        assert stored[0]["id"] == 1

    def test_get_returns_independent_copy(self) -> None:
        """Two consecutive get() calls must return independent objects."""
        mgr = WorkingStateManager()
        mgr.update("data", {"nested": [1, 2, 3]})

        v1 = mgr.get("data")
        v2 = mgr.get("data")
        v1["nested"].append(99)

        assert v2["nested"] == [1, 2, 3]

    def test_update_deep_copies_value(self) -> None:
        """update() must deep-copy the value so that later mutation of
        the original does not affect internal state."""
        mgr = WorkingStateManager()
        value: dict[str, Any] = {"level1": {"level2": "original"}}
        mgr.update("key", value)

        value["level1"]["level2"] = "TAMPERED"

        assert mgr.get("key")["level1"]["level2"] == "original"

    def test_current_returns_independent_working_state(self) -> None:
        """Two calls to current() must return independent WorkingState instances."""
        mgr = WorkingStateManager()
        ws1 = mgr.current()
        ws2 = mgr.current()

        assert ws1 is not ws2


# ---------------------------------------------------------------------------
# ProjectionEngine side-effect isolation
# ---------------------------------------------------------------------------


class TestProjectionEngineNoSideEffects:
    """Verify that ProjectionEngine write methods (record_outcome,
    adaptive weights) do not leak into projection output, and that
    projection methods do not mutate source data."""

    def test_record_outcome_does_not_change_projection_output(self) -> None:
        """Calling record_outcome() must not alter the messages produced
        by project() for the same input."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        payload = {
            "system_hint": "You are helpful.",
            "turns": [{"role": "user", "content": "hello"}],
        }

        messages_before = engine.project(copy.deepcopy(payload), receipt_store)

        # Record many outcomes to shift weights
        for _ in range(20):
            engine.record_outcome(success=True, tokens_used=100)
        for _ in range(20):
            engine.record_outcome(success=False, tokens_used=200)

        messages_after = engine.project(copy.deepcopy(payload), receipt_store)

        # The message content must be identical; only internal weights change
        assert len(messages_before) == len(messages_after)
        for m_before, m_after in zip(messages_before, messages_after, strict=True):
            assert m_before["role"] == m_after["role"]
            assert m_before["content"] == m_after["content"]

    def test_project_returns_new_list_each_call(self) -> None:
        """project() must return a fresh list on each invocation."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        payload = {
            "system_hint": "sys",
            "turns": [{"role": "user", "content": "go"}],
        }

        m1 = engine.project(copy.deepcopy(payload), receipt_store)
        m2 = engine.project(copy.deepcopy(payload), receipt_store)

        assert m1 is not m2
        # And individual message dicts are independent
        if m1 and m2:
            assert m1[0] is not m2[0]

    def test_build_turns_does_not_mutate_events(self) -> None:
        """build_turns must not mutate the event objects it receives."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        class FakeEvent:
            def __init__(self, seq: int, role: str, content: str) -> None:
                self.sequence = seq
                self.route = "patch"
                self.role = role
                self.content = content
                self.event_id = f"evt_{seq}"
                self.metadata = ()
                self.artifact_id = ""

        events = [FakeEvent(1, "user", "original_content")]
        original_content = events[0].content

        engine.build_turns(events, receipt_store)

        assert events[0].content == original_content

    def test_build_payload_returns_independent_dict(self) -> None:
        """build_payload must not leak internal state into the returned dict."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        class FakeEvent:
            def __init__(self) -> None:
                self.sequence = 1
                self.route = "patch"
                self.role = "user"
                self.content = "test"
                self.event_id = "e1"
                self.metadata = ()
                self.artifact_id = ""

        payload = engine.build_payload(
            active_window=[FakeEvent()],
            receipt_store=receipt_store,
            head_anchor="system",
            tail_anchor="tail",
        )

        # Mutating the payload should not affect a second call
        payload["turns"].append({"role": "injected", "content": "bad"})

        payload2 = engine.build_payload(
            active_window=[FakeEvent()],
            receipt_store=receipt_store,
            head_anchor="system",
            tail_anchor="tail",
        )

        assert not any(t.get("role") == "injected" for t in payload2["turns"])

    def test_projection_engine_has_no_truth_log_write_method(self) -> None:
        """ProjectionEngine must not expose any method that writes to
        TruthLog or WorkingState."""
        engine = ProjectionEngine()

        # These method names would indicate a layer-crossing write
        forbidden_methods = [
            "append_to_truth_log",
            "write_truth_log",
            "update_working_state",
            "set_working_state",
            "mutate_truth_log",
        ]
        for method_name in forbidden_methods:
            assert not hasattr(engine, method_name), f"ProjectionEngine must not have write method: {method_name}"


# ---------------------------------------------------------------------------
# ReceiptStore isolation hardening
# ---------------------------------------------------------------------------


class TestReceiptStoreHardening:
    """Additional isolation checks for ReceiptStore."""

    def test_put_and_get_returns_same_content(self) -> None:
        """Basic round-trip: put then get must return identical content."""
        store = ReceiptStore()
        store.put("r1", "test content")
        assert store.get("r1") == "test content"

    def test_separate_stores_fully_isolated(self) -> None:
        """Two ReceiptStore instances must not share state."""
        s1 = ReceiptStore()
        s2 = ReceiptStore()

        s1.put("shared_id", "from_s1")
        s2.put("shared_id", "from_s2")

        assert s1.get("shared_id") == "from_s1"
        assert s2.get("shared_id") == "from_s2"

    def test_export_import_round_trip(self) -> None:
        """export_receipts / import_receipts must produce a faithful copy."""
        s1 = ReceiptStore()
        s1.put("r1", "content_1")
        s1.put("r2", "content_2")

        exported = s1.export_receipts()

        s2 = ReceiptStore()
        s2.import_receipts(exported)

        assert s2.get("r1") == "content_1"
        assert s2.get("r2") == "content_2"

    def test_offload_below_threshold_returns_original(self) -> None:
        """Content below the threshold must not be offloaded."""
        store = ReceiptStore()
        content, refs = store.offload_content("r1", "short", threshold=100, placeholder="[stub]")

        assert content == "short"
        assert refs == ()

    def test_offload_above_threshold_stores_and_returns_placeholder(self) -> None:
        """Content above the threshold must be stored and replaced with placeholder."""
        store = ReceiptStore()
        long_content = "x" * 200
        content, refs = store.offload_content("r1", long_content, threshold=50, placeholder="[stored]")

        assert content == "[stored]"
        assert refs == ("r1",)
        assert store.get("r1") == long_content


# ---------------------------------------------------------------------------
# Cross-layer contamination regression
# ---------------------------------------------------------------------------


class TestCrossLayerContamination:
    """Regression tests ensuring no accidental cross-layer state sharing."""

    def test_truth_log_and_working_state_share_no_references(self) -> None:
        """Storing the same dict in both TruthLog and WorkingState must not
        create shared mutable references between layers."""
        shared_dict: dict[str, Any] = {"key": "value", "nested": {"a": 1}}

        log = TruthLogService()
        mgr = WorkingStateManager()

        log.append(shared_dict)
        mgr.update("entry", shared_dict)

        # Mutate the original
        shared_dict["key"] = "TAMPERED"
        shared_dict["nested"]["a"] = 999

        # Both layers must be unaffected
        log_entry = log.get_entries()[0]
        ws_entry = mgr.get("entry")

        assert log_entry["key"] == "value"
        assert log_entry["nested"]["a"] == 1
        assert ws_entry["key"] == "value"
        assert ws_entry["nested"]["a"] == 1

    def test_truth_log_entries_and_receipt_store_isolated(self) -> None:
        """TruthLog entries with receipt references must not share
        mutable state with ReceiptStore internals."""
        log = TruthLogService()
        store = ReceiptStore()

        content = "large receipt content"
        receipt_hash = store.put("r1", content)

        log.append({"turn_id": "t1", "receipt_ref": receipt_hash, "content": "stub"})

        # Mutating the log entry must not affect the receipt store
        replayed = log.replay()
        replayed[0]["receipt_ref"] = "TAMPERED_HASH"

        assert store.get("r1") == content

    def test_projection_engine_does_not_hold_truth_log_reference(self) -> None:
        """ProjectionEngine must not retain references to TruthLog data
        between calls."""
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()

        payload1 = {
            "system_hint": "sys",
            "turns": [{"role": "user", "content": "first call"}],
        }
        messages1 = engine.project(payload1, receipt_store)

        payload2 = {
            "system_hint": "sys",
            "turns": [{"role": "user", "content": "second call"}],
        }
        messages2 = engine.project(payload2, receipt_store)

        # First call results must not be contaminated by second call
        user_msgs_1 = [m for m in messages1 if m["role"] == "user"]
        user_msgs_2 = [m for m in messages2 if m["role"] == "user"]

        assert user_msgs_1[0]["content"] == "first call"
        assert user_msgs_2[0]["content"] == "second call"
