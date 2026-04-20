import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "src" / "backend" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from pm.qa_auditor import normalize_qa_contract


def test_readme_task_defaults_to_generic_task_type():
    task = {
        "assigned_to": "Director",
        "title": "Create README documentation",
        "goal": "Draft README.md with prerequisites, installation, usage, and testing sections",
    }
    contract = normalize_qa_contract(None, task=task)
    assert contract["task_type"] == "generic"


def test_ui_terms_still_route_to_ui_canvas():
    task = {
        "assigned_to": "Director",
        "title": "Build frontend canvas screen",
        "goal": "Render UI components for dashboard",
    }
    contract = normalize_qa_contract(None, task=task)
    assert contract["task_type"] == "ui_canvas"


def test_three_arguments_phrase_does_not_route_to_ui_canvas():
    task = {
        "assigned_to": "Director",
        "title": "Implement CLI argument parser",
        "goal": "Parse three positional arguments for unit conversion",
    }
    contract = normalize_qa_contract(None, task=task)
    assert contract["task_type"] == "generic"


def test_threejs_scene_terms_route_to_ui_canvas():
    task = {
        "assigned_to": "Director",
        "title": "Render Three.js scene",
        "goal": "Build canvas renderer with WebGL pipeline",
    }
    contract = normalize_qa_contract(None, task=task)
    assert contract["task_type"] == "ui_canvas"
