"""Regression tests for physical final-request tool authority."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from polaris.cells.roles.kernel.internal.llm_caller.final_request_tool_surface import (
    assert_tool_in_final_request_surface,
    final_request_allowed_tool_names,
)


def _tool(name: str) -> dict[str, object]:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def test_physical_forced_tool_overrides_broad_semantic_surface() -> None:
    prepared = SimpleNamespace(
        request_options={"tools": [_tool("read_file"), _tool("edit_file")]},
        native_tool_schemas=[_tool("read_file"), _tool("edit_file")],
    )
    active_request = SimpleNamespace(
        options={
            "tools": [_tool("edit_file")],
            "tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        }
    )

    assert final_request_allowed_tool_names(active_request=active_request, prepared=prepared) == {
        "edit_file"
    }
    assert_tool_in_final_request_surface(
        tool_name="edit_file",
        active_request=active_request,
        prepared=prepared,
    )

    with pytest.raises(
        RuntimeError,
        match=r"provider_tool_surface_violation: requested=read_file; allowed=edit_file",
    ):
        assert_tool_in_final_request_surface(
            tool_name="read_file",
            active_request=active_request,
            prepared=prepared,
        )


def test_tool_alias_is_normalized_before_authorization() -> None:
    prepared = SimpleNamespace(request_options={}, native_tool_schemas=[])
    active_request = SimpleNamespace(
        options={
            "tools": [_tool("edit_file")],
            "tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        }
    )

    assert_tool_in_final_request_surface(
        tool_name="edit-file",
        active_request=active_request,
        prepared=prepared,
    )


def test_no_final_tool_surface_rejects_provider_tool_call() -> None:
    prepared = SimpleNamespace(request_options={"tools": []}, native_tool_schemas=[])
    active_request = SimpleNamespace(options={"tools": []})

    with pytest.raises(
        RuntimeError,
        match=r"provider_tool_surface_violation: requested=read_file; allowed=<none>",
    ):
        assert_tool_in_final_request_surface(
            tool_name="read_file",
            active_request=active_request,
            prepared=prepared,
        )
