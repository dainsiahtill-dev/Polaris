"""Tests for role-specific tool runtime integrations."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.llm.tool_runtime.public import ROLE_TOOL_INTEGRATIONS


def test_text_tool_protocol_fails_closed(tmp_path: Path) -> None:
    integration = ROLE_TOOL_INTEGRATIONS["director"](str(tmp_path))
    try:
        result = integration.process_llm_response("Need a file read.\n[READ_FILE]\npath = pyproject.toml\n[/READ_FILE]")
    finally:
        integration.close()

    assert result["has_tools"] is False
    assert result["tools_executed"] == []
    assert result["protocol_violation"] == "text_tool_protocol_disabled"


def test_director_text_tool_prompt_does_not_advertise_deprecated_precision_edit(tmp_path: Path) -> None:
    integration = ROLE_TOOL_INTEGRATIONS["director"](str(tmp_path))
    try:
        prompt = integration.get_system_prompt()
    finally:
        integration.close()

    assert "precision_edit" not in prompt
    assert "edit_blocks" in prompt
