"""Regression tests for filesystem write-tool guidance."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _handle_write_file


def test_edit_fragment_write_guidance_uses_active_edit_tools(tmp_path: Path) -> None:
    executor = AgentAccelToolExecutor(workspace=str(tmp_path))

    result = _handle_write_file(
        executor,
        file="src/main.py",
        content="在第 3 行之后添加日志输出",
    )

    assert result.get("ok") is False
    assert result.get("error_type") == "edit_fragment_write"
    error = str(result.get("error") or "")
    assert "edit_blocks/edit_file" in error
    assert "precision_edit" not in error

