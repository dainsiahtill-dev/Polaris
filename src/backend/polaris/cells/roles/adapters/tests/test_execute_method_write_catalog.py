"""Regression tests for Director execute-method write-tool diagnostics."""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director import execute_method
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS


def test_diag_write_tool_names_use_canonical_catalog() -> None:
    assert execute_method._DIAG_WRITE_TOOL_NAMES == WRITE_TOOLS

