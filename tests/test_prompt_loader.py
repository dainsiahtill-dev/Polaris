import os
import importlib.util
import unittest
from pathlib import Path


def _load_prompt_loader():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "core" / "polaris_loop" / "prompt_loader.py"
    spec = importlib.util.spec_from_file_location("prompt_loader", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load prompt_loader.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPromptLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt_loader = _load_prompt_loader()

    def test_current_profile_default(self):
        env_key = self.prompt_loader.PROFILE_ENV
        original = os.environ.pop(env_key, None)
        try:
            self.prompt_loader.load_profile.cache_clear()
            self.assertEqual(self.prompt_loader.current_profile(), "zhenguan_governance")
        finally:
            if original is not None:
                os.environ[env_key] = original
            self.prompt_loader.load_profile.cache_clear()

    def test_load_profile_fallback(self):
        env_key = self.prompt_loader.PROFILE_ENV
        original = os.environ.get(env_key)
        try:
            os.environ[env_key] = "does_not_exist"
            self.prompt_loader.load_profile.cache_clear()
            data = self.prompt_loader.load_profile()
            self.assertEqual(data.get("id"), "zhenguan_governance")
        finally:
            if original is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = original
            self.prompt_loader.load_profile.cache_clear()

    def test_render_template(self):
        rendered = self.prompt_loader.render_template("Hello {{name}}", {"name": "World"})
        self.assertEqual(rendered, "Hello World")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
