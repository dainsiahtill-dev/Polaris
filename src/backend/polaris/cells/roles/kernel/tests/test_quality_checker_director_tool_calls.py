"""Test suite for `roles.kernel` QualityChecker Director output validation.

Covers:
- QualityChecker._validate_director_output() — unified parser + legacy + markdown extraction
- QualityChecker._extract_director_patches() — multi-dialect patch extraction
- QualityChecker._is_safe_relative_path() — path safety enforcement
- QualityChecker._extract_tool_calls() — tool call tag parsing
- QualityChecker._validate_architect_output() — architect section validation
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.quality_checker import (
    QualityChecker,
    QualityResult,
)


class TestValidateDirectorOutput:
    """_validate_director_output delegates to multi-dialect patch + tool-call extraction."""

    def test_extracts_json_data(self) -> None:
        checker = QualityChecker()
        # _extract_json requires fenced ```json ... ``` blocks
        content = '```json\n{"result": "ok"}\n```'
        data, errors = checker._validate_director_output(content)
        assert data is not None
        assert data["result"] == "ok"
        assert errors == []

    def test_no_json_no_patches_returns_error(self) -> None:
        checker = QualityChecker()
        content = "Just some plain text without any patches or JSON."
        data, errors = checker._validate_director_output(content)
        assert data is None
        assert len(errors) > 0
        assert "未找到有效的JSON或补丁" in errors

    def test_empty_content_returns_error(self) -> None:
        checker = QualityChecker()
        data, errors = checker._validate_director_output("")
        assert data is None
        assert len(errors) > 0

    def test_whitespace_only_returns_error(self) -> None:
        checker = QualityChecker()
        data, errors = checker._validate_director_output("   \n\t  ")
        assert data is None
        assert len(errors) > 0


class TestExtractDirectorPatches:
    """_extract_director_patches combines unified parser + legacy + markdown."""

    def test_extracts_unified_search_replace(self) -> None:
        checker = QualityChecker()
        content = """<<<<<<< SEARCH
old_line = 1
=======
new_line = 2
>>>>>>> REPLACE"""
        patches = checker._extract_director_patches(content)
        assert len(patches) >= 1

    def test_extracts_legacy_search_replace(self) -> None:
        checker = QualityChecker()
        content = """<<<<<<< SEARCH
def hello():
    pass
=======
def hello():
    return "hi"
