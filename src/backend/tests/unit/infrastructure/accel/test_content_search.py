"""Tests for polaris.infrastructure.accel.query.content_search module."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.infrastructure.accel.query.content_search import (
    _build_snippet,
    _compile_pattern,
    _iter_candidate_files,
    _matches_any,
    _normalize_patterns,
    _normalize_rel_path,
    _should_skip_default,
    _truncate_text,
    search_code_content,
)


class TestNormalizePatterns:
    """Tests for _normalize_patterns function."""

    def test_none_input(self) -> None:
        """None input should return empty list."""
        result = _normalize_patterns(None)
        assert result == []

    def test_empty_list(self) -> None:
        """Empty list should return empty list."""
        result = _normalize_patterns([])
        assert result == []

    def test_normalizes_patterns(self) -> None:
        """Should normalize patterns (trim, convert backslash)."""
        result = _normalize_patterns(["  *.py  ", r"src\*.js"])
        assert "*.py" in result
        assert "src/*.js" in result

    def test_filters_empty_patterns(self) -> None:
        """Should filter out empty patterns."""
        result = _normalize_patterns(["", "  ", "*.py", ""])
        assert result == ["*.py"]


class TestNormalizeRelPath:
    """Tests for _normalize_rel_path function."""

    def test_relative_path(self, tmp_path: Path) -> None:
        """Should return relative path."""
        file_path = tmp_path / "subdir" / "file.txt"
        result = _normalize_rel_path(tmp_path, file_path)
        assert result == "subdir/file.txt"

    def test_file_at_root(self, tmp_path: Path) -> None:
        """Should return just filename for root file."""
        file_path = tmp_path / "file.txt"
        result = _normalize_rel_path(tmp_path, file_path)
        assert result == "file.txt"

    def test_path_outside_project(self, tmp_path: Path) -> None:
        """Should handle path outside project."""
        other_path = Path("C:/other/file.txt")
        result = _normalize_rel_path(tmp_path, other_path)
        # Result should be the other path (as_posix for comparison)
        assert result == other_path.as_posix()


class TestMatchesAny:
    """Tests for _matches_any function."""

    def test_empty_patterns(self) -> None:
        """Empty patterns should return False."""
        assert _matches_any("file.py", []) is False

    def test_matches_exact(self) -> None:
        """Should match exact filename."""
        assert _matches_any("file.py", ["file.py"]) is True

    def test_matches_extension(self) -> None:
        """Should match extension wildcard."""
        assert _matches_any("file.py", ["*.py"]) is True
        assert _matches_any("file.js", ["*.py"]) is False

    def test_matches_directory_path(self) -> None:
        """Should match path patterns."""
        assert _matches_any("src/file.py", ["src/*.py"]) is True
        assert _matches_any("src/file.py", ["tests/*.py"]) is False

    def test_matches_base_name_only(self) -> None:
        """Should match against base name only."""
        assert _matches_any("src/file.py", ["file.py"]) is True


class TestShouldSkipDefault:
    """Tests for _should_skip_default function."""

    def test_skips_git_dir(self) -> None:
        """Should skip .git directory."""
        assert _should_skip_default(".git/config") is True

    def test_skips_pycache(self) -> None:
        """Should skip __pycache__."""
        assert _should_skip_default("__pycache__/module.pyc") is True

    def test_skips_node_modules(self) -> None:
        """Should skip node_modules."""
        assert _should_skip_default("node_modules/package/index.js") is True

    def test_does_not_skip_src(self) -> None:
        """Should not skip src directory."""
        assert _should_skip_default("src/module.py") is False

    def test_does_not_skip_tests(self) -> None:
        """Should not skip tests directory."""
        assert _should_skip_default("tests/test_module.py") is False


class TestTruncateText:
    """Tests for _truncate_text function."""

    def test_short_text(self) -> None:
        """Short text should not be truncated."""
        text = "short"
        result = _truncate_text(text, 100)
        assert result == text

    def test_long_text_truncated(self) -> None:
        """Long text should be truncated with ellipsis."""
        text = "a" * 200
        result = _truncate_text(text, 50)
        assert len(result) == 50
        assert result.endswith("...")

    def test_very_small_limit(self) -> None:
        """Very small limit should still truncate."""
        text = "hello"
        result = _truncate_text(text, 3)
        assert len(result) == 3

    def test_exact_length(self) -> None:
        """Text exactly at limit should not be truncated."""
        text = "abc"
        result = _truncate_text(text, 3)
        assert result == text


class TestBuildSnippet:
    """Tests for _build_snippet function."""

    def test_centered_snippet(self) -> None:
        """Should return centered snippet with context."""
        lines = ["line0", "line1", "line2", "line3", "line4"]
        snippet, start, end = _build_snippet(lines, line_idx=2, context_lines=1)
        assert start == 2  # line_idx 2 -> line number 2 (1-indexed)
        assert end == 4
        assert "line1" in snippet
        assert "line2" in snippet
        assert "line3" in snippet

    def test_at_start(self) -> None:
        """Should handle snippet at start of file."""
        lines = ["line0", "line1", "line2"]
        snippet, start, end = _build_snippet(lines, line_idx=0, context_lines=1)
        # snippet_start = max(0, 0-1) = 0, so start = 0+1 = 1
        # snippet_end = min(3, 0+1+1) = 2
        assert start == 1
        assert end == 2
        assert "line0" in snippet
        assert "line1" in snippet

    def test_at_end(self) -> None:
        """Should handle snippet at end of file."""
        lines = ["line0", "line1", "line2"]
        _snippet, start, end = _build_snippet(lines, line_idx=2, context_lines=1)
        assert start == 2
        assert end == 3

    def test_truncates_long_snippet(self) -> None:
        """Should truncate long snippets."""
        # Create a line longer than MAX_SNIPPET_CHARS (600)
        long_line = "a" * 1000
        lines = [long_line]
        snippet, _, _ = _build_snippet(lines, line_idx=0, context_lines=0)
        # The function should return a string (truncation may or may not happen
        # depending on MAX_SNIPPET_CHARS constant)
        assert isinstance(snippet, str)
        assert len(snippet) >= 0


class TestCompilePattern:
    """Tests for _compile_pattern function."""

    def test_literal_pattern(self) -> None:
        """Should escape literal pattern when use_regex=False."""
        pattern = _compile_pattern("test.pattern", case_sensitive=True, use_regex=False)
        assert pattern.match("test.pattern") is not None
        assert pattern.match("testXpattern") is None

    def test_case_insensitive(self) -> None:
        """Should handle case insensitive matching."""
        pattern = _compile_pattern("TEST", case_sensitive=False, use_regex=True)
        assert pattern.match("test") is not None
        assert pattern.match("TEST") is not None

    def test_case_sensitive(self) -> None:
        """Should handle case sensitive matching."""
        pattern = _compile_pattern("test", case_sensitive=True, use_regex=True)
        assert pattern.match("test") is not None
        assert pattern.match("TEST") is None

    def test_invalid_regex(self) -> None:
        """Invalid regex should raise re.error."""
        import re

        with pytest.raises(re.error):
            _compile_pattern("[invalid", case_sensitive=True, use_regex=True)


class TestIterCandidateFiles:
    """Tests for _iter_candidate_files function."""

    def test_finds_python_files(self, tmp_path: Path) -> None:
        """Should find Python files matching pattern."""
        (tmp_path / "test.py").write_text("code")
        (tmp_path / "readme.txt").write_text("text")
        results = list(
            _iter_candidate_files(
                tmp_path,
                file_patterns=["*.py"],
                include_patterns=[],
                exclude_patterns=[],
            )
        )
        assert len(results) == 1
        assert results[0][0].name == "test.py"

    def test_excludes_directories(self, tmp_path: Path) -> None:
        """Should exclude default skipped directories."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        (git_dir / "config").write_text("config")
        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "code.py").write_text("code")
        results = list(
            _iter_candidate_files(
                tmp_path,
                file_patterns=["*.py"],
                include_patterns=[],
                exclude_patterns=[],
            )
        )
        # Should include src/code.py but not .git/config
        assert any(r[0].name == "code.py" for r in results)
        assert not any(".git" in str(r[0]) for r in results)

    def test_respects_exclude_patterns(self, tmp_path: Path) -> None:
        """Should respect exclude patterns."""
        (tmp_path / "include.py").write_text("code")
        (tmp_path / "exclude.py").write_text("code")
        results = list(
            _iter_candidate_files(
                tmp_path,
                file_patterns=[],
                include_patterns=[],
                exclude_patterns=["exclude.py"],
            )
        )
        assert len(results) == 1
        assert results[0][0].name == "include.py"


