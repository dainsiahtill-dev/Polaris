"""Tests for stream_orchestrator helper functions."""

import json

from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import (
    _build_continue_visible_content,
    _extract_read_tools_from_receipt,
)


class TestExtractReadToolsFromReceipt:
    """测试 _extract_read_tools_from_receipt。"""

    def test_empty_receipt_returns_empty(self):
        assert _extract_read_tools_from_receipt(None) == []
        assert _extract_read_tools_from_receipt({}) == []

    def test_extracts_read_tools_only(self):
        receipt = {
            "results": [
                {"tool_name": "read_file"},
                {"tool_name": "edit_file"},
                {"tool_name": "repo_read_head"},
                {"tool_name": "write_file"},
                {"tool_name": "search_symbol"},
            ]
        }
        result = _extract_read_tools_from_receipt(receipt)
        assert "read_file" in result
        assert "repo_read_head" in result
        assert "search_symbol" in result
        assert "edit_file" not in result
        assert "write_file" not in result

    def test_deduplicates_tools(self):
        receipt = {
            "raw_results": [
                {"tool_name": "read_file"},
                {"tool_name": "read_file"},
                {"tool_name": "grep"},
            ]
        }
        result = _extract_read_tools_from_receipt(receipt)
        assert result.count("read_file") == 1
        assert result == ["read_file", "grep"]

    def test_ignores_empty_tool_names(self):
        receipt = {
            "results": [
                {"tool_name": ""},
                {"tool_name": "   "},
                {"tool_name": "read_file"},
            ]
        }
        assert _extract_read_tools_from_receipt(receipt) == ["read_file"]


class TestBuildContinueVisibleContent:
    """测试 _build_continue_visible_content。"""

    def test_contains_session_patch_block(self):
        content = _build_continue_visible_content(["read_file", "repo_read_head"])
        assert "<SESSION_PATCH>" in content
        assert "</SESSION_PATCH>" in content

    def test_session_patch_json_valid(self):
        content = _build_continue_visible_content(["read_file"])
        # 提取 JSON
        start = content.index("<SESSION_PATCH>") + len("<SESSION_PATCH>")
        end = content.index("</SESSION_PATCH>")
        json_str = content[start:end].strip()
        patch = json.loads(json_str)
        assert patch["task_progress"] == "implementing"
        assert patch["recent_reads"] == ["read_file"]
        assert "写工具" in patch["instruction"]

    def test_empty_reads_omits_recent_reads(self):
        content = _build_continue_visible_content([])
        start = content.index("<SESSION_PATCH>") + len("<SESSION_PATCH>")
        end = content.index("</SESSION_PATCH>")
        patch = json.loads(content[start:end].strip())
        assert "recent_reads" not in patch

    def test_visible_content_has_system_hint(self):
        content = _build_continue_visible_content(["read_file"])
        assert "多回合工作流继续" in content
        assert "读阶段已完成" in content
        assert "进入写阶段" in content
