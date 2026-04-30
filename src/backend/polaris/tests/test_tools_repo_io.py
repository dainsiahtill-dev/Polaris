import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


def _load_tools_module():
    repo_root = Path(__file__).resolve().parents[2]
    tools_path = repo_root / "polaris" / "tools.py"
    spec = importlib.util.spec_from_file_location("polaris_tools", tools_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestToolsRepoIo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = _load_tools_module()
        cls.repo_root = Path(__file__).resolve().parents[2]

    def test_repo_read_head_tail_slice(self):
        with tempfile.TemporaryDirectory(dir=str(self.repo_root)) as temp_dir:
            file_path = Path(temp_dir) / "sample.txt"
            file_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
            rel = os.path.relpath(file_path, self.repo_root)
            head = self.tools.repo_read_head([rel, "2"], str(self.repo_root), 0)
            self.assertTrue(head["ok"])
            self.assertEqual(head["content"][0]["t"], "line1")
            tail = self.tools.repo_read_tail([rel, "2"], str(self.repo_root), 0)
            self.assertTrue(tail["ok"])
            self.assertEqual(tail["content"][-1]["t"], "line3")
            slice_out = self.tools.repo_read_slice([rel, "2", "3"], str(self.repo_root), 0)
            self.assertTrue(slice_out["ok"])
            self.assertEqual(slice_out["content"][0]["t"], "line2")

    def test_repo_rg_finds_match(self):
        with tempfile.TemporaryDirectory(dir=str(self.repo_root)) as temp_dir:
            file_path = Path(temp_dir) / "sample.txt"
            file_path.write_text("alpha\nbeta\n", encoding="utf-8")
            rel = os.path.relpath(file_path, self.repo_root)
            result = self.tools.repo_rg(["beta", rel], str(self.repo_root), 0)
            self.assertTrue(result["ok"])
            self.assertTrue(result["hits"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
