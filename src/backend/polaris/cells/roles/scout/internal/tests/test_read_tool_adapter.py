"""Tests for RegistryReadTool (UTF-8).

The execution test is a REAL integration test: it runs an actual read tool via
``AgentAccelToolExecutor`` against a temp workspace and asserts a real hit.

Why ``repo_tree`` for the execution proof: in this environment the ``rg`` binary
is not on the subprocess PATH (``repo_rg`` returns ``backend=rg_unavailable``)
and ``repo_symbols_index`` is incompatible with the installed tree-sitter build.
``repo_tree`` is a pure-filesystem read tool that executes reliably and proves
the executor path + read-only gate + envelope normalization end to end.
"""

import pytest
from polaris.cells.roles.scout.internal.read_tool_adapter import ReadOnlyViolation, RegistryReadTool


def test_adapter_refuses_unknown_tool() -> None:
    adapter = RegistryReadTool(workspace=".")
    with pytest.raises(ReadOnlyViolation):
        adapter.run("definitely_not_a_tool", {})


def test_adapter_refuses_registered_write_tool() -> None:
    # apply_patch is registered but categorized as a write tool — the gate must reject it.
    adapter = RegistryReadTool(workspace=".")
    with pytest.raises(ReadOnlyViolation):
        adapter.run("apply_patch", {"patch": "x"})


def test_adapter_runs_real_read_tool_and_returns_hit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "pay.py").write_text("def payment_gateway():\n    return 1\n", encoding="utf-8")
    adapter = RegistryReadTool(workspace=str(tmp_path))
    try:
        out = adapter.run("repo_tree", {"path": ".", "depth": 2})
        assert out["ok"] is True
        # Envelope normalized: payload fields are top-level (no nested "result").
        entries = out.get("entries", [])
        paths = [str(e.get("path") or "") for e in entries]
        assert any(p.endswith("pay.py") for p in paths)
    finally:
        adapter.close()
