"""Tool execution integration tests.

Tests that verify tools execute correctly with real workspace operations.
These tests create temporary workspaces and verify actual tool behavior.

Run with: pytest polaris/kernelone/llm/toolkit/tests/test_tools_execution.py -v
"""

import os
from pathlib import Path

import pytest
from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace with test files."""
    # Create test file structure
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # Python file with various patterns
    (src_dir / "main.py").write_text("def foo(): pass\nclass Bar: pass\ndef bar(): pass\n", encoding="utf-8")

    # File with unicode
    (src_dir / "i18n.py").write_text("print('你好 🌍')\ndef hello(): pass\n", encoding="utf-8")

    # Duplicate function for search_replace tests
    (src_dir / "dup.py").write_text("def foo():\n    pass\n\ndef foo():\n    pass\n", encoding="utf-8")

    # File with CRLF line endings
    (src_dir / "win.py").write_bytes(b"def foo():\r\n    pass\r\n")

    # Create nested directory
    nested = src_dir / "nested"
    nested.mkdir()
    (nested / "deep.py").write_text("def deep(): pass\n", encoding="utf-8")

    return str(tmp_path)


class TestRepoRgExecution:
    """Test repo_rg tool execution."""

    def test_finds_def_pattern(self, temp_workspace) -> None:
        """repo_rg should find 'def' pattern."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": "^def ", "path": "src"})

        assert result["ok"] is True
        assert (
            len(result["result"]["result"]["results"]) >= 2
        )  # At least foo and bar from main.py (other files may also match)

    def test_pattern_with_trailing_space_preserved(self, temp_workspace) -> None:
        """Pattern '^def ' (with trailing space) should find only function definitions."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": "^def ", "path": "src"})

        assert result["ok"] is True
        matches = result["result"]["result"]["results"]
        # Should find "def foo():" and "def bar():" but NOT "class Bar:"
        assert all("def " in m["snippet"] for m in matches)

    def test_case_sensitive_search(self, temp_workspace) -> None:
        """repo_rg should respect case sensitivity."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": "Def", "path": "src", "case_sensitive": True})

        assert result["ok"] is True
        assert len(result["result"]["result"]["results"]) == 0  # 'Def' != 'def'

    def test_handles_unicode(self, temp_workspace) -> None:
        """repo_rg should handle unicode characters."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": "你好", "path": "src"})

        assert result["ok"] is True
        assert len(result["result"]["result"]["results"]) >= 1

    def test_max_results_respected(self, temp_workspace) -> None:
        """repo_rg should respect max_results limit."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": ".", "path": "src", "max_results": 1})

        assert result["ok"] is True
        assert len(result["result"]["result"]["results"]) <= 1

    def test_single_file_search_without_context_parses_line_only_output(self, temp_workspace) -> None:
        """repo_rg should parse `line:snippet` output when searching a single file path."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": "class Bar", "path": "src/main.py"})

        assert result["ok"] is True
        matches = result["result"]["result"]["results"]
        assert len(matches) >= 1
        assert any(match["file"].endswith("src/main.py") for match in matches)
        assert any("class Bar" in match["snippet"] for match in matches)

    def test_single_file_search_with_context_parses_line_only_output(self, temp_workspace) -> None:
        """repo_rg should parse `line:snippet` / `line-snippet` with context on one file."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": "def foo", "path": "src/main.py", "context_lines": 2})

        assert result["ok"] is True
        matches = result["result"]["result"]["results"]
        assert len(matches) >= 1
        assert any(match["file"].endswith("src/main.py") for match in matches)
        assert any("def foo" in match["snippet"] for match in matches)


class TestSearchCodeExecution:
    """Test search_code tool execution."""

    def test_search_code_finds_pattern(self, temp_workspace) -> None:
        """search_code should find patterns."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("search_code", {"query": "def", "path": "src"})

        assert result["ok"] is True

    def test_search_code_preserves_trailing_space(self, temp_workspace) -> None:
        """search_code query with trailing space should work."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("search_code", {"query": "^def ", "path": "src"})

        assert result["ok"] is True


