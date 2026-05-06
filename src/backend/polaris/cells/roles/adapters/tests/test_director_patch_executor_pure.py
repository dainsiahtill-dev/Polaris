"""Unit tests for DirectorPatchExecutor pure logic (no I/O, no filesystem).

Covers:
- resolve_llm_call_timeout_seconds
- _normalize_tool_arguments
- _extract_markdown_file_blocks
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor

# ---------------------------------------------------------------------------
# LLM Timeout Resolution
# ---------------------------------------------------------------------------


class TestResolveLlmCallTimeoutSeconds:
    """resolve_llm_call_timeout_seconds is a pure function of context + env."""

    def test_default_fallback(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds(None)
        assert isinstance(result, float)
        assert result > 0

    def test_context_value_used(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 30.0})
        assert result == 30.0

    def test_context_string_coerced(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": "45"})
        assert result == 45.0

    def test_context_invalid_ignored(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": "abc"})
        assert isinstance(result, float)
        assert result > 0

    def test_context_zero_ignored(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 0})
        assert isinstance(result, float)
        assert result > 0

    def test_context_negative_ignored(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": -10})
        assert isinstance(result, float)
        assert result > 0

    def test_env_override(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS", "60")
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds(None)
        assert result == 60.0

    def test_env_fallback_timeout_seconds(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", "90")
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds(None)
        assert result == 90.0

    def test_context_takes_precedence_over_env(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS", "60")
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 30.0})
        assert result == 30.0

    def test_clamped_to_maximum(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 9999.0})
        assert result == 900.0

    def test_clamped_to_minimum(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 0.01})
        assert result == 0.1


# ---------------------------------------------------------------------------
# Tool Argument Normalization
# ---------------------------------------------------------------------------


class TestNormalizeToolArguments:
    """_normalize_tool_arguments is a pure static function."""

    def test_dict_passthrough(self) -> None:
        args = {"file": "test.py", "content": "print(1)"}
        result, error = DirectorPatchExecutor._normalize_tool_arguments(args)
        assert result == args
        assert error is None

    def test_list_with_single_dict(self) -> None:
        args = [{"file": "test.py"}]
        result, error = DirectorPatchExecutor._normalize_tool_arguments(args)
        assert result == {"file": "test.py"}
        assert error is None

    def test_list_multiple_items_error(self) -> None:
        args = [{"file": "a.py"}, {"file": "b.py"}]
        result, error = DirectorPatchExecutor._normalize_tool_arguments(args)
        assert result == {}
        assert error is not None
        assert "list" in error.lower()

    def test_string_error(self) -> None:
        result, error = DirectorPatchExecutor._normalize_tool_arguments("not a dict")
        assert result == {}
        assert error is not None
        assert "str" in error.lower()

    def test_none_error(self) -> None:
        result, error = DirectorPatchExecutor._normalize_tool_arguments(None)
        assert result == {}
        assert error is not None
        assert "NoneType" in error

    def test_int_error(self) -> None:
        result, error = DirectorPatchExecutor._normalize_tool_arguments(42)
        assert result == {}
        assert error is not None
        assert "int" in error.lower()


# ---------------------------------------------------------------------------
# Markdown File Block Extraction
# ---------------------------------------------------------------------------


class TestExtractMarkdownFileBlocks:
    """_extract_markdown_file_blocks is a pure static function."""

    def test_empty_text(self) -> None:
        result = DirectorPatchExecutor._extract_markdown_file_blocks("")
        assert result == []

    def test_none_text(self) -> None:
        result = DirectorPatchExecutor._extract_markdown_file_blocks("")
        assert result == []

    def test_basic_code_block(self) -> None:
        text = "test.py\n```python\nprint(1)\n```"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert len(result) == 1
        assert result[0]["file"] == "test.py"
        assert result[0]["replace"] == "print(1)"
        assert result[0]["search"] == ""

    def test_multiple_code_blocks(self) -> None:
        text = "a.py\n```python\nprint(1)\n```\n\nb.js\n```javascript\nconsole.log(1)\n```"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert len(result) == 2
        assert result[0]["file"] == "a.py"
        assert result[1]["file"] == "b.js"

    def test_heading_prefix(self) -> None:
        text = "### src/main.py\n```python\ndef main(): pass\n```"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert len(result) == 1
        assert result[0]["file"] == "src/main.py"

    def test_list_prefix(self) -> None:
        text = "- config.yaml\n```yaml\nkey: value\n```"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert len(result) == 1
        assert result[0]["file"] == "config.yaml"

    def test_no_language_specifier(self) -> None:
        text = "file.txt\n```\nplain text\n```"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert len(result) == 1
        assert result[0]["file"] == "file.txt"
        assert result[0]["replace"] == "plain text"

    def test_skips_protocol_like_content(self, monkeypatch: Any) -> None:
        # When content looks like a protocol patch response, it should be skipped
        text = "file.py\n```python\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n```"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert result == []

    def test_no_match_returns_empty(self) -> None:
        text = "Just some plain text without code blocks"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert result == []

    def test_file_path_with_various_extensions(self) -> None:
        text = (
            "model.ts\n```typescript\ninterface Model {}\n```\n"
            "styles.css\n```css\n.body { color: red; }\n```\n"
            "README.md\n```markdown\n# Title\n```"
        )
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert len(result) == 3
        files = [r["file"] for r in result]
        assert "model.ts" in files
        assert "styles.css" in files
        assert "README.md" in files

    def test_multiline_content_preserved(self) -> None:
        text = "script.py\n```python\nline1\nline2\nline3\n```"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert len(result) == 1
        assert result[0]["replace"] == "line1\nline2\nline3"
