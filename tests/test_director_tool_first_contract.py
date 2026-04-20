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

from app.services.director_logic import write_gate_check  # noqa: E402
from core.polaris_loop.director_tooling.plan_parser import (  # noqa: E402
    extract_tool_plan,
    parse_tool_plan_item,
)
from core.polaris_loop.tool_contract import (  # noqa: E402
    canonicalize_tool_name,
    normalize_tool_args,
    validate_tool_step,
)


def test_tool_contract_canonicalizes_write_aliases() -> None:
    assert canonicalize_tool_name("apply_diff") == "repo_apply_diff"
    assert canonicalize_tool_name("search_replace") == "precision_edit"


def test_tool_contract_normalizes_write_args() -> None:
    args = normalize_tool_args(
        "apply_diff",
        {
            "path": "README.md",
            "search": "OLD",
            "replace": "NEW",
        },
    )
    assert args["file"] == "README.md"
    assert args["search"] == "OLD"
    assert args["replace"] == "NEW"


def test_tool_contract_validates_required_read_args() -> None:
    ok, code, _message = validate_tool_step("repo_search", {"path": "."})
    assert ok is False
    assert code == "INVALID_TOOL_ARGS"


def test_plan_parser_extracts_and_normalizes_tool_plan() -> None:
    payload = {
        "tool_plan": [
            {
                "tool": "repo_list_dir",
                "path": "src",
                "depth": 2,
            },
            {
                "tool": "repo_search",
                "query": "package.json tsconfig.json",
                "path": ".",
            },
        ]
    }
    steps = extract_tool_plan(payload)
    assert len(steps) == 2
    assert steps[0]["tool"] == "repo_tree"
    assert steps[0]["args"]["path"] == "src"
    assert steps[1]["tool"] == "repo_rg"
    assert "pattern" in steps[1]["args"]


def test_plan_parser_parses_cli_style_items() -> None:
    step = parse_tool_plan_item("repo_search query=main path=src")
    assert step is not None
    assert step["tool"] == "repo_rg"
    assert step["args"]["pattern"] == "main"
    assert step["args"]["paths"] == ["src"]


def test_write_gate_check_enforces_act_and_pm_scopes(monkeypatch) -> None:
    monkeypatch.delenv("POLARIS_SCOPE_GATE_MODE", raising=False)
    ok, reason = write_gate_check(
        changed_files=["src/app.py"],
        act_files=["src/app.py", "src/utils.py"],
        pm_target_files=["src"],
    )
    assert ok is True
    assert reason == ""

    ok, reason = write_gate_check(
        changed_files=["src/app.py", "docs/readme.md"],
        act_files=["src/app.py"],
        pm_target_files=["src"],
    )
    assert ok is True
    assert "scope expanded" in reason

    monkeypatch.setenv("POLARIS_SCOPE_GATE_MODE", "strict")
    ok, reason = write_gate_check(
        changed_files=["src/app.py", "scripts/build.py"],
        act_files=["src/app.py"],
        pm_target_files=["src"],
    )
    assert ok is False
    assert "act.files scope" in reason
