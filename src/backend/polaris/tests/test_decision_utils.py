import importlib.util
import unittest
from pathlib import Path


def _load_decision_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "core" / "polaris_loop" / "decision.py"
    spec = importlib.util.spec_from_file_location("decision", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load decision.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDecisionUtils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decision = _load_decision_module()

    def test_get_numbered_options(self):
        text = "1) foo\n2) bar"
        opts = self.decision.get_numbered_options(text)
        self.assertEqual(len(opts), 2)
        self.assertEqual(opts[0]["number"], 1)

    def test_needs_decision(self):
        text = "Please choose:\n1) A\n2) B"
        self.assertTrue(self.decision.needs_decision(text))

    def test_parse_decision_number(self):
        options = [{"number": 1, "text": "A"}, {"number": 2, "text": "B"}]
        self.assertEqual(self.decision.parse_decision_number("2", options), 2)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
