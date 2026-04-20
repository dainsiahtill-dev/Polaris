"""
tests/test_context_gatherer.py — Unit tests for context_gatherer.gather().

Uses stubbed run_tool callables — no real filesystem tool execution needed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "core", "polaris_loop"),
)

from polaris.cells.director.execution.internal.context_gatherer import GatheredContext, gather

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _make_run_tool(responses: dict) -> callable:
    """Return a run_tool stub that returns responses[tool_name] or {}."""
    def _run(tool_name: str, **kwargs):
        return responses.get(tool_name, {})
    return _run


TREE_RESULT = {"stdout": "src/\n  index.ts\n  utils.ts\npackage.json\n"}
PACKAGE_RESULT = {"content": [{"t": '{"name": "my-app"}'}, {"t": '"version": "1.0.0"'}]}
FILE_RESULT = {"content": [{"t": "export function hello() {"}, {"t": "  return 42;"}, {"t": "}"}]}


# ---------------------------------------------------------------------------
# CREATE mode
# ---------------------------------------------------------------------------

class TestGatherCreate:
    def test_returns_gathered_context(self, tmp_path):
        ws = str(tmp_path)
        run_tool = _make_run_tool({
            "repo_tree": TREE_RESULT,
            "repo_read_head": PACKAGE_RESULT,
        })
        ctx = gather("create", ["src/new.ts"], ws, run_tool=run_tool)
        assert isinstance(ctx, GatheredContext)
        assert ctx.mode == "create"
        assert ctx.tree != ""

    def test_package_meta_extracted(self, tmp_path):
        ws = str(tmp_path)
        pkg_json = tmp_path / "package.json"
        pkg_json.write_text('{"name": "test"}')
        run_tool = _make_run_tool({
            "repo_tree": TREE_RESULT,
            "repo_read_head": PACKAGE_RESULT,
        })
        ctx = gather("create", ["src/new.ts"], ws, run_tool=run_tool)
        assert ctx.package_meta != ""

    def test_as_dict_is_serialisable(self, tmp_path):
        ws = str(tmp_path)
        run_tool = _make_run_tool({"repo_tree": TREE_RESULT})
        ctx = gather("create", ["src/new.ts"], ws, run_tool=run_tool)
        d = ctx.as_dict()
        assert "mode" in d
        assert "tree" in d
        assert "target_contents" in d
        assert "reference_files" in d


# ---------------------------------------------------------------------------
# MODIFY mode
# ---------------------------------------------------------------------------

class TestGatherModify:
    def test_reads_existing_target_files(self, tmp_path):
        ws = str(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "existing.ts").write_text("export const x = 1;")
        run_tool = _make_run_tool({
            "repo_tree": TREE_RESULT,
            "repo_read_head": FILE_RESULT,
        })
        ctx = gather("modify", ["src/existing.ts"], ws, run_tool=run_tool)
        assert ctx.mode == "modify"
        assert "src/existing.ts" in ctx.target_contents

    def test_missing_file_in_modify_noted_as_to_create(self, tmp_path):
        ws = str(tmp_path)
        run_tool = _make_run_tool({
            "repo_tree": TREE_RESULT,
            "repo_read_head": {},
        })
        ctx = gather("modify", ["src/missing.ts"], ws, run_tool=run_tool)
        assert "files_to_create" in ctx.extra
        assert "src/missing.ts" in ctx.extra["files_to_create"]


# ---------------------------------------------------------------------------
# Tool failure resilience
# ---------------------------------------------------------------------------

class TestGatherResilient:
    def test_tree_failure_does_not_crash(self, tmp_path):
        ws = str(tmp_path)
        def _failing_run(tool_name, **kwargs):
            raise RuntimeError("tool unavailable")
        ctx = gather("create", ["src/new.ts"], ws, run_tool=_failing_run)
        assert ctx is not None
        assert ctx.tree == ""

    def test_no_targets_returns_empty_context(self, tmp_path):
        ws = str(tmp_path)
        run_tool = _make_run_tool({"repo_tree": TREE_RESULT})
        ctx = gather("modify", [], ws, run_tool=run_tool)
        assert ctx.target_contents == {}
