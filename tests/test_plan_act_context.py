from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
LOOP_CORE_ROOT = BACKEND_ROOT / "core" / "polaris_loop"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(LOOP_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_CORE_ROOT))

from app.services.director_logic import compact_pm_payload  # noqa: E402
from core.polaris_loop.director_tooling.plan_parser import (  # noqa: E402
    extract_tool_budget,
    extract_tool_plan,
)


def test_compact_pm_payload_respects_max_chars() -> None:
    pm_payload = {
        "overall_goal": "G" * 500,
        "focus": "F" * 400,
        "notes": "N" * 400,
        "tasks": [
            {
                "id": f"PM-{idx}",
                "title": "T" * 200,
                "goal": "G" * 200,
                "target_files": [f"file_{i}.py" for i in range(20)],
                "context_files": [f"ctx_{i}.py" for i in range(20)],
                "constraints": ["C" * 80 for _ in range(10)],
                "acceptance": ["A" * 80 for _ in range(10)],
            }
            for idx in range(5)
        ],
    }
    max_chars = 160
    compact = compact_pm_payload(pm_payload, max_chars)
    serialized = json.dumps(compact, ensure_ascii=False)
    assert len(serialized) <= max_chars + 20


def test_extract_tool_plan_and_budget_from_payload() -> None:
    payload = {
        "tool_plan": [
            {"tool": "repo_search", "query": "main", "path": "src"},
            {"tool": "repo_read_head", "file": "README.md", "n": 50},
        ],
        "budget": {"max_rounds": 3, "max_total_lines": 1200},
    }
    plan = extract_tool_plan(payload)
    rounds, lines = extract_tool_budget(payload, default_rounds=6, default_lines=800)

    assert len(plan) == 2
    assert plan[0]["tool"] == "repo_rg"
    assert plan[0]["args"]["pattern"] == "main"
    assert rounds == 3
    assert lines == 1200
