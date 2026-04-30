import importlib.util
import json
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


REQUIRED_TEMPLATES = {
    "pm_prompt",
    "planner_prompt",
    "tool_planner_prompt",
    "patch_planner_prompt",
    "qa_prompt",
    "reviewer_prompt",
    "ollama_prompt",
}


class TestPromptTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt_loader = _load_prompt_loader()

    def test_profiles_have_required_templates(self):
        repo_root = Path(__file__).resolve().parents[1]
        prompts_dir = repo_root / "prompts"
        for path in [prompts_dir / "zhenguan_governance.json", prompts_dir / "generic.json", prompts_dir / "demo_ming_armada.json"]:
            data = json.loads(path.read_text(encoding="utf-8"))
            templates = data.get("templates")
            self.assertIsInstance(templates, dict)
            for key in REQUIRED_TEMPLATES:
                self.assertIn(key, templates)
            self.assertIn("plan_template", data)

    def test_patch_planner_prompt_contains_plan_act(self):
        repo_root = Path(__file__).resolve().parents[1]
        prompts_dir = repo_root / "prompts"
        data = json.loads((prompts_dir / "zhenguan_governance.json").read_text(encoding="utf-8"))
        prompt = data["templates"]["patch_planner_prompt"]
        self.assertTrue("\"plan\"" in prompt or "\\\"plan\\\"" in prompt)
        self.assertTrue("\"act\"" in prompt or "\\\"act\\\"" in prompt)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
