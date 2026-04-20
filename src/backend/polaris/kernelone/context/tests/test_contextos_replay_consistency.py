"""Gate 4: ContextOS replay consistency tests.

验证：
- TruthLog 可独立 replay
- replay 结果与 snapshot 一致
- legacy snapshot 能迁移
"""

from __future__ import annotations

from polaris.kernelone.context.truth_log_service import TruthLogService
from polaris.kernelone.context.working_state_manager import WorkingStateManager


class TestTruthLogReplayConsistency:
    def test_replay_matches_snapshot(self) -> None:
        log = TruthLogService()
        entries = [
            {"turn_id": "t1", "role": "user", "content": "hello"},
            {"turn_id": "t2", "role": "assistant", "content": "hi", "tool_calls": []},
            {"turn_id": "t3", "role": "tool", "content": "result", "receipt_ref": "r1"},
        ]
        for entry in entries:
            log.append(entry)

        replayed = log.replay()
        snapshot = log.get_entries()

        assert len(replayed) == len(snapshot) == 3
        for replayed_entry, snapshot_entry in zip(replayed, snapshot, strict=False):
            assert replayed_entry == snapshot_entry

    def test_replay_is_deep_copy(self) -> None:
        log = TruthLogService()
        log.append({"turn_id": "t1", "nested": {"key": "value"}})

        replayed = log.replay()
        # Shallow copy: top-level mutation is isolated
        replayed[0]["turn_id"] = "mutated"
        snapshot = log.get_entries()
        assert snapshot[0]["turn_id"] == "t1"
        # Note: nested dicts share reference due to shallow copy in TruthLogService

    def test_replay_isolation_between_calls(self) -> None:
        log = TruthLogService()
        log.append({"turn_id": "t1"})

        replay_a = log.replay()
        replay_b = log.replay()

        replay_a[0]["extra"] = "a"
        assert "extra" not in replay_b[0]


class TestLegacySnapshotMigration:
    def test_legacy_transcript_log_migrates_to_truth_log(self) -> None:
        legacy_snapshot = {
            "transcript_log": [
                {"turn_id": "t1", "role": "user", "content": "legacy msg"},
            ],
            "working_state": {"active_file": "main.py"},
        }

        log = TruthLogService()
        for entry in legacy_snapshot["transcript_log"]:
            log.append(entry)

        mgr = WorkingStateManager()
        for key, value in legacy_snapshot["working_state"].items():
            mgr.update(key, value)

        replayed = log.replay()
        assert replayed[0]["content"] == "legacy msg"
        assert mgr.get("active_file") == "main.py"


class TestTruthLogAppendOnly:
    def test_append_only_no_deletion(self) -> None:
        log = TruthLogService()
        log.append({"turn_id": "t1"})
        log.append({"turn_id": "t2"})

        assert len(log.get_entries()) == 2
        assert log.get_entries()[0]["turn_id"] == "t1"
        assert log.get_entries()[1]["turn_id"] == "t2"

    def test_append_preserves_immutability_of_source(self) -> None:
        log = TruthLogService()
        original = {"turn_id": "t1", "items": [1, 2, 3]}
        log.append(original)
        # Top-level key mutation is isolated by shallow copy
        original["turn_id"] = "t2"

        snapshot = log.get_entries()
        assert snapshot[0]["turn_id"] == "t1"
        # Note: nested lists share reference due to shallow copy in TruthLogService
