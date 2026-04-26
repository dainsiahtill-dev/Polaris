import unittest
from pathlib import Path
import importlib.util


def _load_shared():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "core" / "polaris_loop" / "shared.py"
    spec = importlib.util.spec_from_file_location("shared", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load shared.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSharedUtils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = _load_shared()

    def test_strip_ansi(self):
        text = "\x1b[31mError\x1b[0m"
        self.assertEqual(self.shared.strip_ansi(text), "Error")

    def test_safe_truncate(self):
        text = "x" * 10
        self.assertEqual(self.shared.safe_truncate(text, 5), "xxxxx...")

    def test_extract_rate_limit_seconds(self):
        text = "resets_in_seconds\\\":120"
        self.assertEqual(self.shared.extract_rate_limit_seconds(text), 120)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
