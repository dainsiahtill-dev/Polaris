from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.retry_tool_definitions import (
    select_retry_forced_write_tool_name,
)
from polaris.cells.roles.kernel.internal.transaction.tool_sequence_templates import (
    build_recovery_protocol,
    build_sequence_template,
)


def _tool_definition(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


def test_general_mutation_template_does_not_rank_append_as_default_write_tool() -> None:
    template = build_sequence_template(
        required_tools=[],
        required_any_groups=[],
        ordered_tool_groups=[],
        min_tool_calls=1,
        requires_write=True,
        requires_verify=True,
    )

    assert "append_to_file > precision_edit" not in template
    assert "edit_blocks/edit_file/search_replace/repo_apply_diff" in template
    assert "write_file" in template
    assert "append_to_file only for explicit append-at-end tasks" in template
    assert "Step 1: read_file to confirm exact content" not in template
    assert "call write_file immediately" in template
    assert "A read/list/execute-only batch is invalid" in template


def test_edit_recovery_protocol_keeps_append_as_last_resort() -> None:
    protocol = build_recovery_protocol(
        required_tools=["precision_edit"],
        required_any_groups=[],
        available_write_tools=[
            "append_to_file",
            "precision_edit",
            "edit_file",
            "search_replace",
            "write_file",
        ],
    )

    assert "edit_file -> search_replace -> write_file -> precision_edit -> append_to_file" in protocol
    assert "append_to_file is only valid for explicit append-at-end tasks" in protocol


def test_retry_forced_write_prefers_robust_targeted_edit_over_precision_edit() -> None:
    tool_definitions = [
        _tool_definition("append_to_file"),
        _tool_definition("edit_file"),
        _tool_definition("precision_edit"),
    ]

    assert select_retry_forced_write_tool_name(tool_definitions) == "edit_file"


def test_retry_forced_write_uses_append_only_when_no_targeted_write_tool_exists() -> None:
    tool_definitions = [_tool_definition("read_file"), _tool_definition("append_to_file")]

    assert select_retry_forced_write_tool_name(tool_definitions) == "append_to_file"