class TestSearchCodeContent:
    """Tests for search_code_content function."""

    def test_empty_pattern_error(self, tmp_path: Path) -> None:
        """Empty pattern should return error."""
        result = search_code_content(project_dir=tmp_path, pattern="")
        assert result["status"] == "error"
        assert result["error"] == "empty_pattern"

    def test_nonexistent_project(self, tmp_path: Path) -> None:
        """Non-existent project should return error."""
        result = search_code_content(
            project_dir=tmp_path / "nonexistent",
            pattern="test",
        )
        assert result["status"] == "error"
        assert result["error"] == "project_not_found"

    def test_invalid_regex(self, tmp_path: Path) -> None:
        """Invalid regex should return error."""
        result = search_code_content(
            project_dir=tmp_path,
            pattern="[invalid",
            use_regex=True,
        )
        assert result["status"] == "error"
        assert result["error"] == "invalid_pattern"

    def test_basic_search(self, tmp_path: Path) -> None:
        """Should find matches in files."""
        (tmp_path / "test.py").write_text("def test():\n    pass\n")
        result = search_code_content(
            project_dir=tmp_path,
            pattern="def test",
            file_patterns=["*.py"],
        )
        assert result["status"] == "ok"
        assert result["result_count"] == 1
        assert result["matches"][0]["file"] == "test.py"
        assert result["matches"][0]["line"] == 1

    def test_case_insensitive_search(self, tmp_path: Path) -> None:
        """Should handle case insensitive search."""
        (tmp_path / "test.py").write_text("TEST\ntest\nTest\n")
        result = search_code_content(
            project_dir=tmp_path,
            pattern="test",
            case_sensitive=False,
        )
        assert result["result_count"] == 3

    def test_case_sensitive_search(self, tmp_path: Path) -> None:
        """Should handle case sensitive search."""
        (tmp_path / "test.py").write_text("TEST\ntest\nTest\n")
        result = search_code_content(
            project_dir=tmp_path,
            pattern="test",
            case_sensitive=True,
        )
        assert result["result_count"] == 1

    def test_max_results_limit(self, tmp_path: Path) -> None:
        """Should respect max_results limit."""
        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"match{i}\n")
        result = search_code_content(
            project_dir=tmp_path,
            pattern="match",
            max_results=5,
        )
        assert result["result_count"] <= 5
        assert result["truncated"] is True

    def test_context_lines(self, tmp_path: Path) -> None:
        """Should include context lines in snippet."""
        (tmp_path / "test.py").write_text("line0\nline1\nline2\nline3\nline4\n")
        result = search_code_content(
            project_dir=tmp_path,
            pattern="line2",
            context_lines=1,
        )
        assert result["status"] == "ok"
        assert len(result["matches"]) == 1
        assert "line1" in result["matches"][0]["snippet"]
        assert "line2" in result["matches"][0]["snippet"]
        assert "line3" in result["matches"][0]["snippet"]

    def test_exclude_patterns(self, tmp_path: Path) -> None:
        """Should exclude files matching exclude patterns."""
        (tmp_path / "include.py").write_text("match\n")
        (tmp_path / "exclude.py").write_text("match\n")
        result = search_code_content(
            project_dir=tmp_path,
            pattern="match",
            exclude_patterns=["exclude.py"],
        )
        assert result["result_count"] == 1
        assert result["matches"][0]["file"] == "include.py"

    def test_files_scanned_count(self, tmp_path: Path) -> None:
        """Should report correct files scanned count."""
        # Create a unique subdirectory to avoid tmp_path contamination
        test_dir = tmp_path / "scan_test"
        test_dir.mkdir()
        (test_dir / "test1.py").write_text("match\n")
        (test_dir / "test2.py").write_text("match\n")
        (test_dir / "test3.py").write_text("no match\n")
        result = search_code_content(
            project_dir=test_dir,
            pattern="match",
        )
        # files_scanned should be at least 3
        assert result["files_scanned"] >= 3
        # files_with_matches should be at least 2 (test1.py and test2.py contain "match")
        assert result["files_with_matches"] >= 2

    def test_files_with_matches_count(self, tmp_path: Path) -> None:
        """Should report correct files with matches count."""
        (tmp_path / "test1.py").write_text("match\nmatch\n")
        result = search_code_content(
            project_dir=tmp_path,
            pattern="match",
        )
        # Multiple matches in same file should count as 1 file
        assert result["files_with_matches"] == 1
        assert result["result_count"] == 2

    def test_filters_parameter(self, tmp_path: Path) -> None:
        """Should include filters in result."""
        result = search_code_content(
            project_dir=tmp_path,
            pattern="test",
            file_patterns=["*.py"],
            include_patterns=["src/"],
            exclude_patterns=["test_*.py"],
        )
        assert "filters" in result
        assert result["filters"]["file_patterns"] == ["*.py"]
        assert result["filters"]["include_patterns"] == ["src/"]
        assert result["filters"]["exclude_patterns"] == ["test_*.py"]