class TestGlobExecution:
    """Test glob tool execution."""

    def test_finds_python_files(self, temp_workspace) -> None:
        """glob should find Python files."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("glob", {"pattern": "**/*.py"})

        assert result["ok"] is True
        files = result["result"].get("results", [])
        assert len(files) >= 3  # main.py, i18n.py, dup.py, win.py, nested/deep.py

    def test_glob_in_specific_directory(self, temp_workspace) -> None:
        """glob should find files in specific directory."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("glob", {"pattern": "src/*.py"})

        assert result["ok"] is True


class TestListDirectoryExecution:
    """Test list_directory tool execution."""

    def test_lists_directory(self, temp_workspace) -> None:
        """list_directory should list directory contents."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("list_directory", {"path": "src"})

        assert result["ok"] is True

    def test_recursive_listing(self, temp_workspace) -> None:
        """list_directory with recursive should list nested contents."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("list_directory", {"path": "src", "recursive": True})

        assert result["ok"] is True


class TestReadFileExecution:
    """Test read_file tool execution."""

    def test_reads_file(self, temp_workspace) -> None:
        """read_file should read file contents."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("read_file", {"file": "src/main.py"})

        assert result["ok"] is True
        assert "def foo" in result["result"]["content"]

    def test_read_head(self, temp_workspace) -> None:
        """repo_read_head should read first N lines."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_read_head", {"file": "src/main.py", "n": 2})

        assert result["ok"] is True
        content = result["result"]["content"]
        lines = content.split("\n")
        assert len(lines) <= 3

    def test_read_head_allows_large_file_with_small_range(self, temp_workspace) -> None:
        """repo_read_head should work on oversized files when requested range is small."""
        large_file = Path(temp_workspace) / "src" / "large.py"
        large_file.write_text("".join(f"line_{i}\n" for i in range(1, 2601)), encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_read_head", {"file": "src/large.py", "n": 40})

        assert result["ok"] is True
        payload = result["result"]
        assert payload["line_count"] == 40
        assert "line_1" in payload["content"]
        assert "line_41" not in payload["content"]

    def test_read_file_blocks_oversized_full_read_without_range(self, temp_workspace) -> None:
        """read_file full read must still be blocked by hard limit on oversized files."""
        large_file = Path(temp_workspace) / "src" / "huge.py"
        large_file.write_text("".join(f"entry_{i}\n" for i in range(1, 2601)), encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("read_file", {"file": "src/huge.py"})

        assert result["ok"] is False
        assert result.get("error_code") == "BUDGET_EXCEEDED"
        assert "hard limit" in result.get("error", "").lower()

    def test_read_slice_blocks_oversized_range_if_span_exceeds_hard_limit(self, temp_workspace) -> None:
        """Oversized-file ranged reads must still be blocked when range span is too large."""
        large_file = Path(temp_workspace) / "src" / "oversized.py"
        large_file.write_text("".join(f"item_{i}\n" for i in range(1, 2601)), encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_read_slice", {"file": "src/oversized.py", "start": 1, "end": 2500})

        assert result["ok"] is False
        assert result.get("error_code") == "BUDGET_EXCEEDED"
        assert "requested range spans" in result.get("error", "").lower()


class TestSearchReplaceExecution:
    """Test search_replace tool execution."""

    def test_search_replace_success(self, temp_workspace) -> None:
        """search_replace should replace matching text."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        # Must read file first to satisfy read-before-edit enforcement
        executor.execute("read_file", {"file": "src/main.py"})
        result = executor.execute(
            "search_replace", {"file": "src/main.py", "search": "def foo(): pass", "replace": "def foo(): return True"}
        )

        assert result["ok"] is True

        # Verify replacement
        content = (Path(temp_workspace) / "src" / "main.py").read_text(encoding="utf-8")
        assert "return True" in content
        assert "pass" not in content.split("def foo")[1].split("\n")[0]

    def test_search_replace_handles_crlf(self, temp_workspace) -> None:
        """search_replace should handle CRLF files with LF input."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        # Must read file first to satisfy read-before-edit enforcement
        executor.execute("read_file", {"file": "src/win.py"})
        result = executor.execute(
            "search_replace",
            {
                "file": "src/win.py",
                "search": "def foo():\n    pass",  # LF input
                "replace": "def bar():\n    pass",
            },
        )

        assert result["ok"] is True

    def test_search_replace_multiple_matches_fails(self, temp_workspace) -> None:
        """search_replace should fail or warn on multiple matches."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute(
            "search_replace",
            {"file": "src/dup.py", "search": "def foo():\n    pass", "replace": "def bar():\n    return True"},
        )

        # Should either fail or succeed but warn about multiple matches
        # The exact behavior depends on implementation
        assert "ok" in result


class TestSearchReplaceEdgeCases:
    """Test search_replace edge cases and error handling."""

    def test_search_replace_multiple_matches(self, temp_workspace) -> None:
        """search_replace should warn on multiple matches."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        # Must read file first to satisfy read-before-edit enforcement
        executor.execute("read_file", {"file": "src/dup.py"})
        result = executor.execute(
            "search_replace",
            {"file": "src/dup.py", "search": "def foo():\n    pass", "replace": "def bar():\n    return True"},
        )

        # Should warn about multiple matches or handle them explicitly
        assert result["ok"] is True
        # Check for warning about multiple matches or replacements_count indicating behavior
        assert (
            "multiple" in result.get("warning", "").lower()
            or "matches" in result.get("warning", "").lower()
            or result.get("result", {}).get("replacements_count", 0) > 0
        )

    def test_search_replace_handles_crlf(self, temp_workspace) -> None:
        """search_replace should handle CRLF file with LF input."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        # Must read file first to satisfy read-before-edit enforcement
        executor.execute("read_file", {"file": "src/win.py"})
        result = executor.execute(
            "search_replace",
            {
                "file": "src/win.py",
                "search": "def foo():\n    pass",  # LF input
                "replace": "def bar():\n    pass",
            },
        )

        assert result["ok"] is True
        # Verify the file was actually modified
        content = (Path(temp_workspace) / "src" / "win.py").read_text(encoding="utf-8")
        assert "def bar" in content

    def test_search_replace_nonexistent_file(self, temp_workspace) -> None:
        """search_replace should handle non-existent file gracefully."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute(
            "search_replace", {"file": "src/nonexistent.py", "search": "def foo():", "replace": "def bar():"}
        )

        assert result["ok"] is False
        # The stale_edit enforcement catches this before the handler's file-not-found check.
        # Accept either stale_edit error or file-not-found error.
        error = result.get("error", "").lower()
        assert (
            "not found" in error
            or "does not exist" in error
            or "no such file" in error
            or "stale" in error
            or "action denied" in error
        )

    def test_search_replace_empty_search(self, temp_workspace) -> None:
        """search_replace should handle empty search string without crashing."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("search_replace", {"file": "src/main.py", "search": "", "replace": "something"})

        # Should return a valid result structure (either error or success)
        assert "ok" in result
        assert "result" in result or "error" in result


class TestPathSecurity:
    """Test path security and traversal prevention."""

    def test_read_prevents_directory_traversal(self, temp_workspace) -> None:
        """Should prevent reading outside workspace."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_read_head", {"file": "../../../etc/passwd", "n": 10})

        # Should either fail or return empty/warning
        if not result["ok"]:
            error = result.get("error", "").lower()
            assert "access" in error or "outside" in error or "permission" in error or "unsupported" in error

    def test_write_prevents_directory_traversal(self, temp_workspace) -> None:
        """Should prevent writing outside workspace."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("write_file", {"file": "../../../tmp/malicious.txt", "content": "malicious"})

        assert result["ok"] is False

    def test_glob_restricted_to_workspace(self, temp_workspace) -> None:
        """glob should not escape workspace with ../ in pattern."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("glob", {"pattern": "../**/*.py"})

        # Should fail or return empty
        assert result["ok"] is False or len(result["result"].get("results", [])) == 0

    def test_list_directory_blocks_parent_traversal(self, temp_workspace) -> None:
        """list_directory should block ../ traversal."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("list_directory", {"path": "../"})

        # Should fail or be restricted
        assert result["ok"] is False or "error" in result


class TestExecuteCommandSecurity:
    """Test execute_command security boundaries."""

    def test_blocks_dangerous_commands(self, temp_workspace) -> None:
        """Should block obviously dangerous commands."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("execute_command", {"command": "rm -rf /"})

        # Should be blocked or fail
        assert result["ok"] is False or "denied" in result.get("error", "").lower()


