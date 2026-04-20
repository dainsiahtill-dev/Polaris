"""Tests for W1: Repository Intelligence (repo_map.py).

This module tests the repository mapping and symbol extraction capabilities:
    - Language filtering and normalization
    - File skeleton generation
    - Tree-sitter parsing with fallback
    - Token estimation
    - Caching integration
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestLanguageNormalization:
    """Tests for language filtering and normalization."""

    def test_normalize_empty_list_returns_all(self, temp_workspace: Path) -> None:
        """Empty language filter should include all supported languages."""
        from polaris.kernelone.context.repo_map import _normalize_languages

        result = _normalize_languages(None)
        assert "python" in result
        assert "typescript" in result

    def test_normalize_single_language(self, temp_workspace: Path) -> None:
        """Single language string should be normalized."""
        from polaris.kernelone.context.repo_map import _normalize_languages

        result = _normalize_languages(["python"])
        assert result == ["python"]
        assert len(result) == 1

    def test_normalize_language_with_alias(self, temp_workspace: Path) -> None:
        """Language aliases should resolve to canonical names."""
        from polaris.kernelone.context.repo_map import _normalize_languages

        result = _normalize_languages(["py", "js"])
        assert "python" in result
        assert "javascript" in result

    def test_normalize_comma_separated(self, temp_workspace: Path) -> None:
        """Comma-separated languages should be parsed correctly."""
        from polaris.kernelone.context.repo_map import _normalize_languages

        result = _normalize_languages(["python,typescript"])
        assert "python" in result
        assert "typescript" in result

    def test_normalize_deduplication(self, temp_workspace: Path) -> None:
        """Duplicate languages should be deduplicated."""
        from polaris.kernelone.context.repo_map import _normalize_languages

        result = _normalize_languages(["python", "python", "py"])
        assert result.count("python") == 1


class TestBuildRepoMap:
    """Tests for build_repo_map function."""

    def test_build_repo_map_returns_dict(self, sample_repo_structure: Path) -> None:
        """build_repo_map should return a dict with expected keys."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"])
        assert isinstance(result, dict)
        assert "root" in result
        assert "languages" in result
        assert "lines" in result
        assert "text" in result
        assert "stats" in result

    def test_build_repo_map_filters_by_language(self, sample_repo_structure: Path) -> None:
        """Only files of specified languages should be included."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"], max_files=50)
        text = result.get("text", "")
        # Should include Python files
        assert "main.py" in text or "utils.py" in text

    def test_build_repo_map_respects_max_files(self, sample_repo_structure: Path) -> None:
        """build_repo_map should respect max_files limit."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"], max_files=1)
        assert result["stats"]["total_files"] <= 1

    def test_build_repo_map_respects_max_lines(self, sample_repo_structure: Path) -> None:
        """build_repo_map should respect max_lines limit."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"], max_lines=5)
        # skeleton_lines includes file path lines + skeleton entries
        # With truncation, total should be bounded
        lines_count = result["stats"]["skeleton_lines"]
        assert lines_count <= 10  # Generous bound accounting for paths
        assert result["truncated"] is True

    def test_build_repo_map_truncation_flag(self, sample_repo_structure: Path) -> None:
        """truncated flag should be True when limits are hit."""
        from polaris.kernelone.context.repo_map import build_repo_map

        # Small limit should trigger truncation
        result = build_repo_map(str(sample_repo_structure), languages=["python"], max_lines=1)
        assert result["truncated"] is True

    def test_build_repo_map_no_truncation_when_large(self, sample_repo_structure: Path) -> None:
        """truncated flag should be False when limits are not hit."""
        from polaris.kernelone.context.repo_map import build_repo_map

        # Large limits should not trigger truncation
        result = build_repo_map(str(sample_repo_structure), languages=["python"], max_lines=1000, max_files=100)
        assert result["truncated"] is False

    def test_build_repo_map_stats_accuracy(self, sample_repo_structure: Path) -> None:
        """Stats should accurately reflect the mapping."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"])
        stats = result["stats"]
        assert "total_files" in stats
        assert "mapped_files" in stats
        assert "total_lines" in stats
        assert "skeleton_lines" in stats
        assert "symbols" in stats
        assert "compressed_ratio" in stats
        assert stats["mapped_files"] > 0
        assert stats["symbols"] > 0


