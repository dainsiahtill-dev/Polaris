"""Tests for ``internal/store_sqlite.py``."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord
from polaris.cells.runtime.task_market.internal.store_sqlite import TaskMarketSQLiteStore


class TestTaskMarketSQLiteStore:
    """Integration tests for SQLite WAL store."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> str:
        ws = str(tmp_path / "ws")
        return ws

    @pytest.fixture
    def store(self, workspace: str) -> TaskMarketSQLiteStore:
        return TaskMarketSQLiteStore(workspace)

    # ---- work_items --------------------------------------------------------

    def test_upsert_and_load_item(self, store: TaskMarketSQLiteStore) -> None:
        item = TaskWorkItemRecord(
            task_id="task-sqlite-1",
            trace_id="trace-1",
            run_id="run-1",
            workspace=store._workspace,
            stage="pending_exec",
            status="pending_exec",
            priority="high",
            payload={"foo": "bar"},
            metadata={},
        )
        store.upsert_item(item)

        loaded = store.load_items()
        assert "task-sqlite-1" in loaded
        assert loaded["task-sqlite-1"].trace_id == "trace-1"
        assert loaded["task-sqlite-1"].payload["foo"] == "bar"

    def test_upsert_updates_existing(self, store: TaskMarketSQLiteStore) -> None:
        item = TaskWorkItemRecord(
            task_id="task-upd",
            trace_id="trace-1",
            run_id="run-1",
            workspace=store._workspace,
            stage="pending_exec",
            status="pending_exec",
            priority="medium",
            payload={},
            metadata={},
        )
        store.upsert_item(item)

        # Must explicitly increment version before re-upserting
        # (SQLite INSERT OR REPLACE uses item.version literally, not version+1).
        item.priority = "critical"
        item.version = 2  # explicitly increment like service.py does
        store.upsert_item(item)

        loaded = store.load_items()
        assert loaded["task-upd"].priority == "critical"
        assert loaded["task-upd"].version == 2

    def test_revision_fields_round_trip(self, store: TaskMarketSQLiteStore) -> None:
        item = TaskWorkItemRecord(
            task_id="task-revision",
            trace_id="trace-revision",
            run_id="run-revision",
            workspace=store._workspace,
            stage="pending_design",
            status="pending_design",
            priority="high",
            plan_id="plan-1",
            plan_revision_id="rev-3",
            root_task_id="root-1",
            parent_task_id="epic-7",
            is_leaf=False,
            depends_on=["dep-1", "dep-2"],
            requirement_digest="req-digest",
            constraint_digest="constraint-digest",
            summary_ref="summary://task-revision",
            superseded_by_revision="rev-4",
            change_policy="strict",
            compensation_group_id="cg-9",
            payload={"foo": "bar"},
            metadata={"kind": "epic"},
        )
        store.upsert_item(item)

        loaded = store.load_items()["task-revision"]
        assert loaded.plan_id == "plan-1"
        assert loaded.plan_revision_id == "rev-3"
        assert loaded.root_task_id == "root-1"
        assert loaded.parent_task_id == "epic-7"
        assert loaded.is_leaf is False
        assert loaded.depends_on == ["dep-1", "dep-2"]
        assert loaded.requirement_digest == "req-digest"
        assert loaded.constraint_digest == "constraint-digest"
        assert loaded.summary_ref == "summary://task-revision"
        assert loaded.superseded_by_revision == "rev-4"
        assert loaded.change_policy == "strict"
        assert loaded.compensation_group_id == "cg-9"

    def test_load_items_returns_all_for_workspace(self, store: TaskMarketSQLiteStore) -> None:
        for i in range(3):
            item = TaskWorkItemRecord(
                task_id=f"task-{i}",
                trace_id=f"trace-{i}",
                run_id="run-1",
                workspace=store._workspace,
                stage="pending_design",
                status="pending_design",
                priority="medium",
                payload={},
                metadata={},
            )
            store.upsert_item(item)

        loaded = store.load_items()
        assert len(loaded) == 3

    # ---- transitions -------------------------------------------------------

    def test_append_and_load_transition(self, store: TaskMarketSQLiteStore) -> None:
        store.append_transition(
            task_id="task-t1",
            from_status="pending_design",
            to_status="in_design",
            event_type="claimed",
            worker_id="ce-1",
            lease_token="tok123",
            version=1,
            metadata={},
        )
        transitions = store.load_transitions("task-t1")
        assert len(transitions) == 1
        assert transitions[0]["from_status"] == "pending_design"
        assert transitions[0]["to_status"] == "in_design"
        assert transitions[0]["event_type"] == "claimed"

    # ---- dead_letter_items ------------------------------------------------

    def test_append_and_load_dead_letters(self, store: TaskMarketSQLiteStore) -> None:
        store.append_dead_letter(
            {
                "task_id": "task-dlq-1",
                "trace_id": "trace-1",
                "run_id": "run-1",
                "workspace": store._workspace,
                "reason": "exec_failed",
                "error_code": "ERR_EXEC",
                "attempts": 3,
                "max_attempts": 3,
                "metadata": {},
                "dead_lettered_at": "2026-04-14T00:00:00Z",
            }
        )

        entries = store.load_dead_letters(limit=10)
        assert len(entries) == 1
        assert entries[0]["task_id"] == "task-dlq-1"
        assert entries[0]["error_code"] == "ERR_EXEC"

    def test_dead_letters_respects_limit(self, store: TaskMarketSQLiteStore) -> None:
        for i in range(5):
            store.append_dead_letter(
                {
                    "task_id": f"task-dlq-{i}",
                    "trace_id": f"trace-{i}",
                    "run_id": "run-1",
                    "workspace": store._workspace,
                    "reason": "fail",
                    "error_code": f"ERR_{i}",
                    "attempts": 3,
                    "max_attempts": 3,
                    "metadata": {},
                    "dead_lettered_at": f"2026-04-14T00:00:{i:02d}Z",
                }
            )

        entries = store.load_dead_letters(limit=3)
        assert len(entries) == 3

    # ---- plan_revisions ----------------------------------------------------

    def test_upsert_and_load_plan_revision(self, store: TaskMarketSQLiteStore) -> None:
        store.upsert_plan_revision(
            {
                "workspace": store._workspace,
                "plan_id": "plan-1",
                "plan_revision_id": "rev-1",
                "parent_revision_id": "",
                "source_role": "pm",
                "requirement_digest": "req-1",
                "constraint_digest": "cons-1",
                "metadata": {"origin": "pm"},
                "created_at": "2026-04-14T00:00:00+00:00",
            }
        )
        rows = store.load_plan_revisions(store._workspace, plan_id="plan-1")
        assert len(rows) == 1
        assert rows[0]["plan_revision_id"] == "rev-1"
        metadata = rows[0]["metadata"]
        assert isinstance(metadata, dict)
        assert metadata.get("origin") == "pm"

    # ---- change_orders -----------------------------------------------------

    def test_append_and_load_change_order(self, store: TaskMarketSQLiteStore) -> None:
        store.append_change_order(
            {
                "workspace": store._workspace,
                "plan_id": "plan-1",
                "from_revision_id": "rev-1",
                "to_revision_id": "rev-2",
                "change_type": "doc_patch",
                "source_role": "pm",
                "summary": "doc updated",
                "trace_id": "trace-1",
                "affected_task_ids": ["task-1", "task-2"],
                "impact_counts": {"superseded": 2},
                "metadata": {"ticket": "CO-1"},
                "created_at": "2026-04-14T00:00:01+00:00",
            }
        )
        rows = store.load_change_orders(store._workspace, plan_id="plan-1")
        assert len(rows) == 1
        assert rows[0]["change_type"] == "doc_patch"
        assert rows[0]["affected_task_ids"] == ["task-1", "task-2"]
        impact_counts = rows[0]["impact_counts"]
        assert isinstance(impact_counts, dict)
        assert impact_counts.get("superseded") == 2

    # ---- outbox_messages ----------------------------------------------------

    def test_append_load_and_mark_outbox_message(self, store: TaskMarketSQLiteStore) -> None:
        store.append_outbox_message(
            {
                "outbox_id": "outbox-1",
                "workspace": store._workspace,
                "stream": "task_market.events",
                "event_type": "task_market.test",
                "source": "runtime.task_market",
                "run_id": "run-1",
                "task_id": "task-1",
                "payload": {"x": 1},
                "status": "pending",
                "attempts": 0,
                "last_error": "",
                "created_at": "2026-04-14T00:00:00+00:00",
            }
        )

        pending = store.load_outbox_messages(store._workspace, statuses=("pending",), limit=10)
        assert len(pending) == 1
        assert pending[0]["outbox_id"] == "outbox-1"
        assert pending[0]["payload"] == {"x": 1}

        store.mark_outbox_message_failed(
            store._workspace,
            "outbox-1",
            error_message="emit_failed",
            failed_at="2026-04-14T00:01:00+00:00",
        )
        failed = store.load_outbox_messages(store._workspace, statuses=("failed",), limit=10)
        assert len(failed) == 1
        assert failed[0]["attempts"] == 1
        assert failed[0]["last_error"] == "emit_failed"

        store.mark_outbox_message_sent(
            store._workspace,
            "outbox-1",
            delivered_at="2026-04-14T00:02:00+00:00",
        )
        sent = store.load_outbox_messages(store._workspace, statuses=("sent",), limit=10)
        assert len(sent) == 1
        assert sent[0]["status"] == "sent"

    # ---- WAL mode -----------------------------------------------------------

    def test_wal_journal_mode(self, workspace: str) -> None:
        store = TaskMarketSQLiteStore(workspace)
        conn = store._get_conn()
        cursor = conn.execute("PRAGMA journal_mode")
        row = cursor.fetchone()
        assert row is not None
        assert row[0].lower() == "wal"

    # ---- concurrent access -------------------------------------------------

    def test_concurrent_upsert_no_corruption(self, store: TaskMarketSQLiteStore) -> None:
        errors: list[BaseException] = []

        def writer(start: int) -> None:
            try:
                for i in range(20):
                    item = TaskWorkItemRecord(
                        task_id=f"concurrent-{start + i}",
                        trace_id=f"trace-{start + i}",
                        run_id="run-1",
                        workspace=store._workspace,
                        stage="pending_exec",
                        status="pending_exec",
                        priority="medium",
                        payload={"n": start + i},
                        metadata={},
                    )
                    store.upsert_item(item)
            except Exception as e:  # noqa: BLE001 test requires capturing all errors
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i * 20,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent errors: {errors}"
        loaded = store.load_items()
        assert len(loaded) == 100

    # ---- workspace isolation -----------------------------------------------

    def test_workspace_isolation(self, tmp_path: Path) -> None:
        ws1 = str(tmp_path / "ws1")
        ws2 = str(tmp_path / "ws2")
        store1 = TaskMarketSQLiteStore(ws1)
        store2 = TaskMarketSQLiteStore(ws2)

        item1 = TaskWorkItemRecord(
            task_id="task-ws1",
            trace_id="trace-1",
            run_id="run-1",
            workspace=ws1,
            stage="pending_design",
            status="pending_design",
            priority="high",
            payload={},
            metadata={},
        )
        store1.upsert_item(item1)

        # ws2 should not see ws1 items.
        assert "task-ws1" not in store2.load_items()

    # ---- null workspace rejection -------------------------------------------

    def test_rejects_empty_workspace(self) -> None:
        with pytest.raises(ValueError, match="workspace is required"):
            TaskMarketSQLiteStore("")
