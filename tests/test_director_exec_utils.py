from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
LOOP_CORE_ROOT = BACKEND_ROOT / "core" / "polaris_loop"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(LOOP_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_CORE_ROOT))

from core.polaris_loop.tool_contract import (  # noqa: E402
    canonicalize_tool_name,
    normalize_tool_args,
)
from domain.verification.impact_analyzer import assess_patch_risk  # noqa: E402


def test_tool_contract_normalizes_repo_search_args() -> None:
    args = normalize_tool_args(
        "repo_search",
        {
            "query": "package.json tsconfig.json",
            "path": ".",
            "max": 10,
        },
    )
    assert canonicalize_tool_name("repo_search") == "repo_rg"
    assert args["paths"] == "."
    assert args["max_results"] == 10
    assert "pattern" in args


def test_assess_patch_risk_reports_high_change_surface() -> None:
    changed = [f"src/module_{idx}.py" for idx in range(8)]
    risk = assess_patch_risk(changed, file_contents={path: "old content" for path in changed})
    assert isinstance(risk, dict)
    assert risk["total_files"] == 8
    assert risk["score"] >= 3