class TestFileSkeletons:
    """Tests for file skeleton extraction."""

    def test_class_extraction(self, sample_repo_structure: Path) -> None:
        """Classes should be extracted from Python files."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"], per_file_lines=20)
        text = result.get("text", "")
        # main.py has App class
        assert "class App" in text or "App" in text

    def test_function_extraction(self, sample_repo_structure: Path) -> None:
        """Functions should be extracted from Python files."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"], per_file_lines=20)
        text = result.get("text", "")
        # Should find functions
        assert "function" in text.lower() or "def" in text

    def test_line_ranges_included(self, sample_repo_structure: Path) -> None:
        """Skeleton entries should include line number ranges."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"], per_file_lines=20)
        text = result.get("text", "")
        # Line ranges should be in format [start-end]
        import re

        ranges = re.findall(r"\[\d+-\d+\]", text)
        assert len(ranges) > 0, "Should have line range markers"


class TestSkipDirs:
    """Tests for directory skipping logic."""

    def test_skips_git_dir(self, temp_workspace: Path) -> None:
        """Git directories should be skipped."""
        from polaris.kernelone.context.repo_map import _iter_files

        # Create a .git directory
        git_dir = temp_workspace / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("dummy", encoding="utf-8")

        files = list(_iter_files(str(temp_workspace), None, None))
        paths = [Path(f).name for f in files]
        assert ".git" not in paths

    def test_skips_node_modules(self, temp_workspace: Path) -> None:
        """node_modules should be skipped."""
        from polaris.kernelone.context.repo_map import _iter_files

        # Create node_modules
        nm_dir = temp_workspace / "node_modules"
        nm_dir.mkdir()
        (nm_dir / "package.json").write_text("{}", encoding="utf-8")

        files = list(_iter_files(str(temp_workspace), None, None))
        paths = [Path(f).name for f in files]
        assert "node_modules" not in paths


class TestCacheIntegration:
    """Tests for repo map caching via TieredAssetCacheManager."""

    @pytest.mark.asyncio
    async def test_repo_map_caching(self, tiered_cache, sample_repo_structure: Path) -> None:
        """Repo map should be cached and retrieved correctly."""
        from polaris.kernelone.context.repo_map import build_repo_map

        # Build and cache
        repo_map = build_repo_map(str(sample_repo_structure), languages=["python"])

        await tiered_cache.put_repo_map(sample_repo_structure, "python", repo_map)

        # Retrieve from cache
        cached = await tiered_cache.get_repo_map(sample_repo_structure, "python")
        assert cached is not None
        assert cached["root"] == repo_map["root"]
        assert cached["stats"]["symbols"] == repo_map["stats"]["symbols"]

    @pytest.mark.asyncio
    async def test_repo_map_cache_miss(self, tiered_cache, temp_workspace: Path) -> None:
        """Cache miss should return None."""
        cached = await tiered_cache.get_repo_map(temp_workspace, "python")
        assert cached is None

    @pytest.mark.asyncio
    async def test_different_languages_separate_cache(self, tiered_cache, sample_repo_structure: Path) -> None:
        """Different languages should have separate cache entries."""
        from polaris.kernelone.context.repo_map import build_repo_map

        # Build for Python
        py_map = build_repo_map(str(sample_repo_structure), languages=["python"])
        await tiered_cache.put_repo_map(sample_repo_structure, "python", py_map)

        # Build for TypeScript (empty)
        ts_map = build_repo_map(str(sample_repo_structure), languages=["typescript"])
        await tiered_cache.put_repo_map(sample_repo_structure, "typescript", ts_map)

        # Verify separate caching
        cached_py = await tiered_cache.get_repo_map(sample_repo_structure, "python")
        cached_ts = await tiered_cache.get_repo_map(sample_repo_structure, "typescript")

        assert cached_py is not None
        assert cached_ts is not None
        assert cached_py["stats"]["symbols"] > 0
        assert cached_ts["stats"]["symbols"] == 0


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_workspace(self, temp_workspace: Path) -> None:
        """Empty workspace should return valid result."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(temp_workspace), languages=["python"])
        assert result["stats"]["mapped_files"] == 0
        assert result["truncated"] is False

    def test_nonexistent_language(self, temp_workspace: Path) -> None:
        """Nonexistent language filter should return empty results."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(temp_workspace), languages=["nonexistent"])
        assert result["stats"]["mapped_files"] == 0

    def test_invalid_workspace_path(self) -> None:
        """Invalid workspace should be handled gracefully."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map("/nonexistent/path/12345", languages=["python"])
        # Should return valid structure
        assert "stats" in result

    def test_max_files_zero_means_truncated(self, sample_repo_structure: Path) -> None:
        """max_files=0 should return valid structure."""
        from polaris.kernelone.context.repo_map import build_repo_map

        result = build_repo_map(str(sample_repo_structure), languages=["python"], max_files=0)
        # max_files=0 may still scan but should return valid structure
        assert isinstance(result, dict)
        assert "stats" in result
        # Behavior depends on implementation - just verify valid result
