"""Test suite for `roles.kernel` OutputParser patch file parsing.

Covers:
- OutputParser.extract_search_replace() — unified SEARCH/REPLACE block extraction
- OutputParser.extract_json() — JSON extraction from code blocks and <output> tags
- OutputParser.parse_thinking() — <thinking> tag extraction
- OutputParser.check_security() — dangerous pattern detection
- Path safety in _parse_patch_file_format()
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.output_parser import OutputParser


class TestExtractSearchReplace:
    """extract_search_replace() delegates to unified parser or falls back to regex."""

    def test_standard_search_replace_blocks(self) -> None:
        parser = OutputParser()
        content = """<<<<<<< SEARCH
def hello():
    return "old"
=======
def hello():
    return "new"
>>>>>>> REPLACE"""
        result = parser.extract_search_replace(content)
        assert result is not None
        assert len(result) == 1
        assert 'return "old"' in result[0]["search"]
        assert 'return "new"' in result[0]["replace"]

    def test_multiple_search_replace_blocks(self) -> None:
        parser = OutputParser()
        content = """<<<<<<< SEARCH
a = 1
=======
a = 2
>>>>>>> REPLACE

<<<<<<< SEARCH
b = 3
=======
b = 4
>>>>>>> REPLACE"""
        result = parser.extract_search_replace(content)
        assert result is not None
        assert len(result) == 2

    def test_no_blocks_returns_none(self) -> None:
        parser = OutputParser()
        result = parser.extract_search_replace("just regular text")
        # Falls back to regex — no blocks → None
        assert result is None

    def test_empty_content_returns_none(self) -> None:
        parser = OutputParser()
        result = parser.extract_search_replace("")
        assert result is None

    def test_strips_whitespace(self) -> None:
        parser = OutputParser()
        content = "<<<<<<< SEARCH\\n  old code  \\n=======\\n  new code  \\n>>>>>>> REPLACE"
        result = parser.extract_search_replace(content)
        assert result is not None
        assert "old code" in result[0]["search"]
        assert "new code" in result[0]["replace"]


class TestExtractJSON:
    """extract_json() handles fenced and unfenced JSON."""

    def test_fenced_json_block(self) -> None:
        parser = OutputParser()
        content = '```json\n{"key": "value", "count": 42}\n```'
        result = parser.extract_json(content)
        assert result == {"key": "value", "count": 42}

    def test_triple_single_quotes_json(self) -> None:
        parser = OutputParser()
        content = "'''json\n{\"name\": \"test\"}\n'''"
        result = parser.extract_json(content)
        assert result == {"name": "test"}

    def test_output_tag_json(self) -> None:
        parser = OutputParser()
        content = '<output>{"result": true}</output>'
        result = parser.extract_json(content)
        assert result == {"result": True}

    def test_no_json_returns_none(self) -> None:
        parser = OutputParser()
        result = parser.extract_json("plain text")
        assert result is None

    def test_invalid_json_returns_none(self) -> None:
        parser = OutputParser()
        content = '```json\n{"broken": }\n```'
        result = parser.extract_json(content)
        assert result is None

    def test_multiple_json_blocks_takes_first(self) -> None:
        parser = OutputParser()
        content = '```json\n{"first": true}\n```\n\n```json\n{"second": true}\n```'
        result = parser.extract_json(content)
        assert result == {"first": True}


class TestParseThinking:
    """parse_thinking() extracts <thinking> tags and returns clean content."""

    def test_extracts_thinking_tag(self) -> None:
        parser = OutputParser()
        content = "<thinking>I should check the docs first</thinking>\n\nFinal answer here."
        result = parser.parse_thinking(content)
        assert result.thinking is not None
        assert "check the docs" in result.thinking
        assert "<thinking>" not in result.clean_content

    def test_no_thinking_returns_none(self) -> None:
        parser = OutputParser()
        content = "Just plain output without tags."
        result = parser.parse_thinking(content)
        assert result.thinking is None
        assert result.clean_content == content

    def test_multiline_thinking(self) -> None:
        parser = OutputParser()
        content = "<thinking>Line 1\nLine 2\nLine 3</thinking>\nOutput"
        result = parser.parse_thinking(content)
        assert result.thinking is not None and "Line 1" in result.thinking
        assert result.thinking is not None and "Line 2" in result.thinking

    def test_tolerates_malformed_closing_thinking_tag(self) -> None:
        parser = OutputParser()
        content = "<thinking>分析中...\n下一步计划</thinking\n最终答复"
        result = parser.parse_thinking(content)
        assert result.thinking is not None
        assert "分析中" in result.thinking
        assert "最终答复" in result.clean_content
        assert "<thinking>" not in result.clean_content

    def test_unclosed_thinking_tag_treated_as_thinking_only(self) -> None:
        parser = OutputParser()
        content = "<thinking>只有思考，没有最终答复"
        result = parser.parse_thinking(content)
        assert result.thinking == "只有思考，没有最终答复"
        assert result.clean_content == ""

    def test_parse_thinking_strips_output_wrappers_from_clean_content(self) -> None:
        parser = OutputParser()
        content = "<output>最终答复</output>"
        result = parser.parse_thinking(content)
        assert result.thinking is None
        assert result.clean_content == "最终答复"
        assert "<output>" not in result.clean_content

    def test_parse_thinking_strips_output_wrappers_after_thinking_removal(self) -> None:
        parser = OutputParser()
        content = "<thinking>先分析</thinking><output>最终答复</output>"
        result = parser.parse_thinking(content)
        assert result.thinking == "先分析"
        assert result.clean_content == "最终答复"
        assert "<output>" not in result.clean_content


class TestCheckSecurity:
    """check_security() detects dangerous path traversal and code injection patterns."""

    def test_detects_path_traversal(self) -> None:
        parser = OutputParser()
        safe, issues = parser.check_security("Content referencing ../secrets")
        assert safe is False
        assert len(issues) > 0

    def test_detects_absolute_windows_path(self) -> None:
        parser = OutputParser()
        # DANGEROUS_PATTERNS includes "../", so parent traversal with Windows backslash is caught
        safe, issues = parser.check_security("Accessing ../secrets from C:\\path")
        assert safe is False
        assert len(issues) > 0

    def test_detects_eval_injection(self) -> None:
        parser = OutputParser()
        safe, _issues = parser.check_security("Use eval() for dynamic code")
        assert safe is False

    def test_detects_rm_rf(self) -> None:
        parser = OutputParser()
        safe, _issues = parser.check_security("Execute: rm -rf /")
        assert safe is False

    def test_safe_content_passes(self) -> None:
        parser = OutputParser()
        safe, issues = parser.check_security("def hello(): return 'world'")
        assert safe is True
        assert issues == []


class TestIsSafeRelativePath:
    """_is_safe_relative_path enforces workspace-only paths."""

    def test_relative_path_is_safe(self) -> None:
        parser = OutputParser()
        assert parser._is_safe_relative_path("src/main.py") is True
        assert parser._is_safe_relative_path("polaris/kernelone/llm/engine.py") is True

    def test_absolute_unix_path_unsafe(self) -> None:
        parser = OutputParser()
        assert parser._is_safe_relative_path("/etc/passwd") is False
        assert parser._is_safe_relative_path("/workspace/src") is False

    def test_absolute_windows_path_unsafe(self) -> None:
        parser = OutputParser()
        assert parser._is_safe_relative_path("C:\\Windows\\System32") is False
        assert parser._is_safe_relative_path("D:\\secrets\\key") is False

    def test_parent_traversal_unsafe(self) -> None:
        parser = OutputParser()
        assert parser._is_safe_relative_path("../secrets") is False
        assert parser._is_safe_relative_path("src/../etc/passwd") is False

    def test_null_byte_unsafe(self) -> None:
        parser = OutputParser()
        assert parser._is_safe_relative_path("safe\x00file") is False

    def test_empty_path_unsafe(self) -> None:
        parser = OutputParser()
        assert parser._is_safe_relative_path("") is False
        assert parser._is_safe_relative_path("   ") is False
