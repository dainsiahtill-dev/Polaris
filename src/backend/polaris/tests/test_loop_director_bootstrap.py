from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from domain.verification.existence_gate import check_mode  # noqa: E402


def _load_loop_director():
    module_path = BACKEND_ROOT / "scripts" / "loop-director.py"
    spec = importlib.util.spec_from_file_location("loop_director_bootstrap", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-director.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existence_gate_detects_create_mode_when_targets_missing(tmp_path: Path) -> None:
    result = check_mode(["apps/server/index.ts", "apps/server/package.json"], str(tmp_path))
    assert result.mode == "create"
    assert result.all_missing is True


def test_existence_gate_detects_modify_mode_when_targets_exist(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    result = check_mode(["src/main.py"], str(tmp_path))
    assert result.mode == "modify"
    assert result.all_exist is True


def test_extract_pm_tasks_preserves_metadata() -> None:
    loop_director = _load_loop_director()
    contract = {
        "tasks": [
            {
                "id": "PM-0001-F1",
                "title": "Requirements bootstrap (Rust Api)",
                "goal": "Create initial files using Rust conventions.",
                "description": "Technology Stack: Rust",
                "priority": 1,
                "metadata": {
                    "detected_language": "rust",
                    "project_type": "api",
                },
            }
        ]
    }
    extracted = loop_director.extract_pm_tasks(contract)
    assert len(extracted) == 1
    metadata = extracted[0].get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("detected_language") == "rust"
