"""Test for runtime_lifecycle module."""
import unittest
import tempfile
from pathlib import Path
import os
import sys

# Add src/backend to sys.path for core module imports
# This must match the structure expected by pytest
_repo_root = Path(__file__).resolve().parents[1]  # Go up from tests/ to root
_backend_dir = str(_repo_root / "src" / "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


def _load_runtime_lifecycle():
    """Load runtime_lifecycle module using package import."""
    from core.polaris_loop import runtime_lifecycle
    return runtime_lifecycle


class TestRuntimeLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lifecycle = _load_runtime_lifecycle()

    def test_update_director_lifecycle_tracks_start_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            path = self.lifecycle.director_lifecycle_path(str(run_dir))
            self.lifecycle.update_director_lifecycle(
                path,
                phase="startup_ready",
                run_id="pm-00001",
                task_id="PM-1",
                startup_completed=True,
                execution_started=False,
                status="running",
            )
            self.lifecycle.update_director_lifecycle(
                path,
                phase="tooling",
                execution_started=True,
                status="running",
            )
            payload = self.lifecycle.read_director_lifecycle(path)
            self.assertEqual(payload.get("run_id"), "pm-00001")
            self.assertEqual(payload.get("task_id"), "PM-1")
            self.assertTrue(payload.get("startup_completed"))
            self.assertTrue(payload.get("execution_started"))
            self.assertEqual(payload.get("phase"), "tooling")


if __name__ == "__main__":
    unittest.main()
