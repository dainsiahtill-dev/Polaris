"""Continuation guidance must describe actual available tools and observations."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import (
    _build_continue_visible_content,
    _extract_read_tools_from_receipt,
)


def test_exploration_guidance_uses_only_actual_available_content_read_tools() -> None:
    prompt = _build_continue_visible_content(
        [],
        current_progress="exploring",
        force_read_required=True,
        tool_definitions=[
            {"type": "function", "function": {"name": "repo_read_head"}},
            {"type": "function", "function": {"name": "repo_tree"}},
        ],
    )
    assert "repo_read_head" in prompt
    assert "read_file" not in prompt
    assert "尚未读取任何文件内容" not in prompt


def test_current_successful_read_overrides_stale_exploration_phase_description() -> None:
    prompt = _build_continue_visible_content(
        ["repo_read_head"],
        current_progress="exploring",
        force_read_required=True,
        modification_contract_status="ready",
        tool_definitions=[{"type": "function", "function": {"name": "repo_read_head"}}],
    )
    assert "尚未读取" not in prompt
    assert "必须立即调用" not in prompt
    assert "recent_reads" in prompt


def test_failed_read_and_prefix_named_nonread_are_not_successful_observations() -> None:
    receipt = {
        "results": [
            {"tool_name": "repo_read_head", "status": "error"},
            {"tool_name": "read_file_unregistered", "status": "success"},
            {"tool_name": "repo_read_slice", "status": "success"},
        ]
    }
    assert _extract_read_tools_from_receipt(receipt) == ["repo_read_slice"]


def test_writer_only_surface_does_not_demand_unavailable_read_tool() -> None:
    prompt = _build_continue_visible_content(
        [],
        current_progress="exploring",
        force_read_required=True,
        tool_definitions=[{"type": "function", "function": {"name": "write_file"}}],
    )
    assert "read_file" not in prompt
    assert "repo_read" not in prompt
    assert "读取目标文件" not in prompt