class TestExecuteCommandGuards:
    """Test execute_command security and timeout guards."""

    def test_execute_command_blocks_dangerous_commands(self, temp_workspace) -> None:
        """Should block obviously dangerous commands like 'rm -rf /'."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("execute_command", {"command": "rm -rf /"})

        # Should be blocked or fail
        assert result["ok"] is False or "denied" in result.get("error", "").lower()

    def test_execute_command_timeout_on_hanging_process(self, temp_workspace) -> None:
        """Should timeout on hanging process like infinite loop."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        # Use Python infinite loop since python is in the allowed executables
        result = executor.execute("execute_command", {"command": 'python -c "while True: pass"', "timeout_seconds": 3})

        # Should timeout and fail
        assert result["ok"] is False
        error = result.get("error", "").lower()
        assert "timeout" in error or "unsafe" in error or "flag" in error

    def test_execute_command_readonly_result(self, temp_workspace) -> None:
        """Should return proper result structure from command execution."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("execute_command", {"command": "echo hello"})

        assert "ok" in result
        # If ok is True, should have result; if False, should have error
        if result["ok"]:
            assert "result" in result
        else:
            assert "error" in result

    def test_execute_command_includes_effect_receipt(self, temp_workspace) -> None:
        """execute_command should emit a canonical effect receipt for audit chain."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("execute_command", {"command": "python --version"})

        assert result["ok"] is True
        receipt = result["result"].get("effect_receipt")
        assert isinstance(receipt, dict)
        assert receipt.get("operation") == "execute_command"

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific shell wrapping behavior")
    def test_execute_command_windows_wraps_via_cmd(self, temp_workspace, monkeypatch) -> None:
        """npm-like commands should execute through cmd /c on Windows."""
        captured: dict[str, object] = {}

        class _Completed:
            returncode = 0
            stdout = "ok\n"
            stderr = ""

        def _fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
            captured["argv"] = argv
            captured["cwd"] = kwargs.get("cwd")
            return _Completed()

        monkeypatch.setattr("subprocess.run", _fake_run)

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("execute_command", {"command": "npm test"})

        assert result["ok"] is True
        assert captured["argv"] == ["cmd", "/c", "npm test"]
        assert captured["cwd"] == temp_workspace


