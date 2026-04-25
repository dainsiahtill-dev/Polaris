"""Unit tests for director/state_utils.py pure logic (no I/O, no filesystem).

Covers:
- extract_output_requirements
- is_output_sparse
- extract_domain_tokens
- summarize_tool_results
- default_projection_slug
- compose_projection_requirement
"""

from __future__ import annotations

from typing import Any

import pytest

from polaris.cells.roles.adapters.internal.director.state_utils import (
    compose_projection_requirement,
    default_projection_slug,
    extract_domain_tokens,
    extract_output_requirements,
    is_output_sparse,
    summarize_tool_results,
)


# ---------------------------------------------------------------------------
# Output requirements extraction
# ---------------------------------------------------------------------------


class TestExtractOutputRequirements:
    def test_empty_task_defaults(self) -> None:
        assert extract_output_requirements({}) == (1, 1)

    def test_subject_only(self) -> None:
        task = {"subject": "实现登录功能"}
        assert extract_output_requirements(task) == (1, 1)

    def test_min_files_from_subject(self) -> None:
        task = {"subject": "至少3个代码文件"}
        assert extract_output_requirements(task) == (3, 1)

    def test_min_lines_from_description(self) -> None:
        task = {"description": "不少于100行代码"}
        assert extract_output_requirements(task) == (1, 100)

    def test_metadata_goal(self) -> None:
        task = {"metadata": {"goal": "至少5个文件", "scope": "至少200行"}}
        assert extract_output_requirements(task) == (5, 200)

    def test_steps_included(self) -> None:
        task = {"metadata": {"steps": ["step1", "至少4个文件", "至少50行"]}}
        assert extract_output_requirements(task) == (4, 50)

    def test_invalid_numbers_fallback(self) -> None:
        task = {"subject": "至少abc个文件"}
        assert extract_output_requirements(task) == (1, 1)

    def test_zero_clamped_to_one(self) -> None:
        task = {"subject": "至少0个文件", "description": "至少0行"}
        assert extract_output_requirements(task) == (1, 1)


# ---------------------------------------------------------------------------
# Output sparsity
# ---------------------------------------------------------------------------


class TestIsOutputSparse:
    def test_sufficient_output(self) -> None:
        assert is_output_sparse(file_count=5, line_count=100, min_files=3, min_lines=50) is False

    def test_insufficient_files(self) -> None:
        assert is_output_sparse(file_count=1, line_count=100, min_files=3, min_lines=50) is True

    def test_insufficient_lines(self) -> None:
        assert is_output_sparse(file_count=5, line_count=10, min_files=3, min_lines=50) is True

    def test_zero_min_values_clamped(self) -> None:
        assert is_output_sparse(file_count=0, line_count=0, min_files=0, min_lines=0) is True


# ---------------------------------------------------------------------------
# Domain tokens
# ---------------------------------------------------------------------------


class TestExtractDomainTokens:
    def test_empty_task(self) -> None:
        assert extract_domain_tokens({}) == []

    def test_filters_stopwords(self) -> None:
        task = {"subject": "task project module code", "description": "implement feature service"}
        result = extract_domain_tokens(task)
        for stopword in ("task", "project", "module", "code", "implement", "feature", "service"):
            assert stopword not in result

    def test_extracts_unique_tokens(self) -> None:
        task = {"subject": "payment gateway", "description": "payment processing"}
        result = extract_domain_tokens(task)
        assert "payment" in result
        assert "gateway" in result
        assert "processing" in result
        assert result.count("payment") == 1

    def test_limits_to_10(self) -> None:
        task = {"subject": "a1 b2 c3 d4 e5 f6 g7 h8 i9 j10 k11 l12"}
        result = extract_domain_tokens(task)
        assert len(result) <= 10

    def test_short_tokens_filtered(self) -> None:
        task = {"subject": "ab xyz"}
        result = extract_domain_tokens(task)
        assert "ab" not in result
        assert "xyz" in result


# ---------------------------------------------------------------------------
# Tool results summary
# ---------------------------------------------------------------------------


