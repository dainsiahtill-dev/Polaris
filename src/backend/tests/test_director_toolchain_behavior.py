"""Behavior tests for Director tool chain parsing and CLI building.

Covers parse_tool_chain_step, normalize_tool_plan, and build_tool_cli_args.
These are pure, synchronous functions with no I/O — ideal unit test targets.
"""
from __future__ import annotations

from polaris.kernelone.tool_execution.chain import (
    normalize_tool_plan,
    parse_tool_chain_step,
)
from polaris.kernelone.tool_execution.cli_builder import (
    build_tool_cli_args,
)


class TestParseToolChainStep:
    """parse_tool_chain_step normalizes raw plan steps into ToolChainStep."""

    def test_parses_basic_step(self) -> None:
        # repo_tree is in READ_ONLY_TOOLS, so it defaults to on_error=retry
        step = parse_tool_chain_step({"tool": "repo_tree", "args": {"path": "src"}})

        assert step.tool == "repo_tree"
        assert step.args == {"path": "src"}
        assert step.on_error == "retry"  # read-only tools default to retry
        assert step.max_retries == 2

    def test_parses_non_readonly_tool_defaults_to_stop(self) -> None:
        # Non-read-only tools like execute_command default to on_error=stop
        step = parse_tool_chain_step({"tool": "execute_command", "args": {"command": "echo hi"}})
        assert step.on_error == "stop"

    def test_parses_write_tool_stop_on_error(self) -> None:
        step = parse_tool_chain_step(
            {"tool": "write_file", "args": {"file": "out.txt", "content": "hi"}, "on_error": "stop"}
        )

        assert step.tool == "write_file"
        assert step.on_error == "stop"
        assert step.max_retries == 2  # max_retries still set, but on_error=stop means no retries

    def test_parses_step_with_chain_metadata(self) -> None:
        step = parse_tool_chain_step({
            "tool": "repo_read",
            "args": {"file": "a.txt"},
            "step_id": "s1",
            "save_as": "content_a",
            "input_from": None,
            "on_error": "continue",
            "max_retries": 3,
        })

        assert step.step_id == "s1"
        assert step.save_as == "content_a"
        assert step.input_from is None
        assert step.on_error == "continue"
        assert step.max_retries == 3

    def test_invalid_on_error_defaults_to_stop(self) -> None:
        step = parse_tool_chain_step({"tool": "write_file", "on_error": "foobar"})
        assert step.on_error == "stop"

    def test_ignores_non_dict_steps(self) -> None:
        # parse_tool_chain_step only processes dicts; non-dict items are filtered upstream
        # normalize_tool_plan skips non-dict entries, so this is safe to call directly
        step = parse_tool_chain_step({})
        assert step.tool == ""
        assert step.args == {}

    def test_max_retries_from_step(self) -> None:
        step = parse_tool_chain_step({"tool": "read_file", "max_retries": 5})
        assert step.max_retries == 5

    def test_safe_int_for_invalid_max_retries(self) -> None:
        step = parse_tool_chain_step({"tool": "read_file", "max_retries": "not_a_number"})
        assert step.max_retries == 2  # falls back to default


class TestNormalizeToolPlan:
    """normalize_tool_plan applies history-based radius suggestions."""

    def test_passes_through_valid_steps(self) -> None:
        plan = [
            {"tool": "repo_read", "args": {"file": "a.txt"}},
            {"tool": "repo_read", "args": {"file": "b.txt"}},
        ]
        result = normalize_tool_plan(plan, {}, 0)

        assert len(result) == 2
        assert result[0]["tool"] == "repo_read"
        assert result[1]["tool"] == "repo_read"

    def test_skips_non_dict_steps(self) -> None:
        plan = [
            {"tool": "repo_read", "args": {"file": "a.txt"}},
            "not a dict",
            None,
            {"tool": "write_file", "args": {"file": "out.txt", "content": ""}},
        ]
        result = normalize_tool_plan(plan, {}, 0)

        assert len(result) == 2

    def test_empty_plan_returns_empty(self) -> None:
        result = normalize_tool_plan([], {}, 0)
        assert result == []


class TestBuildToolCliArgs:
    """build_tool_cli_args converts argument dicts to CLI token lists."""

    def test_repo_tree_with_path(self) -> None:
        args = build_tool_cli_args("repo_tree", {"path": "src"})
        assert "src" in args

    def test_repo_tree_with_depth(self) -> None:
        args = build_tool_cli_args("repo_tree", {"path": ".", "depth": 3})
        assert "--depth" in args
        assert "3" in args

    def test_repo_rg_with_pattern(self) -> None:
        args = build_tool_cli_args("repo_rg", {"pattern": "TODO", "path": "src"})
        assert "TODO" in args

    def test_list_args_passed_through(self) -> None:
        args = build_tool_cli_args("unknown_tool", ["--flag", "value"])
        assert args == ["--flag", "value"]

    def test_none_args_uses_defaults(self) -> None:
        # None is normalised to {} so repo_tree falls back to default path "."
        args = build_tool_cli_args("repo_tree", None)
        assert args == ["."]

    def test_non_dict_args_returns_empty(self) -> None:
        args = build_tool_cli_args("repo_tree", "just a string")
        assert args == []

    def test_repo_read_around_args(self) -> None:
        args = build_tool_cli_args(
            "repo_read_around",
            {"file": "src/main.py", "line": 10, "radius": 5},
        )
        assert "src/main.py" in args
        assert "10" in args

    def test_repo_read_slice_args(self) -> None:
        args = build_tool_cli_args(
            "repo_read_slice",
            {"file": "src/main.py", "start": 5, "end": 15},
        )
        assert "src/main.py" in args
        assert "5" in args
        assert "15" in args

    def test_repo_diff_stat_mode_adds_flag(self) -> None:
        # _build_repo_diff_args only emits --stat when stat/mode is set
        args = build_tool_cli_args("repo_diff", {"stat": True})
        assert "--stat" in args

    def test_repo_diff_no_args_returns_empty(self) -> None:
        # repo_diff with no stat/mode args produces no CLI tokens
        args = build_tool_cli_args("repo_diff", {"path": "src", "cached": True})
        assert args == []