>>>>>>> REPLACE"""
        patches = checker._extract_director_patches(content)
        assert any(p.get("search") for p in patches)
        assert any(p.get("replace") for p in patches)

    def test_extracts_markdown_file_blocks(self) -> None:
        checker = QualityChecker()
        content = "# src/main.py\n\n```python\ndef main():\n    pass\n```\n"
        patches = checker._extract_director_patches(content)
        assert len(patches) >= 1
        assert patches[0].get("file") == "src/main.py"

    def test_empty_content_returns_empty_list(self) -> None:
        checker = QualityChecker()
        patches = checker._extract_director_patches("")
        assert patches == []

    def test_no_patches_returns_empty_list(self) -> None:
        checker = QualityChecker()
        patches = checker._extract_director_patches("No patch content here")
        assert patches == []


class TestIsSafeRelativePath:
    """_is_safe_relative_path blocks absolute, traversal, and null-byte paths."""

    def test_relative_path_is_safe(self) -> None:
        assert QualityChecker._is_safe_relative_path("src/main.py") is True
        assert QualityChecker._is_safe_relative_path("polaris/kernelone/llm/engine.py") is True
        assert QualityChecker._is_safe_relative_path("a/b/c/d.py") is True

    def test_unix_absolute_is_unsafe(self) -> None:
        assert QualityChecker._is_safe_relative_path("/etc/passwd") is False
        assert QualityChecker._is_safe_relative_path("/workspace/file.py") is False

    def test_windows_absolute_is_unsafe(self) -> None:
        assert QualityChecker._is_safe_relative_path("C:\\Windows\\System32") is False
        assert QualityChecker._is_safe_relative_path("D:\\secrets\\key") is False

    def test_parent_traversal_is_unsafe(self) -> None:
        assert QualityChecker._is_safe_relative_path("../secrets") is False
        assert QualityChecker._is_safe_relative_path("src/../etc/passwd") is False
        assert QualityChecker._is_safe_relative_path("foo/../bar") is False

    def test_null_byte_is_unsafe(self) -> None:
        assert QualityChecker._is_safe_relative_path("safe\x00file") is False

    def test_empty_is_unsafe(self) -> None:
        assert QualityChecker._is_safe_relative_path("") is False
        assert QualityChecker._is_safe_relative_path("   ") is False

    def test_backslash_normalized_to_forward_slash(self) -> None:
        # Backslash is normalized to forward slash; src\\main.py -> src/main.py -> safe
        assert QualityChecker._is_safe_relative_path("src\\main.py") is True

    def test_patch_target_heuristic_filters_plain_words(self) -> None:
        assert QualityChecker._looks_like_patch_target("pass") is False
        assert QualityChecker._looks_like_patch_target("src/main.py") is True
        assert QualityChecker._looks_like_patch_target("Dockerfile") is True


class TestExtractToolCalls:
    """_extract_tool_calls parses tool call tags from text."""

    def test_no_tool_calls_returns_empty(self) -> None:
        checker = QualityChecker()
        calls = checker._extract_tool_calls("Just plain text")
        assert calls == []

    def test_empty_content_returns_empty(self) -> None:
        checker = QualityChecker()
        calls = checker._extract_tool_calls("")
        assert calls == []


class TestValidateArchitectOutput:
    """_validate_architect_output checks required sections."""

    def test_all_sections_present_returns_no_errors(self) -> None:
        checker = QualityChecker()
        content = "## 架构\n\nSome architecture.\n## 技术栈\n\nPython.\n## 模块\n\nModule A."
        data, errors = checker._validate_architect_output(content)
        assert data is not None
        assert errors == []

    def test_missing_section_returns_error(self) -> None:
        checker = QualityChecker()
        content = "## 架构\n\nSome architecture.\n## 技术栈\n\nPython."
        _data, errors = checker._validate_architect_output(content)
        assert errors != []
        assert "缺少章节" in errors[0]

    def test_empty_content_returns_error(self) -> None:
        checker = QualityChecker()
        _data, errors = checker._validate_architect_output("")
        assert errors != []


class TestQualityCheckerValidateOutput:
    """Full validate_output() method across roles."""

    def test_director_role_uses_validate_director_output(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "director"

        # Director uses _validate_director_output which requires fenced JSON
        content = '```json\n{"result": "ok"}\n```'
        result = checker.validate_output(content, MockProfile())  # type: ignore[arg-type]
        assert isinstance(result, QualityResult)
        # _validate_director_output extracts JSON successfully
        assert result.data is not None

    def test_security_block_bypasses_validation(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "director"

        content = "该请求超出我的职责范围或违反安全策略"
        result = checker.validate_output(content, MockProfile())  # type: ignore[arg-type]
        assert result.success is True
        assert result.data is not None
        assert result.data.get("security_blocked") is True

    def test_pm_role_requires_json(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "pm"

        result = checker.validate_output("not json", MockProfile())  # type: ignore[arg-type]
        assert result.success is False
        # Parse failures appear in suggestions, not errors list
        assert len(result.suggestions) > 0

    def test_qa_role_requires_json(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "qa"

        result = checker.validate_output("not json", MockProfile())  # type: ignore[arg-type]
        assert result.success is False

    def test_unknown_role_defaults_to_text(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "unknown_role"

        result = checker.validate_output("some text", MockProfile())  # type: ignore[arg-type]
        assert result.data is not None
        assert "text" in result.data


class TestCheckDirectorQuality:
    """_check_director_quality enforces patch format and security."""

    def test_has_tool_calls_early_return(self) -> None:
        checker = QualityChecker()
        score, _suggestions = checker._check_director_quality(
            "some text",
            {"tool_calls": [{"name": "read_file", "arguments": {}}]},
        )
        assert score == 100.0

    def test_no_patches_or_tool_calls_penalized(self) -> None:
        checker = QualityChecker()
        score, suggestions = checker._check_director_quality(
            "just plain text without any markers",
            {},
        )
        assert score < 100.0
        assert len(suggestions) > 0

    def test_mismatched_search_replace_blocks_penalized(self) -> None:
        checker = QualityChecker()
        content = "<<<<<<< SEARCH\na\n>>>>>>> REPLACE\n<<<<<<< SEARCH\nb\n>>>>>>> REPLACE\n<<<<<<< SEARCH\nc\n"
        score, suggestions = checker._check_director_quality(content, {})
        assert score < 100.0
        assert any("Mismatched" in s for s in suggestions)

    def test_dangerous_pattern_penalized(self) -> None:
        checker = QualityChecker()
        dangerous_contents = [
            "Content with ../secrets path",
            "eval() usage here",
            "rm -rf / is dangerous",
        ]
        for content in dangerous_contents:
            score, suggestions = checker._check_director_quality(content, {})
            assert score < 100.0
            assert any("Dangerous" in s for s in suggestions)

    def test_patch_operations_trust_data_not_text_markers(self) -> None:
        checker = QualityChecker()
        # patches in data should pass even without SEARCH/REPLACE text
        score, _suggestions = checker._check_director_quality(
            "some text",
            {"patches": [{"file": "src/main.py", "search": "", "replace": "x"}]},
        )
        assert score == 100.0