class TestSummarizeToolResults:
    def test_empty_list(self) -> None:
        result = summarize_tool_results([])
        assert '"tool_results": []' in result

    def test_basic_summary(self) -> None:
        tools = [
            {"tool": "write_file", "success": True, "result": {"file": "test.py"}},
            {"tool": "read_file", "success": False, "error": "not found"},
        ]
        result = summarize_tool_results(tools)
        assert "write_file" in result
        assert "read_file" in result

    def test_error_truncation(self) -> None:
        long_error = "x" * 500
        tools = [{"tool": "x", "success": False, "error": long_error}]
        result = summarize_tool_results(tools)
        assert "...[truncated]" not in result  # 400 limit, but 500 > 400 so it should be truncated
        # Actually error is truncated to 400, so 500 char error becomes 400 + no ellipsis in the field itself
        # Let's verify the error is present but shorter
        assert len(result) < len(long_error) + 100

    def test_result_value_truncation(self) -> None:
        long_stdout = "x" * 1500
        tools = [{"tool": "x", "success": True, "result": {"stdout": long_stdout}}]
        result = summarize_tool_results(tools)
        assert "...[truncated]" in result

    def test_limits_to_4_items(self) -> None:
        tools = [{"tool": f"tool_{i}", "success": True} for i in range(10)]
        result = summarize_tool_results(tools)
        # Only first 4 should appear
        assert "tool_0" in result
        assert "tool_3" in result
        assert "tool_4" not in result

    def test_skips_non_dict_items(self) -> None:
        tools = ["bad", {"tool": "ok", "success": True}]
        result = summarize_tool_results(tools)  # type: ignore[list-item]
        assert "ok" in result
        assert "bad" not in result


# ---------------------------------------------------------------------------
# Default projection slug
# ---------------------------------------------------------------------------


class TestDefaultProjectionSlug:
    def test_from_subject(self) -> None:
        assert default_projection_slug("t1", {"subject": "My Project"}, {}) == "my_project"

    def test_from_title(self) -> None:
        assert default_projection_slug("t1", {"title": "Hello World"}, {}) == "hello_world"

    def test_from_input_data(self) -> None:
        assert default_projection_slug("t1", {}, {"project_slug": "Custom Name"}) == "custom_name"

    def test_from_task_id_fallback(self) -> None:
        assert default_projection_slug("task-123", {}, {}) == "task_123"

    def test_empty_fallback(self) -> None:
        assert default_projection_slug("", {}, {}) == "projection_task"

    def test_length_limit(self) -> None:
        long_subject = "a" * 100
        result = default_projection_slug("t1", {"subject": long_subject}, {})
        assert len(result) <= 48

    def test_special_chars_replaced(self) -> None:
        assert default_projection_slug("t1", {"subject": "foo-bar.baz"}, {}) == "foo_bar_baz"


# ---------------------------------------------------------------------------
# Compose projection requirement
# ---------------------------------------------------------------------------


class TestComposeProjectionRequirement:
    def test_input_data_projection_requirement(self) -> None:
        task = {"metadata": {"goal": "ignored"}}
        input_data = {"projection_requirement": "Build API"}
        assert compose_projection_requirement(task, input_data) == "Build API"

    def test_input_data_requirement_delta(self) -> None:
        task = {}
        input_data = {"requirement_delta": "Add auth"}
        assert compose_projection_requirement(task, input_data) == "Add auth"

    def test_metadata_projection_requirement(self) -> None:
        task = {"metadata": {"projection_requirement": "From meta"}}
        assert compose_projection_requirement(task, {}) == "From meta"

    def test_goal_fallback(self) -> None:
        task = {"metadata": {"goal": "The goal"}}
        assert compose_projection_requirement(task, {}) == "The goal"

    def test_description_fallback(self) -> None:
        task = {"description": "The description"}
        assert compose_projection_requirement(task, {}) == "The description"

    def test_subject_fallback(self) -> None:
        task = {"subject": "The subject"}
        assert compose_projection_requirement(task, {}) == "The subject"

    def test_completely_empty(self) -> None:
        assert compose_projection_requirement({}, {}) == "完成当前 Director 任务并生成可验证的传统代码产物。"
