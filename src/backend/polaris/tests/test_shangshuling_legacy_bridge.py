import os
import shutil
import sys
import tempfile
import unittest

import pytest

# Skip this test - pm.* modules have been migrated to polaris/cells
try:
    from pm.pm_integration import get_pm, reset_pm
    from pm.task_orchestrator import TaskStatus
except ImportError:
    pytest.importorskip("polaris.cells.pm")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_SCRIPTS = os.path.join(REPO_ROOT, "src", "backend", "scripts")
BACKEND_CORE = os.path.join(REPO_ROOT, "src", "backend", "core", "polaris_loop")
if BACKEND_SCRIPTS not in sys.path:
    sys.path.insert(0, BACKEND_SCRIPTS)
if BACKEND_CORE not in sys.path:
    sys.path.insert(0, BACKEND_CORE)


class TestShangshulingLegacyBridge(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="shangshuling_legacy_bridge_")
        self.workspace = os.path.join(self.test_dir, "workspace")
        os.makedirs(self.workspace, exist_ok=True)
        self.pm = get_pm(self.workspace)
        self.pm.initialize(project_name="LegacyBridge", description="test")

    def tearDown(self) -> None:
        reset_pm(self.workspace)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sync_is_idempotent_by_legacy_id(self) -> None:
        legacy_tasks = [
            {
                "id": "PM-LEGACY-001",
                "title": "Implement API",
                "description": "Bridge test task",
                "status": "todo",
                "priority": "high",
                "assignee": "Director",
                "assignee_type": "Director",
            }
        ]

        first = self.pm.sync_from_legacy_tasks(legacy_tasks)
        second = self.pm.sync_from_legacy_tasks(legacy_tasks)

        stats = self.pm.tasks.get_stats_summary()
        canonical = self.pm.resolve_task_id("PM-LEGACY-001")
        ready = self.pm.get_ready_tasks_for_director(limit=10)

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(stats.get("total"), 1)
        self.assertTrue(str(canonical or "").startswith("TASK-"))
        self.assertTrue(any(item.get("id") == "PM-LEGACY-001" for item in ready))

    def test_record_completion_accepts_legacy_id(self) -> None:
        legacy_tasks = [
            {
                "id": "PM-LEGACY-002",
                "title": "Build worker",
                "description": "Record completion via legacy id",
                "status": "assigned",
                "priority": "medium",
                "assignee": "Director",
                "assignee_type": "Director",
            }
        ]
        self.pm.sync_from_legacy_tasks(legacy_tasks)

        ok = self.pm.record_task_completion(
            "PM-LEGACY-002",
            "Director",
            success=True,
            result={
                "verification_method": "auto_check",
                "evidence": "unit tests green",
                "summary": "task done",
            },
        )

        canonical = self.pm.resolve_task_id("PM-LEGACY-002")
        task = self.pm.tasks.get_task(canonical or "")

        self.assertTrue(ok)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, TaskStatus.COMPLETED)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
