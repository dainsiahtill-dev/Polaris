from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.director_logic import extract_required_evidence  # noqa: E402


def test_extract_required_evidence_prefers_top_level() -> None:
    payload = {
        "required_evidence": {"validation_paths": ["top.md"]},
        "tasks": [
            {"id": "A", "required_evidence": {"validation_paths": ["task-a.md"]}},
        ],
    }
    required = extract_required_evidence(payload)
    assert required.get("validation_paths") == ["top.md"]


def test_extract_required_evidence_falls_back_to_task_level() -> None:
    payload = {
        "tasks": [
            {"id": "A", "required_evidence": {"validation_paths": ["task-a.md"]}},
        ],
    }
    required = extract_required_evidence(payload)
    assert required.get("validation_paths") == ["task-a.md"]


def test_extract_required_evidence_handles_missing_payload() -> None:
    assert extract_required_evidence(None) == {}
