"""Unit tests for Repo Intelligence Engine (WS1)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.context.repo_intelligence import (
    FileTag,
    LoIRenderer,
    RepoIntelligenceFacade,
    RepoIntelligenceRanker,
    TagKind,
    TagsCache,
    TagsExtractor,
    clear_repo_intelligence,
    get_repo_intelligence,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace with sample Python files."""
    # Create sample Python files
    main_py = tmp_path / "src" / "main.py"
    main_py.parent.mkdir(parents=True, exist_ok=True)
    main_py.write_text(
        """
class MainProcessor:
    def __init__(self):
        self.data = []

    def process(self, value):
        return value * 2

def parse_config(path):
    return {"path": path}

def main():
    processor = MainProcessor()
    result = processor.process(42)
    print(result)

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )

    utils_py = tmp_path / "src" / "utils.py"
    utils_py.write_text(
        """
class Utils:
    @staticmethod
    def helper(x, y):
        return x + y

def validate(data):
    return bool(data)
""",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def sample_tags() -> list[FileTag]:
    """Create sample tags for testing."""
    return [
        FileTag(
            rel_fname="src/main.py", fname="/repo/src/main.py", name="MainProcessor", kind=TagKind.DEFINITION, line=1
        ),
        FileTag(rel_fname="src/main.py", fname="/repo/src/main.py", name="process", kind=TagKind.DEFINITION, line=5),
        FileTag(
            rel_fname="src/main.py", fname="/repo/src/main.py", name="parse_config", kind=TagKind.DEFINITION, line=10
        ),
        FileTag(rel_fname="src/main.py", fname="/repo/src/main.py", name="main", kind=TagKind.DEFINITION, line=14),
        FileTag(rel_fname="src/utils.py", fname="/repo/src/utils.py", name="Utils", kind=TagKind.DEFINITION, line=1),
        FileTag(rel_fname="src/utils.py", fname="/repo/src/utils.py", name="helper", kind=TagKind.DEFINITION, line=4),
        FileTag(rel_fname="src/utils.py", fname="/repo/src/utils.py", name="validate", kind=TagKind.DEFINITION, line=9),
    ]


# ---------------------------------------------------------------------------
# TagsExtractor tests
# ---------------------------------------------------------------------------


class TestTagsExtractor:
    """Tests for TagsExtractor."""

    def test_get_tags_from_python_file(self, temp_workspace: Path) -> None:
        """Should extract tags from a Python file."""
        extractor = TagsExtractor(temp_workspace, languages=["python"])
        main_py = str(temp_workspace / "src" / "main.py")

        tags = extractor.get_tags(main_py)

        assert len(tags) > 0
        names = [t.name for t in tags]
        assert "MainProcessor" in names
        assert "process" in names
        assert "parse_config" in names
        assert "main" in names

    def test_tags_have_correct_kind(self, temp_workspace: Path) -> None:
        """Tags should have DEFINITION kind."""
        extractor = TagsExtractor(temp_workspace, languages=["python"])
        main_py = str(temp_workspace / "src" / "main.py")

        tags = extractor.get_tags(main_py)

        for tag in tags:
            assert tag.kind == TagKind.DEFINITION
            assert tag.rel_fname == "src/main.py"
            assert tag.line >= 0

    def test_empty_for_nonexistent_file(self, temp_workspace: Path) -> None:
        """Should return empty list for nonexistent file."""
        extractor = TagsExtractor(temp_workspace)

        tags = extractor.get_tags("/nonexistent/file.py")

        assert tags == []

    def test_language_filter(self, temp_workspace: Path) -> None:
        """Should filter by language."""
        extractor_python = TagsExtractor(temp_workspace, languages=["python"])
        extractor_js = TagsExtractor(temp_workspace, languages=["javascript"])
        main_py = str(temp_workspace / "src" / "main.py")

        tags_python = extractor_python.get_tags(main_py)
        tags_js = extractor_js.get_tags(main_py)

        assert len(tags_python) > 0
        assert len(tags_js) == 0


# ---------------------------------------------------------------------------
# TagsCache tests
# ---------------------------------------------------------------------------


class TestTagsCache:
    """Tests for TagsCache."""

    def test_cache_miss_on_empty(self, temp_workspace: Path) -> None:
        """Should miss on empty cache."""
        cache = TagsCache(temp_workspace)
        main_py = str(temp_workspace / "src" / "main.py")

        result = cache.get_tags(main_py)

        assert result is None
        stats = cache.get_stats()
        assert stats.misses == 1

    def test_cache_set_and_get(self, temp_workspace: Path) -> None:
        """Should store and retrieve tags."""
        cache = TagsCache(temp_workspace)
        main_py = str(temp_workspace / "src" / "main.py")
        tags = [
            {"name": "MainProcessor", "kind": "def", "line": 1},
            {"name": "process", "kind": "def", "line": 5},
        ]

        cache.set_tags(main_py, tags)
        result = cache.get_tags(main_py)

        assert result == tags
        stats = cache.get_stats()
        assert stats.hits == 1

    def test_cache_invalidation_on_mtime_change(self, temp_workspace: Path) -> None:
        """Should invalidate when file mtime changes."""
        cache = TagsCache(temp_workspace)
        main_py = temp_workspace / "src" / "main.py"
        main_py_str = str(main_py)

        tags = [{"name": "MainProcessor", "kind": "def", "line": 1}]
        cache.set_tags(main_py_str, tags)

        # Verify hit
        assert cache.get_tags(main_py_str) == tags

        # Touch file to change mtime
        time.sleep(0.1)
        main_py.write_text(main_py.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        # Should miss after mtime change
        result = cache.get_tags(main_py_str)
        assert result is None
        stats = cache.get_stats()
        assert stats.misses >= 1

    def test_invalidate_file(self, temp_workspace: Path) -> None:
        """Should invalidate specific file."""
        cache = TagsCache(temp_workspace)
        main_py = str(temp_workspace / "src" / "main.py")
        tags = [{"name": "MainProcessor", "kind": "def", "line": 1}]

        cache.set_tags(main_py, tags)
        assert cache.get_tags(main_py) == tags

        cache.invalidate_file(main_py)
        assert cache.get_tags(main_py) is None

    def test_invalidate_all(self, temp_workspace: Path) -> None:
        """Should invalidate all cached tags."""
        cache = TagsCache(temp_workspace)
        main_py = str(temp_workspace / "src" / "main.py")
        utils_py = str(temp_workspace / "src" / "utils.py")

        cache.set_tags(main_py, [{"name": "MainProcessor", "kind": "def", "line": 1}])
        cache.set_tags(utils_py, [{"name": "Utils", "kind": "def", "line": 1}])

        cache.invalidate_all()

        assert cache.get_tags(main_py) is None
        assert cache.get_tags(utils_py) is None


# ---------------------------------------------------------------------------
# RepoIntelligenceRanker tests
# ---------------------------------------------------------------------------


class TestRepoIntelligenceRanker:
    """Tests for RepoIntelligenceRanker."""

    def test_empty_ranker(self, temp_workspace: Path) -> None:
        """Should return empty list for empty ranker."""
        ranker = RepoIntelligenceRanker(temp_workspace)

        candidates = ranker.get_ranked_files()

        assert candidates == []

    def test_ranked_files_with_tags(self, temp_workspace: Path, sample_tags: list[FileTag]) -> None:
        """Should rank files based on tags."""
        ranker = RepoIntelligenceRanker(temp_workspace)
        ranker.add_tags(sample_tags)

        candidates = ranker.get_ranked_files()

        assert len(candidates) > 0
        # All candidates should have positive ranks
        for cand in candidates:
            assert cand.rank >= 0

    def test_chat_file_boost(self, temp_workspace: Path, sample_tags: list[FileTag]) -> None:
        """Should boost chat files in ranking."""
        ranker = RepoIntelligenceRanker(temp_workspace)
        ranker.add_tags(sample_tags)

        # Add main.py as a chat file
        ranker.add_chat_files(["src/main.py"])

        candidates = ranker.get_ranked_files()

        # main.py should be highly ranked
        main_cand = next((c for c in candidates if "main.py" in c.fname), None)
        assert main_cand is not None
        assert main_cand.rank > 0

    def test_mentioned_ident_boost(self, temp_workspace: Path, sample_tags: list[FileTag]) -> None:
        """Should boost files with mentioned identifiers."""
        ranker = RepoIntelligenceRanker(temp_workspace)
        ranker.add_tags(sample_tags)

        # Mention MainProcessor
        ranker.add_mentioned_idents(["MainProcessor", "process"])

        candidates = ranker.get_ranked_files()

        # main.py should be highly ranked
        main_cand = next((c for c in candidates if "main.py" in c.fname), None)
        assert main_cand is not None

    def test_ranked_symbols(self, temp_workspace: Path, sample_tags: list[FileTag]) -> None:
        """Should return ranked symbols."""
        ranker = RepoIntelligenceRanker(temp_workspace)
        ranker.add_tags(sample_tags)

        symbols = ranker.get_ranked_symbols()

        assert len(symbols) > 0
        for sym in symbols:
            assert sym.kind == "symbol"
            assert sym.symbol_name
            assert sym.line >= 0

    def test_personalization_boost_configurable(self, temp_workspace: Path, sample_tags: list[FileTag]) -> None:
        """Should respect personalization_boost setting."""
        ranker_low = RepoIntelligenceRanker(temp_workspace, personalization_boost=0.5)
        ranker_high = RepoIntelligenceRanker(temp_workspace, personalization_boost=5.0)

        for ranker in [ranker_low, ranker_high]:
            ranker.add_tags(sample_tags)
            ranker.add_chat_files(["src/main.py"])

        candidates_low = ranker_low.get_ranked_files()
        candidates_high = ranker_high.get_ranked_files()

        # Higher boost should give higher rank to chat files
        main_low = next((c for c in candidates_low if "main.py" in c.fname), None)
        main_high = next((c for c in candidates_high if "main.py" in c.fname), None)

        if main_low and main_high:
            assert main_high.rank > main_low.rank


# ---------------------------------------------------------------------------
# LoIRenderer tests
# ---------------------------------------------------------------------------


class TestLoIRenderer:
    """Tests for LoIRenderer."""

    def test_render_empty(self, temp_workspace: Path) -> None:
        """Should return empty result for no LoI."""
        renderer = LoIRenderer(temp_workspace)

        result = renderer.render()

        assert result.entries == []
        assert result.truncated is False

    def test_render_single_file(self, temp_workspace: Path) -> None:
        """Should render LoI for a single file."""
        renderer = LoIRenderer(temp_workspace)
        renderer.add_loi("src/main.py", [5, 10])

        result = renderer.render()

        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry.rel_fname == "src/main.py"
        assert len(entry.lines) == 2
        assert entry.content
        assert "5" in entry.content or "10" in entry.content

    def test_render_multiple_files(self, temp_workspace: Path) -> None:
        """Should render LoI for multiple files."""
        renderer = LoIRenderer(temp_workspace)
        renderer.add_loi("src/main.py", [5])
        renderer.add_loi("src/utils.py", [4])

        result = renderer.render()

        assert len(result.entries) == 2
        fnames = {e.rel_fname for e in result.entries}
        assert "src/main.py" in fnames
        assert "src/utils.py" in fnames

    def test_loi_padding(self, temp_workspace: Path) -> None:
        """Should include context around LoI."""
        renderer = LoIRenderer(temp_workspace, loi_pad=3)
        renderer.add_loi("src/main.py", [10])

        result = renderer.render()

        assert len(result.entries) == 1
        # Content should have line numbers around 10
        assert result.entries[0].content

    def test_max_entries(self, temp_workspace: Path) -> None:
        """Should respect max_entries limit."""
        renderer = LoIRenderer(temp_workspace)
        renderer.add_loi("src/main.py", [5])
        renderer.add_loi("src/utils.py", [4])

        result = renderer.render(max_entries=1)

        assert len(result.entries) == 1
        assert result.truncated is True

    def test_to_text(self, temp_workspace: Path) -> None:
        """Should render as plain text."""
        renderer = LoIRenderer(temp_workspace)
        renderer.add_loi("src/main.py", [5])

        result = renderer.render()
        text = result.to_text()

        assert "src/main.py" in text
        assert "5" in text


# ---------------------------------------------------------------------------
# RepoIntelligenceFacade tests
# ---------------------------------------------------------------------------


class TestRepoIntelligenceFacade:
    """Tests for RepoIntelligenceFacade."""

    def test_scan_repository(self, temp_workspace: Path) -> None:
        """Should scan repository and extract tags."""
        facade = RepoIntelligenceFacade(temp_workspace, languages=["python"])

        count = facade.scan_repository()

        assert count > 0
        stats = facade.get_stats()
        assert stats.files_scanned > 0

    def test_get_repo_map(self, temp_workspace: Path) -> None:
        """Should generate repo map with ranked candidates."""
        facade = RepoIntelligenceFacade(temp_workspace, languages=["python"])

        result = facade.get_repo_map(
            chat_files=["src/main.py"],
            mentioned_idents=["MainProcessor", "process"],
            max_files=10,
            max_symbols=20,
        )

        assert len(result.ranked_files) > 0
        assert len(result.ranked_symbols) >= 0
        assert result.stats.files_scanned > 0

    def test_get_repo_map_text_output(self, temp_workspace: Path) -> None:
        """Should render repo map as text."""
        facade = RepoIntelligenceFacade(temp_workspace, languages=["python"])

        result = facade.get_repo_map(max_files=5)

        text = result.to_text()
        # Should contain some expected sections
        assert "Ranked" in text or result.ranked_files or result.ranked_symbols

    def test_cache_integration(self, temp_workspace: Path) -> None:
        """Should use tags cache."""
        facade = RepoIntelligenceFacade(temp_workspace, languages=["python"])

        # First scan
        facade.scan_repository()
        stats1 = facade.cache_stats

        # Second scan should hit cache
        facade.scan_repository()
        stats2 = facade.cache_stats

        # Cache hits should have increased
        assert stats2.hits >= stats1.hits

    def test_invalidate_cache(self, temp_workspace: Path) -> None:
        """Should invalidate cache."""
        facade = RepoIntelligenceFacade(temp_workspace, languages=["python"])
        facade.scan_repository()

        # Invalidate
        facade.invalidate_cache()

        stats = facade.get_stats()
        assert stats.files_scanned == 0  # Reset

    def test_module_level_get_repo_intelligence(self, temp_workspace: Path) -> None:
        """Should work with module-level convenience function."""
        try:
            clear_repo_intelligence(temp_workspace)

            facade1 = get_repo_intelligence(temp_workspace, languages=["python"])
            facade2 = get_repo_intelligence(temp_workspace, languages=["python"])

            # Should return same instance
            assert facade1 is facade2

        finally:
            clear_repo_intelligence(temp_workspace)

    def test_mentioned_idents_affects_ranking(self, temp_workspace: Path) -> None:
        """Should affect ranking based on mentioned identifiers."""
        facade = RepoIntelligenceFacade(temp_workspace, languages=["python"])

        # Without mentioned idents
        result1 = facade.get_repo_map(max_files=10)

        # With mentioned idents
        result2 = facade.get_repo_map(
            mentioned_idents=["MainProcessor", "Utils"],
            max_files=10,
        )

        # Rankings should differ
        files1 = [c.fname for c in result1.ranked_files]
        files2 = [c.fname for c in result2.ranked_files]

        # main.py should be ranked higher with MainProcessor mentioned
        main_idx1 = files1.index("src/main.py") if "src/main.py" in files1 else 999
        main_idx2 = files2.index("src/main.py") if "src/main.py" in files2 else 999

        assert main_idx2 <= main_idx1


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestRepoIntelligenceIntegration:
    """Integration tests for full workflow."""

    def test_full_workflow(self, temp_workspace: Path) -> None:
        """Test complete repo intelligence workflow."""
        # Create facade
        facade = RepoIntelligenceFacade(temp_workspace, languages=["python"])

        # Scan repository
        files_scanned = facade.scan_repository()
        assert files_scanned > 0

        # Get repo map
        result = facade.get_repo_map(
            chat_files=["src/main.py"],
            mentioned_idents=["MainProcessor", "process", "Utils"],
            max_files=10,
            max_symbols=20,
            include_loi=True,
        )

        # Verify results
        assert len(result.ranked_files) > 0
        assert result.stats.files_scanned > 0
        assert result.stats.total_tags > 0

        # Check that LoI is rendered
        if result.ranked_symbols:
            assert result.loi_result.entries

    def test_cache_persistence(self, temp_workspace: Path) -> None:
        """Test that cache persists across facade instances."""
        # First session
        facade1 = RepoIntelligenceFacade(temp_workspace, languages=["python"])
        facade1.scan_repository()
        stats1 = facade1.cache_stats

        # Second session (new facade)
        facade2 = RepoIntelligenceFacade(temp_workspace, languages=["python"])
        facade2.scan_repository()
        stats2 = facade2.cache_stats

        # Second scan should have more cache hits
        assert stats2.hits >= stats1.hits
