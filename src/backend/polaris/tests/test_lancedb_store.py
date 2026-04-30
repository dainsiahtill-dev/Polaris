import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_lancedb_store():
    # repo_root is the polaris directory (2 levels up from tests/test_lancedb_store.py)
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "lancedb_store.py"
    spec = importlib.util.spec_from_file_location("lancedb_store", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load lancedb_store.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLanceDBStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _load_lancedb_store()

    def test_ensure_record(self):
        record = self.store.ensure_record({"value": 1})
        self.assertIn("id", record)
        self.assertIn("timestamp", record)
        records = self.store.ensure_record([{"value": 2}])
        self.assertIsInstance(records, list)
        self.assertIn("id", records[0])

    def test_load_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data.json"
            path.write_text(json.dumps({"a": 1}), encoding="utf-8")
            data = self.store.load_json(path)
            self.assertEqual(data["a"], 1)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
