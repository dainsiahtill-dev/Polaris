"""Tests for plan bridge: docs/product/plan.md -> .polaris/runtime/contracts/plan.md

Also validates pm_tasks.schema.json accepts old and new task formats.
"""

import json
import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path

log = logging.getLogger("test_plan_sync")


def _sync_plan_to_runtime(workspace: str, cache_root: str) -> None:
    """Standalone reimplementation of the plan sync logic under test.
    Mirrors _sync_plan_to_runtime() in src/backend/app/routers/docs.py
    to avoid importing the full FastAPI module graph."""
    plan_src = os.path.join(workspace, "docs", "product", "plan.md")
    if not os.path.isfile(plan_src):
        log.info("PLAN_SYNC_SKIP: %s does not exist", plan_src)
        return
    plan_dst = os.path.join(workspace, ".polaris", "runtime", "contracts", "plan.md")
    os.makedirs(os.path.dirname(plan_dst), exist_ok=True)
    tmp_path = plan_dst + ".tmp"
    try:
        shutil.copy2(plan_src, tmp_path)
        os.replace(tmp_path, plan_dst)
        log.info("PLAN_SYNC_OK: %s -> %s", plan_src, plan_dst)
    except Exception:
        log.warning("PLAN_SYNC_FAIL: could not sync plan to runtime", exc_info=True)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class TestSyncPlanToRuntime(unittest.TestCase):
    """Test the plan bridge logic."""

    def test_sync_plan_copies_file(self):
        with tempfile.TemporaryDirectory() as workspace:
            plan_dir = os.path.join(workspace, "docs", "product")
            os.makedirs(plan_dir)
            plan_src = os.path.join(plan_dir, "plan.md")
            with open(plan_src, "w", encoding="utf-8") as f:
                f.write("# My Plan\n\n## Backlog\n- Task 1\n- Task 2\n")

            _sync_plan_to_runtime(workspace, "")

            plan_dst = os.path.join(workspace, ".polaris", "runtime", "contracts", "plan.md")
            self.assertTrue(os.path.isfile(plan_dst))
            with open(plan_dst, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Task 1", content)

    def test_sync_plan_skips_when_source_missing(self):
        with tempfile.TemporaryDirectory() as workspace:
            _sync_plan_to_runtime(workspace, "")
            plan_dst = os.path.join(workspace, ".polaris", "runtime", "contracts", "plan.md")
            self.assertFalse(os.path.exists(plan_dst))

    def test_sync_plan_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as workspace:
            plan_dir = os.path.join(workspace, "docs", "product")
            os.makedirs(plan_dir)
            plan_src = os.path.join(plan_dir, "plan.md")
            runtime_dir = os.path.join(workspace, ".polaris", "runtime")
            os.makedirs(runtime_dir, exist_ok=True)
            plan_dst = os.path.join(runtime_dir, "contracts", "plan.md")
            os.makedirs(os.path.dirname(plan_dst), exist_ok=True)

            with open(plan_dst, "w", encoding="utf-8") as f:
                f.write("Old plan content")
            with open(plan_src, "w", encoding="utf-8") as f:
                f.write("New plan content")

            _sync_plan_to_runtime(workspace, "")

            with open(plan_dst, encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, "New plan content")

    def test_sync_plan_idempotent(self):
        with tempfile.TemporaryDirectory() as workspace:
            plan_dir = os.path.join(workspace, "docs", "product")
            os.makedirs(plan_dir)
            plan_src = os.path.join(plan_dir, "plan.md")
            with open(plan_src, "w", encoding="utf-8") as f:
                f.write("Idempotent plan")

            _sync_plan_to_runtime(workspace, "")
            _sync_plan_to_runtime(workspace, "")

            plan_dst = os.path.join(workspace, ".polaris", "runtime", "contracts", "plan.md")
            with open(plan_dst, encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, "Idempotent plan")

    def test_sync_plan_no_leftover_tmp_file(self):
        with tempfile.TemporaryDirectory() as workspace:
            plan_dir = os.path.join(workspace, "docs", "product")
            os.makedirs(plan_dir)
            with open(os.path.join(plan_dir, "plan.md"), "w", encoding="utf-8") as f:
                f.write("Plan content")

            _sync_plan_to_runtime(workspace, "")

            runtime_dir = os.path.join(workspace, ".polaris", "runtime")
            tmp = os.path.join(runtime_dir, "contracts", "plan.md.tmp")
            self.assertFalse(os.path.exists(tmp))


class TestSchemaValidation(unittest.TestCase):
    """Validate that pm_tasks.schema.json accepts old and new task formats."""

    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schema" / "pm_tasks.schema.json"
        if not schema_path.is_file():
            raise unittest.SkipTest(f"Schema not found: {schema_path}")
        with open(schema_path, encoding="utf-8") as f:
            cls.schema = json.load(f)
        try:
            import jsonschema
            cls.jsonschema = jsonschema
        except ImportError:
            raise unittest.SkipTest("jsonschema not installed")

    def _validate(self, data):
        self.jsonschema.validate(data, self.schema)

    def _base_payload(self, tasks):
        return {
            "schema_version": 1,
            "run_id": "test-run-001",
            "pm_iteration": 1,
            "tasks": tasks,
        }

    def test_old_format_without_new_fields_is_valid(self):
        task = {
            "id": "PM-OLD",
            "priority": 1,
            "dependencies": [],
            "spec": "",
            "acceptance_criteria": ["test passes"],
            "assigned_to": "Director",
        }
        self._validate(self._base_payload([task]))

    def test_new_format_with_all_fields_is_valid(self):
        task = {
            "id": "PM-NEW",
            "priority": 1,
            "dependencies": [],
            "spec": "",
            "acceptance_criteria": ["test passes"],
            "assigned_to": "Director",
            "backlog_ref": "Implement file upload",
            "error_code": "QA_FAIL",
            "failure_detail": "Unit tests failed",
            "failed_at": "2026-02-19T10:00:00Z",
        }
        self._validate(self._base_payload([task]))

    def test_new_format_backlog_ref_only(self):
        task = {
            "id": "PM-BR",
            "priority": 1,
            "dependencies": [],
            "spec": "",
            "acceptance_criteria": ["ok"],
            "assigned_to": "Director",
            "backlog_ref": "Setup Express server",
        }
        self._validate(self._base_payload([task]))

    def test_empty_tasks_is_valid(self):
        self._validate(self._base_payload([]))

    def test_failure_fields_without_backlog_ref(self):
        task = {
            "id": "PM-FAIL",
            "priority": 1,
            "dependencies": [],
            "spec": "",
            "acceptance_criteria": ["ok"],
            "assigned_to": "Director",
            "error_code": "RISK_BLOCKED",
            "failure_detail": "Risk too high",
            "failed_at": "2026-02-19T10:00:00Z",
        }
        self._validate(self._base_payload([task]))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
