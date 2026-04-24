"""Tests for stream_orchestrator helper functions."""

import json

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import (
    ReadStrategyAdapter,
    _build_continue_visible_content,
    _detect_truncation_heuristics,
    _extract_read_tools_from_receipt,
    _resolve_continuation_delivery_contract,
    _should_use_slice_mode,
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
        # FIX-20250421: search_symbol 不是真正的文件读取工具（只是符号搜索）
        assert "search_symbol" not in result
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
        # FIX-20250421: grep 不是真正的文件读取工具（只是搜索）
        assert result == ["read_file"]

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
        content = _build_continue_visible_content(["read_file"], delivery_mode="materialize_changes")
        # 提取 JSON
        start = content.index("<SESSION_PATCH>") + len("<SESSION_PATCH>")
        end = content.index("</SESSION_PATCH>")
        json_str = content[start:end].strip()
        patch = json.loads(json_str)
        # FIX-20250421: task_progress 不再强制覆盖，保持当前阶段
        assert patch["delivery_mode"] == "materialize_changes"
        assert "recent_reads" in patch
        assert patch["recent_reads"] == ["read_file"]

    def test_empty_reads_omits_recent_reads(self):
        content = _build_continue_visible_content([])
        start = content.index("<SESSION_PATCH>") + len("<SESSION_PATCH>")
        end = content.index("</SESSION_PATCH>")
        patch = json.loads(content[start:end].strip())
        assert "recent_reads" not in patch

    def test_visible_content_has_system_hint(self):
        # FIX-20250422: 当 modification_contract_status 为 ready 时，提示执行写操作
        content = _build_continue_visible_content(["read_file"], modification_contract_status="ready")
        assert "多回合工作流继续" in content
        # FIX-20250421: 基于 PhaseManager 阶段生成提示语
        assert "CONTENT_GATHERED" in content
        assert "write_file/edit_file" in content

    def test_visible_content_carries_explicit_delivery_mode(self):
        content = _build_continue_visible_content(["read_file"], delivery_mode="materialize_changes")
        start = content.index("<SESSION_PATCH>") + len("<SESSION_PATCH>")
        end = content.index("</SESSION_PATCH>")
        patch = json.loads(content[start:end].strip())
        assert patch["delivery_mode"] == "materialize_changes"


class TestResolveContinuationDeliveryContract:
    def test_prefers_prompt_metadata_over_missing_ledger_state(self):
        raw_prompt = _build_continue_visible_content(
            ["read_file"],
            current_progress="exploring",
            delivery_mode="materialize_changes",
        )
        contract = _resolve_continuation_delivery_contract(
            raw_user=raw_prompt,
            original_delivery_mode=None,
            parsed_progress="exploring",
        )
        assert contract.mode == DeliveryMode.MATERIALIZE_CHANGES

    def test_falls_back_to_original_delivery_mode(self):
        raw_prompt = _build_continue_visible_content(["read_file"], current_progress="exploring")
        contract = _resolve_continuation_delivery_contract(
            raw_user=raw_prompt,
            original_delivery_mode="materialize_changes",
            parsed_progress="exploring",
        )
        assert contract.mode == DeliveryMode.MATERIALIZE_CHANGES


class TestShouldUseSliceMode:
    """测试 _should_use_slice_mode 函数。"""

    def test_small_file_returns_false(self):
        result = _should_use_slice_mode("test.py", 1024)
        assert result is False

    def test_large_file_returns_true(self):
        result = _should_use_slice_mode("test.py", 200 * 1024)
        assert result is True

    def test_exact_threshold_boundary(self):
        # 正好在阈值边界（100KB）
        result = _should_use_slice_mode("test.py", 100 * 1024)
        assert result is False

    def test_one_byte_over_threshold(self):
        result = _should_use_slice_mode("test.py", 100 * 1024 + 1)
        assert result is True


class TestDetectTruncationHeuristics:
    """测试 _detect_truncation_heuristics 函数。"""

    def test_no_truncation(self):
        result = _detect_truncation_heuristics("complete content")
        assert result is False

    def test_truncated_by_dots(self):
        result = _detect_truncation_heuristics("some content...")
        assert result is True

    def test_truncated_by_marker(self):
        result = _detect_truncation_heuristics("content [truncated]")
        assert result is True

    def test_truncated_by_metadata(self):
        result = _detect_truncation_heuristics("content", {"truncated": True})
        assert result is True

    def test_not_truncated_by_metadata(self):
        result = _detect_truncation_heuristics("content", {"truncated": False})
        assert result is False


class TestReadStrategyAdapter:
    """测试 ReadStrategyAdapter 类。"""

    def test_init(self):
        adapter = ReadStrategyAdapter()
        assert adapter.threshold_bytes == 100 * 1024

    def test_analyze_non_read_file_tool(self):
        adapter = ReadStrategyAdapter()
        result = adapter.analyze_tool_result("write_file", {"ok": True})
        assert result is None

    def test_analyze_normal_read_file(self):
        adapter = ReadStrategyAdapter()
        result = adapter.analyze_tool_result(
            "read_file", {"ok": True, "file": "test.py", "content": "small content", "truncated": False}
        )
        assert result is not None
        assert result.use_slice_mode is False

    def test_analyze_truncated_read_file(self):
        adapter = ReadStrategyAdapter()
        result = adapter.analyze_tool_result(
            "read_file", {"ok": True, "file": "test.py", "content": "content...", "truncated": True}
        )
        assert result is not None
        assert result.use_slice_mode is True

    def test_analyze_large_read_file(self):
        adapter = ReadStrategyAdapter()
        large_content = "x" * (200 * 1024)
        result = adapter.analyze_tool_result("read_file", {"ok": True, "file": "test.py", "content": large_content})
        assert result is not None
        assert result.use_slice_mode is True

    def test_build_slice_replacements(self):
        adapter = ReadStrategyAdapter()
        replacements = adapter.build_slice_replacements("test.py", total_lines=500, slice_size=200)
        assert len(replacements) == 3
        assert replacements[0]["tool_name"] == "repo_read_slice"
        assert replacements[0]["arguments"]["file"] == "test.py"
        assert replacements[0]["arguments"]["start"] == 1
        assert replacements[0]["arguments"]["end"] == 200
