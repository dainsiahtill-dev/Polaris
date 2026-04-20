"""Tests for ContextOS four-layer split components (TruthLog, WorkingState, ReceiptStore, ProjectionEngine)."""

from __future__ import annotations

from polaris.kernelone.context.context_os.content_store import ContentStore
from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.context.truth_log_service import TruthLogService
from polaris.kernelone.context.working_state_manager import WorkingStateManager


class TestTruthLogService:
    def test_append_and_replay(self) -> None:
        log = TruthLogService()
        log.append({"turn_id": "t1", "role": "user", "content": "hello"})
        log.append({"turn_id": "t2", "role": "assistant", "content": "hi"})

        entries = log.get_entries()
        assert len(entries) == 2
        assert entries[0]["turn_id"] == "t1"

        replayed = log.replay()
        assert len(replayed) == 2
        replayed[0]["mutated"] = True
        assert "mutated" not in entries[0]

    def test_immutability(self) -> None:
        log = TruthLogService()
        original = {"turn_id": "t1"}
        log.append(original)
        original["turn_id"] = "t2"
        assert log.get_entries()[0]["turn_id"] == "t1"


class TestWorkingStateManager:
    def test_update_and_get(self) -> None:
        mgr = WorkingStateManager(workspace=".")
        mgr.update("active_file", "main.py")
        assert mgr.get("active_file") == "main.py"
        assert mgr.get("missing") is None

    def test_snapshot_isolation(self) -> None:
        mgr = WorkingStateManager()
        mgr.update("key", "value")
        snap = mgr.snapshot()
        snap["key"] = "mutated"
        assert mgr.get("key") == "value"


class TestReceiptStore:
    def test_put_and_get_round_trip(self) -> None:
        store = ReceiptStore(workspace=".")
        content = "large tool output content"
        ref_hash = store.put("receipt_1", content)
        assert isinstance(ref_hash, str)
        assert store.get("receipt_1") == content

    def test_missing_receipt_returns_none(self) -> None:
        store = ReceiptStore()
        assert store.get("missing") is None

    def test_list_receipt_ids(self) -> None:
        store = ReceiptStore()
        store.put("r1", "a")
        store.put("r2", "b")
        ids = store.list_receipt_ids()
        assert sorted(ids) == ["r1", "r2"]


class TestProjectionEngine:
    def test_project_with_system_hint(self) -> None:
        engine = ProjectionEngine()
        receipt_store = ReceiptStore()
        receipt_store.put("ref_1", "receipt content")
        messages = engine.project(
            {
                "system_hint": "You are a coding assistant.",
                "turns": [
                    {"role": "user", "content": "hello", "receipt_refs": ["ref_1"]},
                ],
            },
            receipt_store,
        )
        assert messages[0] == {"role": "system", "content": "You are a coding assistant."}
        assert messages[1]["role"] == "user"
        assert "receipt content" in messages[1]["content"]

    def test_project_excludes_control_plane_noise(self) -> None:
        engine = ProjectionEngine()
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "do it"}],
                "budget_status": {"remaining": 10},
                "policy_verdict": "allowed",
            },
            ReceiptStore(),
        )
        # budget_status and policy_verdict should not appear in messages
        for msg in messages:
            assert "budget_status" not in msg["content"]
            assert "policy_verdict" not in msg["content"]

    def test_project_strips_telemetry_noise(self) -> None:
        engine = ProjectionEngine()
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "do it"}],
                "telemetry": {"latency_ms": 42},
                "telemetry_events": [{"event": "foo"}],
                "metrics": {"tokens": 100},
            },
            ReceiptStore(),
        )
        for msg in messages:
            assert "telemetry" not in msg["content"]
            assert "metrics" not in msg["content"]


class TestContentStore:
    def test_dedup_works_across_shared_instance(self) -> None:
        store = ContentStore(workspace=".")
        ref1 = store.intern("duplicate content")
        ref2 = store.intern("duplicate content")
        assert ref1.hash == ref2.hash
        assert store.stats["dedup_saved_bytes"] > 0
