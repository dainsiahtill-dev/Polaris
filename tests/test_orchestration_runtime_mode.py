import argparse
import os
import sys
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_SCRIPTS = os.path.join(REPO_ROOT, "src", "backend", "scripts")
BACKEND_CORE = os.path.join(REPO_ROOT, "src", "backend", "core", "polaris_loop")
if BACKEND_SCRIPTS not in sys.path:
    sys.path.insert(0, BACKEND_SCRIPTS)
if BACKEND_CORE not in sys.path:
    sys.path.insert(0, BACKEND_CORE)

from pm.orchestration_engine import _resolve_orchestration_runtime


class TestOrchestrationRuntimeMode(unittest.TestCase):
    def test_args_value_takes_precedence(self) -> None:
        args = argparse.Namespace(orchestration_runtime="nodes")
        self.assertEqual(_resolve_orchestration_runtime(args), "nodes")

    def test_invalid_value_falls_back_to_legacy(self) -> None:
        args = argparse.Namespace(orchestration_runtime="invalid")
        self.assertEqual(_resolve_orchestration_runtime(args), "legacy")

    def test_env_value_used_when_args_missing(self) -> None:
        previous = os.environ.get("POLARIS_ORCHESTRATION_RUNTIME")
        os.environ["POLARIS_ORCHESTRATION_RUNTIME"] = "auto"
        try:
            args = argparse.Namespace()
            self.assertEqual(_resolve_orchestration_runtime(args), "auto")
        finally:
            if previous is None:
                os.environ.pop("POLARIS_ORCHESTRATION_RUNTIME", None)
            else:
                os.environ["POLARIS_ORCHESTRATION_RUNTIME"] = previous


if __name__ == "__main__":
    raise SystemExit(unittest.main())
