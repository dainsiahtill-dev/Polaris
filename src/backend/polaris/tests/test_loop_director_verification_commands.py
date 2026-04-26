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

from core.polaris_loop.tool_contract import validate_tool_step  # noqa: E402
from domain.verification.write_gate import validate_write_scope  # noqa: E402


def test_validate_tool_step_accepts_repo_pytest_command_contract() -> None:
    ok, code, message = validate_tool_step("repo_read_head", {"file": "README.md", "n": 20})
    assert ok is True
    assert code is None
    assert message == ""


def test_validate_tool_step_rejects_missing_required_args() -> None:
    ok, code, _message = validate_tool_step("repo_rg", {"path": "."})
    assert ok is False
    assert code == "INVALID_TOOL_ARGS"


def test_validate_write_scope_blocks_out_of_scope_changes() -> None:
    result = validate_write_scope(
        changed_files=["src/main.py", "docs/README.md"],
        allowed_scope=["src/main.py"],
        workspace=".",
    )
    assert result.allowed is False
    assert "scope" in result.reason.lower()


def test_validate_write_scope_allows_exact_files() -> None:
    result = validate_write_scope(
        changed_files=["src/main.py"],
        allowed_scope=["src/main.py"],
        workspace=".",
    )
    assert result.allowed is True
