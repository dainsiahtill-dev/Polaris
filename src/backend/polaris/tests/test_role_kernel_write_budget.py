from __future__ import annotations

from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor
from polaris.cells.roles.kernel.internal.output_parser import ToolCallResult


def _call(tool: str, **args):
    return ToolCallResult(tool=tool, args=args)


def test_director_default_write_budget_splits_excess_calls() -> None:
    calls = [
        _call("read_file", file="tui_runtime.md"),
        _call("write_file", file="a.py", content="a"),
        _call("write_file", file="b.py", content="b"),
        _call("search_replace", file="c.py", search="x", replace="y"),
        _call("write_file", file="d.py", content="d"),
        _call("edit_file", file="e.py", search="x", replace="z"),
    ]

    executable, deferred, limit = KernelToolExecutor.split_tool_calls_by_write_budget("director", calls)

    assert limit == 3
    assert [c.tool for c in executable] == [
        "read_file",
        "write_file",
        "write_file",
        "search_replace",
    ]
    assert [c.tool for c in deferred] == ["write_file", "edit_file"]


def test_role_write_budget_env_override(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_ROLE_WRITE_CALLS_PER_TURN_DIRECTOR", "1")
    calls = [
        _call("write_file", file="a.py", content="a"),
        _call("write_file", file="b.py", content="b"),
        _call("write_file", file="c.py", content="c"),
    ]

    executable, deferred, limit = KernelToolExecutor.split_tool_calls_by_write_budget("director", calls)

    assert limit == 1
    assert [c.tool for c in executable] == ["write_file"]
    assert [c.tool for c in deferred] == ["write_file", "write_file"]


def test_non_director_has_no_write_budget_by_default() -> None:
    calls = [
        _call("write_file", file="a.py", content="a"),
        _call("write_file", file="b.py", content="b"),
    ]

    executable, deferred, limit = KernelToolExecutor.split_tool_calls_by_write_budget("pm", calls)

    assert limit == 0
    assert len(executable) == 2
    assert deferred == []
