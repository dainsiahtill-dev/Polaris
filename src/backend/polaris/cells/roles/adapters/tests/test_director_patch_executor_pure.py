"""Unit tests for DirectorPatchExecutor pure logic (no I/O, no filesystem).

Covers:
- resolve_llm_call_timeout_seconds
- extract_kernel_tool_results
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

    def test_context_value_cannot_reduce_default(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 30.0})
        assert result >= 600.0

    def test_context_string_cannot_reduce_default(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": "45"})
        assert result >= 600.0

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

    def test_env_sets_explicit_floor_for_context(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS", "60")
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 30.0})
        assert result == 60.0

    def test_context_can_raise_above_env_floor(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS", "60")
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 120.0})
        assert result == 120.0

    def test_clamped_to_maximum(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 9999.0})
        assert result == 1800.0

    def test_maximum_can_be_configured_for_slow_local_models(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS", "2400")
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 9999.0})
        assert result == 2400.0

    def test_clamped_to_minimum(self) -> None:
        result = DirectorPatchExecutor.resolve_llm_call_timeout_seconds({"llm_call_timeout_seconds": 0.01})
        assert result >= 600.0


class TestResolveDirectFallbackTimeoutSeconds:
    """direct text fallback must stay within the primary Director budget."""

    def test_default_caps_long_primary_budget(self) -> None:
        result = DirectorPatchExecutor.resolve_direct_fallback_timeout_seconds(None, 600.0)
        assert result == 60.0

    def test_default_honors_short_primary_budget(self) -> None:
        result = DirectorPatchExecutor.resolve_direct_fallback_timeout_seconds(None, 12.0)
        assert result == 12.0

    def test_context_override_is_bounded_by_primary(self) -> None:
        result = DirectorPatchExecutor.resolve_direct_fallback_timeout_seconds(
            {"direct_fallback_timeout_seconds": 45.0},
            30.0,
        )
        assert result == 30.0

    def test_env_override(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_DIRECT_FALLBACK_TIMEOUT_SECONDS", "25")
        result = DirectorPatchExecutor.resolve_direct_fallback_timeout_seconds(None, 600.0)
        assert result == 25.0


# ---------------------------------------------------------------------------
# Kernel Tool Results
# ---------------------------------------------------------------------------


class TestExtractKernelToolResults:
    def test_delegates_to_normalizer(self) -> None:
        result = DirectorPatchExecutor.extract_kernel_tool_results(
            {
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "package.json"},
                    }
                ]
            }
        )
        assert result[0]["tool"] == "write_file"
        assert result[0]["tool_name"] == "write_file"
        assert result[0]["success"] is True
        assert result[0]["result"] == {"path": "package.json"}
        assert result[0]["raw_result"] == {"tool": "write_file", "success": True, "result": {"path": "package.json"}}


# ---------------------------------------------------------------------------
# Output Validation
# ---------------------------------------------------------------------------


class TestValidateGeneratedOutput:
    def test_accepts_scope_path_domain_signal(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "import http from 'http';\n"
            "const server = http.createServer((_req, res) => res.end('ok'));\n"
            "export default server;\n",
            encoding="utf-8",
        )
        executor = DirectorPatchExecutor(str(tmp_path))

        error = executor.validate_generated_output(
            {
                "subject": "Extend Node.js backend entrypoint",
                "description": "Execute according to the task contract",
                "metadata": {"target_files": ["src/server/app.ts"]},
            },
            ["src/server/app.ts"],
        )

        assert error is None

    def test_accepts_target_stem_domain_signal_in_typescript_symbols(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "models" / "task.model.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "export interface TaskParameter {\n"
            "  name: string;\n"
            "  type: 'string' | 'number' | 'boolean' | 'object' | 'array';\n"
            "}\n\n"
            "export interface TaskDefinition {\n"
            "  id: string;\n"
            "  parameters: TaskParameter[];\n"
            "}\n",
            encoding="utf-8",
        )
        executor = DirectorPatchExecutor(str(tmp_path))

        error = executor.validate_generated_output(
            {
                "subject": "任务定义模型与参数模板开发",
                "description": "Execute according to the task contract",
                "metadata": {"target_files": ["src/models/task.model.ts"]},
            },
            ["src/models/task.model.ts"],
        )

        assert error is None


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

    def test_applies_tool_arg_aliases_when_tool_name_provided(self) -> None:
        result, error = DirectorPatchExecutor._normalize_tool_arguments(
            {"filename": "src/app.py", "text": "print('ok')\n"},
            tool_name="write_file",
        )

        assert result == {"file": "src/app.py", "content": "print('ok')\n"}
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

    def test_skips_patch_file_heading_with_diff_content(self) -> None:
        text = "PATCH_FILE src/main/providers.ts\n```diff\n@@\n-old\n+new\n```"
        result = DirectorPatchExecutor._extract_markdown_file_blocks(text)
        assert result == []

    def test_skips_unified_diff_content_for_real_path(self) -> None:
        text = "src/main/providers.ts\n```diff\n@@\n-old\n+new\n```"
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


class TestValidateRelativePatchPath:
    """Generated patch paths must be workspace-relative file paths."""

    def test_allows_normal_relative_paths(self) -> None:
        assert DirectorPatchExecutor._validate_relative_patch_path("src/App.tsx") is None
        assert DirectorPatchExecutor._validate_relative_patch_path("README.md") is None

    def test_rejects_prose_heading_path(self) -> None:
        error = DirectorPatchExecutor._validate_relative_patch_path("distributable application:")
        assert error is not None
        assert "Invalid patch path" in error

    def test_rejects_patch_file_token_path(self) -> None:
        error = DirectorPatchExecutor._validate_relative_patch_path("PATCH_FILE src/main/providers.ts")
        assert error is not None
        assert "Invalid patch path" in error

    def test_rejects_absolute_windows_path(self) -> None:
        error = DirectorPatchExecutor._validate_relative_patch_path("C:/Users/example/file.ts")
        assert error is not None
        assert "Absolute patch paths" in error

    def test_rejects_parent_traversal(self) -> None:
        error = DirectorPatchExecutor._validate_relative_patch_path("../outside.ts")
        assert error is not None
        assert "Unsafe patch path" in error

    def test_html_placeholder_attribute_is_not_low_quality(self, tmp_path: Any) -> None:
        """L2-10 r4 live regression: a real Markdown editor's <textarea
        placeholder="..."> was killed by the bare-word \\bplaceholder\\b match.
        Attribute/property usage is legitimate input-UI code."""
        target = tmp_path / "index.html"
        target.write_text(
            "<!DOCTYPE html>\n<html>\n<body>\n"
            '<textarea id="editor" placeholder="在此输入 Markdown 内容..."></textarea>\n'
            '<div id="preview"></div>\n'
            "<script>\n"
            "const editor = document.getElementById('editor');\n"
            "editor.addEventListener('input', () => render(editor.value));\n"
            "function render(markdown) { /* parse markdown to html */ }\n"
            "</script>\n</body>\n</html>\n",
            encoding="utf-8",
        )
        executor = DirectorPatchExecutor(str(tmp_path))

        error = executor.validate_generated_output(
            {
                "subject": "实现 Markdown 实时预览器主应用(index.html)",
                "description": "左侧输入右侧实时渲染 markdown editor preview",
                "metadata": {"target_files": ["index.html"]},
            },
            ["index.html"],
        )

        assert error is None

    def test_prose_placeholder_scaffold_still_rejected(self, tmp_path: Any) -> None:
        target = tmp_path / "index.html"
        target.write_text(
            "<!DOCTYPE html>\n<html><body>\n"
            "<!-- This file is a placeholder for the markdown editor implementation -->\n"
            "<div>markdown editor preview coming soon</div>\n"
            "</body></html>\n",
            encoding="utf-8",
        )
        executor = DirectorPatchExecutor(str(tmp_path))

        error = executor.validate_generated_output(
            {
                "subject": "实现 Markdown 实时预览器主应用(index.html)",
                "description": "markdown editor preview",
                "metadata": {"target_files": ["index.html"]},
            },
            ["index.html"],
        )

        assert error is not None
        assert "placeholder" in error

    def test_css_placeholder_pseudo_element_is_not_low_quality(self, tmp_path: Any) -> None:
        """L2-10 r5 live regression: a real .editor::placeholder CSS rule was
        killed by the same bare-word match after the attribute fix."""
        target = tmp_path / "style.css"
        target.write_text(
            ".editor { width: 50%; font-family: monospace; }\n"
            ".editor::placeholder { color: #888; }\n"
            ".editor:placeholder-shown { border-color: #ccc; }\n"
            ".preview { width: 50%; overflow-y: auto; }\n",
            encoding="utf-8",
        )
        executor = DirectorPatchExecutor(str(tmp_path))

        error = executor.validate_generated_output(
            {
                "subject": "Bootstrap Markdown previewer styles (style.css)",
                "description": "editor preview styles css",
                "metadata": {"target_files": ["style.css"]},
            },
            ["style.css"],
        )

        assert error is None
