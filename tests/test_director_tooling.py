from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
LOOP_CORE_ROOT = BACKEND_ROOT / "core" / "polaris_loop"
for entry in (str(BACKEND_ROOT), str(LOOP_CORE_ROOT)):
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)

from core.polaris_loop.director_tooling.chain import normalize_tool_plan  # noqa: E402
from core.polaris_loop.director_tooling.cli_builder import build_tool_cli_args  # noqa: E402
from core.polaris_loop.director_tooling.output import annotate_rg_output  # noqa: E402
from core.polaris_loop.director_tooling.plan_parser import extract_tool_plan  # noqa: E402


def test_build_tool_cli_args_repo_rg() -> None:
    args = build_tool_cli_args(
        "repo_rg",
        {"pattern": "foo", "paths": ["."], "max_results": 5},
    )
    assert args[:2] == ["foo", "."]
    assert "--max" in args


def test_normalize_tool_plan_suggests_radius() -> None:
    plan = [{"tool": "repo_read_around", "args": {"file": "a.py", "line": 10, "radius": 40}}]
    history = {("a.py", 10): {"suggest_radius": 120, "start_line": 1, "end_line": 80}}
    normalized = normalize_tool_plan(plan, history, need_more_context_count=0)
    assert normalized[0]["args"]["radius"] == 120


def test_annotate_rg_output() -> None:
    output = {
        "pattern": "foo|bar",
        "hits": [
            {"file": "loops/x.py", "line": 1, "text": "def foo():"},
            {"file": "docs/readme.md", "line": 1, "text": "foo"},
        ],
    }
    annotate_rg_output(output)
    assert "ranked_hits" in output
    assert output["ranked_hits"][0]["score"] >= output["ranked_hits"][1]["score"]


def test_extract_tool_plan_parses_string_steps() -> None:
    payload = {
        "tool_plan": [
            "repo_rg -p \"foo\" src --max 5",
            "cat docs/agent/README.md",
        ]
    }
    steps = extract_tool_plan(payload)
    assert steps[0]["tool"] == "repo_rg"
    assert steps[0]["args"]["pattern"] == "foo"
    assert "src" in steps[0]["args"].get("paths", [])
    assert steps[1]["tool"] == "repo_read_head"


def test_extract_tool_plan_parses_equals_syntax() -> None:
    payload = {
        "tool_plan": [
            "repo_rg pattern=createServer paths=[src/] --max 5",
            "repo_read_head file=src/index.ts",
        ]
    }
    steps = extract_tool_plan(payload)
    assert steps[0]["tool"] == "repo_rg"
    assert steps[0]["args"]["pattern"] == "createServer"
    assert "src/" in steps[0]["args"].get("paths", [])
    assert steps[1]["tool"] == "repo_read_head"
    assert steps[1]["args"]["file"] == "src/index.ts"


def test_extract_tool_plan_alias_repo_ls() -> None:
    payload = {"tool_plan": ["repo_ls --recursive --include src/"]}
    steps = extract_tool_plan(payload)
    assert steps[0]["tool"] == "repo_tree"
    assert steps[0]["args"]["path"] == "src/"
    assert steps[0]["args"].get("depth") == 6
