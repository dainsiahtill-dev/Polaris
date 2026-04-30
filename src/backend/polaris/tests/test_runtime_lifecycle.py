"""Test for runtime_lifecycle module.

The core.polaris_loop.runtime_lifecycle module has been migrated to
polaris.domain.director.lifecycle. These tests verify the canonical
polaris import path.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

if importlib.util.find_spec("polaris.domain.director.lifecycle") is None:
    import unittest
    raise unittest.SkipTest("Module not available: polaris.domain.director.lifecycle")

from polaris.domain.director.lifecycle import (
    read as read_director_lifecycle,
    update as update_director_lifecycle,
)


class TestRuntimeLifecycle(unittest.TestCase):
    def test_update_director_lifecycle_tracks_start_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            path = str(run_dir / "DIRECTOR_LIFECYCLE.json")
            update_director_lifecycle(
                path=path,
                phase="startup_ready",
                run_id="pm-00001",
                task_id="PM-1",
                startup_completed=True,
                execution_started=False,
                status="running",
            )
            update_director_lifecycle(
                path=path,
                phase="tooling",
                execution_started=True,
                status="running",
            )
            payload = read_director_lifecycle(path)
            self.assertEqual(payload.get("run_id"), "pm-00001")
            self.assertEqual(payload.get("task_id"), "PM-1")
            self.assertTrue(payload.get("startup_completed"))
            self.assertTrue(payload.get("execution_started"))
            self.assertEqual(payload.get("phase"), "tooling")


if __name__ == "__main__":
    unittest.main()
