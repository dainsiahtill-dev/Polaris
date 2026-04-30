import importlib.util
import sys
import unittest
from pathlib import Path


def _load_codex_utils():
    repo_root = Path(__file__).resolve().parents[1]
    module_dir = repo_root / "src" / "backend" / "core" / "polaris_loop"
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    module_path = module_dir / "codex_utils.py"
    spec = importlib.util.spec_from_file_location("codex_utils", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load codex_utils.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCodexUtils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codex_utils = _load_codex_utils()

    def test_build_codex_command(self):
        base_args = ["exec", "--cd", "."]
        ps1 = self.codex_utils.build_codex_command(base_args, "C:/bin/codex.ps1")
        self.assertEqual(ps1[:3], ["powershell", "-NoProfile", "-ExecutionPolicy"])
        cmd = self.codex_utils.build_codex_command(base_args, "C:/bin/codex.cmd")
        self.assertEqual(cmd[:2], ["cmd.exe", "/c"])
        exe = self.codex_utils.build_codex_command(base_args, "C:/bin/codex.exe")
        self.assertEqual(exe[0], "C:/bin/codex.exe")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
