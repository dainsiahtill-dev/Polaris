"""Unit tests for orchestration.pm_planning internal pm_agent.

Tests PMTask, PMTaskStore (with tmp_path), and PMAgent tool methods
that don't require external infrastructure.
"""

from __future__ import annotations

from polaris.cells.orchestration.pm_planning.internal.pm_agent import (
    PMTask,
    PMTaskStore,
)

# ---------------------------------------------------------------------------
# PMTask
# ---------------------------------------------------------------------------


class TestPMTask:
    def test_construction(self) -> None:
        task = PMTask(
            task_id="task-1",
            title="Build login",
            goal="Create a login form",
            priority=1,
        )
        assert task.task_id == "task-1"
        assert task.title == "Build login"
        assert task.goal == "Create a login form"
        assert task.priority == 1
        assert task.status == "todo"

    def test_defaults(self) -> None:
        task = PMTask(task_id="t1", title="T", goal="G")
        assert task.priority == 5
        assert task.context_files == []
        assert task.target_files == []
        assert task.scope_paths == []
        assert task.constraints == []
        assert task.acceptance == []
        assert task.dependencies == []

    def test_to_dict(self) -> None:
        task = PMTask(task_id="t1", title="T", goal="G")
        d = task.to_dict()
        assert d["id"] == "t1"
        assert d["title"] == "T"
        assert d["goal"] == "G"
        assert "created_at" in d
        assert "updated_at" in d

    def test_from_dict(self) -> None:
        data = {
            "id": "t2",
            "title": "From dict",
            "goal": "Goal from dict",
            "status": "in_progress",
            "priority": 3,
            "context_files": ["a.py"],
            "target_files": ["b.py"],
            "scope_paths": ["src/"],
            "constraints": ["constraint1"],
            "acceptance": ["check1"],
            "assigned_to": "director",
            "phase": "implementation",
            "dependencies": ["t1"],
        }
        task = PMTask.from_dict(data)
        assert task.task_id == "t2"
        assert task.status == "in_progress"
        assert task.priority == 3
        assert task.context_files == ["a.py"]
        assert task.assigned_to == "director"
        assert task.phase == "implementation"
        assert task.dependencies == ["t1"]

    def test_roundtrip(self) -> None:
        original = PMTask(
            task_id="t3",
            title="Roundtrip",
            goal="Test roundtrip",
            priority=2,
            context_files=["x.py"],
        )
        restored = PMTask.from_dict(original.to_dict())
        assert restored.task_id == original.task_id
        assert restored.title == original.title
        assert restored.context_files == original.context_files

    def test_update_status(self) -> None:
        task = PMTask(task_id="t1", title="T", goal="G")
        task.update_status("in_progress", result={"key": "val"})
        assert task.status == "in_progress"
        assert task.result == {"key": "val"}

    def test_update_status_with_error(self) -> None:
        task = PMTask(task_id="t1", title="T", goal="G")
        task.update_status("failed", error="something broke")
        assert task.status == "failed"
        assert task.error == "something broke"

    def test_update_status_preserves_other_fields(self) -> None:
        task = PMTask(task_id="t1", title="T", goal="G")
        task.update_status("done")
        assert task.goal == "G"


# ---------------------------------------------------------------------------
# PMTaskStore
# ---------------------------------------------------------------------------


class TestPMTaskStore:
    def test_save_and_load(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        task = PMTask(
            task_id="store-1",
            title="Persist task",
            goal="Make sure it persists",
            priority=1,
        )
        store.save_task(task)

        loaded = store.load_task("store-1")
        assert loaded is not None
        assert loaded.task_id == "store-1"
        assert loaded.title == "Persist task"

    def test_load_nonexistent(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        result = store.load_task("does-not-exist")
        assert result is None

    def test_list_tasks(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        store.save_task(PMTask(task_id="t1", title="T1", goal="G1", priority=3))
        store.save_task(PMTask(task_id="t2", title="T2", goal="G2", priority=1))

        tasks = store.list_tasks()
        assert len(tasks) == 2

    def test_list_tasks_sorted_by_priority(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        store.save_task(PMTask(task_id="t1", title="Low", goal="G", priority=9))
        store.save_task(PMTask(task_id="t2", title="High", goal="G", priority=1))
        store.save_task(PMTask(task_id="t3", title="Mid", goal="G", priority=5))

        tasks = store.list_tasks()
        assert tasks[0].priority == 1
        assert tasks[1].priority == 5
        assert tasks[2].priority == 9

    def test_list_tasks_filtered_by_status(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        t1 = PMTask(task_id="t1", title="T1", goal="G", status="todo")
        t2 = PMTask(task_id="t2", title="T2", goal="G", status="done")
        store.save_task(t1)
        store.save_task(t2)

        todo_tasks = store.list_tasks(status="todo")
        done_tasks = store.list_tasks(status="done")
        assert len(todo_tasks) == 1
        assert todo_tasks[0].task_id == "t1"
        assert len(done_tasks) == 1
        assert done_tasks[0].task_id == "t2"

    def test_get_pending_tasks(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        t1 = PMTask(task_id="t1", title="T1", goal="G", status="pending_dispatch")
        t2 = PMTask(task_id="t2", title="T2", goal="G", status="todo")
        store.save_task(t1)
        store.save_task(t2)

        pending = store.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0].task_id == "t1"

    def test_get_completed_tasks(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        t1 = PMTask(task_id="t1", title="T1", goal="G", status="completed")
        store.save_task(t1)

        completed = store.get_completed_tasks()
        assert len(completed) == 1

    def test_overwrite_task(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        store.save_task(PMTask(task_id="t1", title="Original", goal="G"))
        store.save_task(PMTask(task_id="t1", title="Updated", goal="G"))

        tasks = store.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "Updated"

    def test_index_persistence(self, tmp_path) -> None:
        store1 = PMTaskStore(str(tmp_path))
        store1.save_task(PMTask(task_id="idx-1", title="I1", goal="G"))

        # New store instance reads from same directory
        store2 = PMTaskStore(str(tmp_path))
        tasks = store2.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].task_id == "idx-1"

    def test_load_task_corrupted_file(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        task_file = tmp_path / "runtime" / "state" / "pm_tasks" / "corrupt.json"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text("not valid json{{{", encoding="utf-8")

        result = store.load_task("corrupt")
        assert result is None

    def test_load_task_missing_file(self, tmp_path) -> None:
        store = PMTaskStore(str(tmp_path))
        result = store.load_task("totally-absent")
        assert result is None
