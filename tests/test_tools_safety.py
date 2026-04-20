import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


def _load_tools_module():
    here = Path(__file__).resolve()
    tools_path = here.parents[1] / "tools.py"
    spec = importlib.util.spec_from_file_location("polaris_tools", tools_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestToolsSafety(unittest.TestCase):
    def test_ensure_within_root_different_drive_raises_value_error(self):
        tools = _load_tools_module()
        root = os.path.abspath(os.getcwd())
        with self.assertRaises(ValueError):
            tools._ensure_within_root(root, "D:\\__polaris_test__\\x.txt")

    def test_ts_apply_replacement_is_atomic_and_bounds_checked(self):
        tools = _load_tools_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = str(Path(temp_dir) / "sample.txt")
            with open(file_path, "wb") as handle:
                handle.write(b"hello world")

            tools._ts_apply_replacement(file_path, 6, 11, "there")
            with open(file_path, "rb") as handle:
                self.assertEqual(handle.read(), b"hello there")

            with self.assertRaises(ValueError):
                tools._ts_apply_replacement(file_path, -1, 2, "x")
            with self.assertRaises(ValueError):
                tools._ts_apply_replacement(file_path, 0, 9999, "x")


if __name__ == "__main__":
    raise SystemExit(unittest.main())

