import unittest
from pathlib import Path
import importlib.util


def _load_ollama_utils():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "core" / "polaris_loop" / "ollama_utils.py"
    spec = importlib.util.spec_from_file_location("ollama_utils", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load ollama_utils.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestOllamaUtils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ollama_utils = _load_ollama_utils()

    def test_clean_terminal_output(self):
        raw = "\x1b[31mError\x1b[0m\r\n"
        cleaned = self.ollama_utils.clean_terminal_output(raw)
        self.assertEqual(cleaned.strip(), "Error")

    def test_is_spinner_only(self):
        self.assertTrue(self.ollama_utils.is_spinner_only("⠀"))
        self.assertFalse(self.ollama_utils.is_spinner_only("done"))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
