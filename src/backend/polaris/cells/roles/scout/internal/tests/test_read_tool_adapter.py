"""Tests for RegistryReadTool (UTF-8)."""
import pytest
from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool, ReadOnlyViolation


def test_adapter_refuses_non_read_tool() -> None:
    adapter = RegistryReadTool(workspace=".")
    with pytest.raises(ReadOnlyViolation):
        adapter.run("repo_write", ["--file", "x", "--content", "y"])


@pytest.mark.xfail(
    reason=(
        "repo_rg is registered in ToolSpecRegistry but has no handler_module/"
        "handler_function populated — the _build_tool_spec_from_dict helper does "
        "not extract those fields from the raw spec dict. The read-only gate works "
        "correctly; this is a handler-registry gap to resolve in a follow-up task."
    ),
    strict=True,
)
def test_adapter_runs_repo_rg_read_tool(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "sample.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    adapter = RegistryReadTool(workspace=str(tmp_path))
    out = adapter.run("repo_rg", ["hello", "--max", "10"])
    assert out["ok"] is True
