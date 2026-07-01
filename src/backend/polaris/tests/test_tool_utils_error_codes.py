"""Tool execution contract error code tests."""

from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("polaris.kernelone.tool_execution") is None:
    pytest.skip("Module not available: polaris.kernelone.tool_execution", allow_module_level=True)

from polaris.kernelone.tool_execution import validate_tool_step


def test_validate_tool_step_reports_missing_required_argument() -> None:
    ok, code, message = validate_tool_step("repo_read_head", {})

    assert ok is False
    assert code == "REQUIRED_MISSING"
    assert "missing required argument: file" in message
