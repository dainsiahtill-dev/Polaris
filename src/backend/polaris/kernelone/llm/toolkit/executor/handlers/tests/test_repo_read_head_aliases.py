"""repo_read_head weak-model path alias compatibility."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.repo import _handle_repo_read_head


@pytest.mark.parametrize("alias", ["path", "filename", "target_file", "target_path"])
def test_repo_read_head_handler_accepts_file_aliases(tmp_path: Path, alias: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
    executor = AgentAccelToolExecutor(str(tmp_path))

    result = _handle_repo_read_head(executor, **{alias: "src/app.py", "limit": 2})

    assert result["ok"] is True
    assert result["file"] == "src/app.py"
    assert "line1" in result["content"]
    assert "line3" not in result["content"]
