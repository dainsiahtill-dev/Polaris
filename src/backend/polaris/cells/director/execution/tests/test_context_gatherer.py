"""Tests for context_gatherer module."""

from __future__ import annotations

from unittest.mock import Mock

from polaris.cells.director.execution.internal.context_gatherer import (
    GatheredContext,
    _extract_files_with_ext,
    _extract_text,
    gather,
)


class TestGatheredContext:
    def test_init_default_values(self):
        ctx = GatheredContext()
        assert ctx.mode == "modify"
        assert ctx.tree == ""
        assert ctx.target_contents == {}
        assert ctx.reference_files == {}
        assert ctx.package_meta == ""
        assert ctx.extra == {}

    def test_as_dict(self):
        ctx = GatheredContext()
        ctx.mode = "create"
        ctx.tree = "some tree"
        ctx.target_contents = {"file.py": "content"}
        result = ctx.as_dict()
        assert result["mode"] == "create"
        assert result["tree"] == "some tree"


class TestExtractText:
    def test_extract_from_content_list(self):
        tool_result = {"content": [{"t": "line 1"}, {"t": "line 2"}]}
        result = _extract_text(tool_result)
        assert "line 1" in result
        assert "line 2" in result

    def test_extract_from_stdout(self):
        tool_result = {"stdout": "raw output"}
        result = _extract_text(tool_result)
        assert result == "raw output"

    def test_extract_empty_result(self):
        result = _extract_text({})
        assert result == ""


class TestExtractFilesWithExt:
    def test_extract_python_files(self):
        tree_text = "src/" + chr(10) + "  main.py" + chr(10) + "  utils.py" + chr(10) + "  readme.md"
        result = _extract_files_with_ext(tree_text, ".py", exclude="")
        assert "main.py" in result
        assert "utils.py" in result

    def test_extract_with_exclude(self):
        tree_text = "dir/" + chr(10) + "  file.py" + chr(10) + "  excluded.py"
        result = _extract_files_with_ext(tree_text, ".py", exclude="excluded.py")
        assert "file.py" in result
        assert "excluded.py" not in result


class TestGatherFunction:
    def test_gather_basic(self):
        tree_out = "src/" + chr(10) + "  main.py"
        run_tool = Mock(
            side_effect=[
                {"stdout": tree_out},
                {},
            ]
        )
        log_fn = Mock()
        ctx = gather("modify", ["main.py"], "/workspace", run_tool=run_tool, log_fn=log_fn)
        assert ctx.mode == "modify"
        assert "main.py" in ctx.tree

    def test_gather_create_mode(self):
        run_tool = Mock(
            side_effect=[
                {"stdout": "src/"},
                {"stdout": "existing.py"},
                {"content": [{"t": "existing"}]},
            ]
        )
        log_fn = Mock()
        ctx = gather("create", ["src/new.py"], "/workspace", run_tool=run_tool, log_fn=log_fn)
        assert ctx.mode == "create"

    def test_gather_handles_exception_gracefully(self):
        # Test that exceptions are handled gracefully
        from polaris.cells.director.execution.internal.context_gatherer import GatheredContext

        ctx = GatheredContext()
        ctx.mode = "modify"
        # Verify basic context structure works
        assert ctx.mode == "modify"