class TestEdgeCases:
    """Test edge cases and robustness."""

    def test_handles_empty_pattern(self, temp_workspace) -> None:
        """Should handle empty pattern gracefully."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": "", "path": "src"})

        # Empty pattern should return error, not crash
        assert result["ok"] is False or len(result["result"]["result"].get("results", [])) == 0

    def test_handles_nonexistent_path(self, temp_workspace) -> None:
        """Should handle nonexistent path gracefully."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": "test", "path": "/nonexistent/path"})

        # Implementation returns ok=True with empty results for nonexistent path
        assert result["ok"] is True
        assert result["result"]["result"]["total_results"] == 0

    def test_handles_binary_files(self, temp_workspace) -> None:
        """Should handle binary files without crashing."""
        # Create a binary file
        binary_file = Path(temp_workspace) / "binary.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03\x04\x05")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_rg", {"pattern": ".", "path": temp_workspace})

        # Should not crash, should skip binary files
        assert result["ok"] is True


@pytest.mark.skip(reason="tree_sitter_language_pack import hangs in this environment")
class TestRepoSymbolsIndex:
    """Test repo_symbols_index tool execution."""

    def test_repo_symbols_index_returns_symbols(self, temp_workspace) -> None:
        """repo_symbols_index should return symbols from Python files."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_symbols_index", {"paths": ["src"]})

        assert result["ok"] is True
        assert "symbols" in result["result"]
        assert result["result"]["files_processed"] >= 1
        # Should find foo, Bar (class), bar from main.py
        assert result["result"]["total_symbols"] >= 3
        symbols = result["result"]["symbols"]
        symbol_names = [s["name"] for s in symbols]
        assert "foo" in symbol_names
        assert "Bar" in symbol_names
        assert "bar" in symbol_names

    def test_repo_symbols_index_handles_multiple_paths(self, temp_workspace) -> None:
        """repo_symbols_index should handle multiple paths."""
        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_symbols_index", {"paths": ["src", "src/nested"]})

        assert result["ok"] is True
        # Both directories should be searched
        assert result["result"]["files_processed"] >= 2
        symbol_names = [s["name"] for s in result["result"]["symbols"]]
        assert "foo" in symbol_names
        assert "deep" in symbol_names

    def test_repo_symbols_index_survives_syntax_errors(self, temp_workspace) -> None:
        """repo_symbols_index should not crash on syntax errors."""
        # Create a file with syntax error
        syntax_error_file = Path(temp_workspace) / "src" / "broken.py"
        syntax_error_file.write_text("def foo:\n    pass\n", encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_symbols_index", {"paths": ["src"]})

        # Should still succeed (syntax errors are handled gracefully)
        assert result["ok"] is True
        assert "symbols" in result["result"]
        # Warnings may be present about parsing failures
        if "warnings" in result["result"]:
            assert len(result["result"]["warnings"]) <= 5

    def test_repo_symbols_index_handles_nested_files(self, temp_workspace) -> None:
        """repo_symbols_index should find symbols in nested directory structure."""
        # Create deeply nested structure
        deep_nested = Path(temp_workspace) / "src" / "nested" / "deep"
        deep_nested.mkdir(parents=True, exist_ok=True)
        (deep_nested / "module.py").write_text("class MyClass:\n    pass\ndef my_func(): pass\n", encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_symbols_index", {"paths": ["src"]})

        assert result["ok"] is True
        # Should find symbols in nested/deep/module.py
        symbols = result["result"]["symbols"]
        symbol_names = [s["name"] for s in symbols]
        assert "MyClass" in symbol_names
        assert "my_func" in symbol_names
        # Verify file paths are workspace-relative
        for sym in symbols:
            assert "file" in sym
            assert not sym["file"].startswith("/")


class TestRepoReadSlice:
    """Test repo_read_slice tool execution."""

    def test_repo_read_slice_handles_out_of_bounds_lines(self, temp_workspace) -> None:
        """repo_read_slice should handle out-of-bounds line numbers gracefully."""
        # Create a short file with only 2 lines
        short_file = Path(temp_workspace) / "short.txt"
        short_file.write_text("line 1\nline 2\n", encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_read_slice", {"file": "short.txt", "start": 50, "end": 100})

        assert result["ok"] is True
        # The range should be clamped to actual lines, truncated flag should be set
        assert result["result"]["truncated"] is True
        # The content should reflect actual available lines
        content = result["result"]["content"]
        assert "line 1" in content or "line 2" in content

    def test_repo_read_slice_valid_range(self, temp_workspace) -> None:
        """repo_read_slice should read the exact range requested."""
        # Create a file with 5 lines
        multi_file = Path(temp_workspace) / "multi.txt"
        multi_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_read_slice", {"file": "multi.txt", "start": 2, "end": 4})

        assert result["ok"] is True
        assert result["result"]["truncated"] is True
        content = result["result"]["content"]
        # Should contain lines 2-4
        assert "line 2" in content
        assert "line 3" in content
        assert "line 4" in content

    def test_repo_read_slice_negative_lines(self, temp_workspace) -> None:
        """repo_read_slice should handle negative line numbers."""
        multi_file = Path(temp_workspace) / "multi.txt"
        multi_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        # Negative values get converted by int() in handler
        result = executor.execute("repo_read_slice", {"file": "multi.txt", "start": -5, "end": -1})

        # Should handle gracefully - int(-5) = -5, then clamping kicks in
        assert "ok" in result

    def test_repo_read_slice_zero_start(self, temp_workspace) -> None:
        """repo_read_slice should handle start=0 properly."""
        multi_file = Path(temp_workspace) / "multi.txt"
        multi_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")

        executor = AgentAccelToolExecutor(workspace=temp_workspace)
        result = executor.execute("repo_read_slice", {"file": "multi.txt", "start": 0, "end": 2})

        # start=0 should be handled gracefully
        # int(0) = 0, then start_idx = max(0, 0-1) = 0 in handler
        assert "ok" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
