"""Repo Intelligence Facade - unified entry point.

This module provides a unified facade that combines:
- Tags extraction with persistent caching
- Personalized ranking
- Lines-of-interest rendering

The facade integrates with TieredAssetCacheManager for workspace-persistent
storage and provides a simple high-level API for code intelligence.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.repo_intelligence.cache import (
    TagsCache,
    TagsCacheStats,
    get_tags_cache,
)
from polaris.kernelone.context.repo_intelligence.ranker import (
    RankedCandidate,
    RepoIntelligenceRanker,
)
from polaris.kernelone.context.repo_intelligence.renderer import (
    LoIEntry,
    LoIRenderer,
    LoIRenderResult,
    render_loi,
)
from polaris.kernelone.context.repo_intelligence.tags import (
    FileTag,
    TagKind,
    TagsExtractor,
    get_language_from_filename,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skip directories
# ---------------------------------------------------------------------------

SKIP_DIRS: set[str] = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".polaris",
    ".aider",
}

# Maximum number of tags to keep in memory
MAX_TAGS = 100_000

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RepoIntelligenceStats:
    """Statistics for repo intelligence operations."""

    files_scanned: int = 0
    files_with_tags: int = 0
    total_tags: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    ranked_files: int = 0
    ranked_symbols: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "files_with_tags": self.files_with_tags,
            "total_tags": self.total_tags,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "ranked_files": self.ranked_files,
            "ranked_symbols": self.ranked_symbols,
        }


@dataclass
class RepoMapResult:
    """Result of repo map generation.

    Attributes:
        ranked_files: Ranked file candidates.
        ranked_symbols: Ranked symbol candidates.
        loi_result: Lines-of-interest rendering result.
        stats: Operation statistics.
    """

    ranked_files: list[RankedCandidate] = field(default_factory=list)
    ranked_symbols: list[RankedCandidate] = field(default_factory=list)
    loi_result: LoIRenderResult = field(default_factory=LoIRenderResult)
    stats: RepoIntelligenceStats = field(default_factory=RepoIntelligenceStats)

    def to_text(self) -> str:
        """Render as plain text for LLM consumption."""
        lines: list[str] = []

        if self.ranked_files:
            lines.append("【Ranked Files】")
            for cand in self.ranked_files[:20]:
                lines.append(f"  {cand.rank:.3f} {cand.fname}")

        if self.ranked_symbols:
            lines.append("\n【Ranked Symbols】")
            for cand in self.ranked_symbols[:50]:
                lines.append(f"  {cand.rank:.3f} {cand.fname}:{cand.line + 1} {cand.symbol_name}")

        if self.loi_result.entries:
            lines.append("\n【Code Context】")
            lines.append(self.loi_result.to_text())

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# RepoIntelligenceFacade
# ---------------------------------------------------------------------------


class RepoIntelligenceFacade:
    """Unified facade for repo intelligence operations.

    This facade provides a high-level API that combines tags extraction,
    caching, ranking, and LoI rendering. It integrates with the
    TieredAssetCacheManager for workspace-persistent storage.

    Usage:
        facade = RepoIntelligenceFacade(workspace="/repo")

        # Scan repository for tags
        facade.scan_repository()

        # Get ranked candidates based on conversation context
        result = facade.get_repo_map(
            chat_files=["src/main.py"],
            mentioned_idents={"main", "parse", "config"},
            max_files=20,
            max_symbols=50,
        )

        print(result.to_text())
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        languages: list[str] | None = None,
        cache: TagsCache | None = None,
        personalization_boost: float = 1.0,
        loi_pad: int = 5,
    ) -> None:
        self.workspace = str(workspace)
        self._languages = languages
        self._cache = cache or get_tags_cache(self.workspace)
        self._extractor = TagsExtractor(self.workspace, languages=languages)
        self._ranker = RepoIntelligenceRanker(
            self.workspace,
            personalization_boost=personalization_boost,
        )
        self._loi_renderer = LoIRenderer(
            self.workspace,
            loi_pad=loi_pad,
        )
        self._stats = RepoIntelligenceStats()
        self._tags_by_file: dict[str, list[FileTag]] = defaultdict(list)
        self._all_tags: list[FileTag] = []
        self._scanned = False

    @property
    def cache_stats(self) -> TagsCacheStats:
        """Get cache statistics."""
        return self._cache.get_stats()

    def scan_repository(
        self,
        *,
        max_files: int = 500,
        progress_callback: Callable[[str], None] | None = None,
    ) -> int:
        """Scan repository for tags.

        Args:
            max_files: Maximum number of files to scan.
            progress_callback: Optional callback for progress reporting.

        Returns:
            Number of files scanned.
        """
        if self._scanned:
            return self._stats.files_scanned

        files_processed = 0
        for abs_path in self._iter_source_files(max_files):
            if progress_callback:
                progress_callback(abs_path)

            tags = self._extract_and_cache_tags(abs_path)
            if tags:
                self._tags_by_file[abs_path] = tags
                self._all_tags.extend(tags)
                self._stats.files_with_tags += 1

            files_processed += 1

        # Enforce tag cache size limit
        if len(self._all_tags) > MAX_TAGS:
            logger.warning(
                "Tag cache exceeded limit (%d > %d), truncating to %d",
                len(self._all_tags),
                MAX_TAGS,
                MAX_TAGS,
            )
            self._all_tags = self._all_tags[:MAX_TAGS]

        self._scanned = True
        self._stats.files_scanned = files_processed
        return files_processed

    def _iter_source_files(self, max_files: int) -> Iterator[str]:
        """Iterate over source files in the workspace."""
        root = Path(self.workspace)
        count = 0

        for dirpath, dirnames, filenames in os.walk(root):
            # Filter skip directories in-place
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

            for name in filenames:
                if count >= max_files:
                    return

                abs_path = os.path.join(dirpath, name)
                rel_path = os.path.relpath(abs_path, self.workspace)

                # Skip cache directories
                if rel_path.startswith(".polaris"):
                    continue

                # Check language support
                lang = get_language_from_filename(name)
                if lang and self._languages and lang in self._languages:
                    yield abs_path
                    count += 1

    def _extract_and_cache_tags(self, abs_path: str) -> list[FileTag]:
        """Extract tags with cache support."""
        # Check cache first
        cached = self._cache.get_tags(abs_path)
        if cached is not None:
            self._stats.cache_hits += 1
            # Convert cached dicts back to FileTag
            rel_path = self._get_rel_path(abs_path)
            return [
                FileTag(
                    rel_fname=tag.get("rel_fname", rel_path),
                    fname=tag.get("fname", abs_path),
                    name=tag.get("name", ""),
                    kind=TagKind(tag.get("kind", "def")),
                    line=tag.get("line", 0),
                )
                for tag in cached
            ]

        self._stats.cache_misses += 1

        # Extract tags
        tags = self._extractor.get_tags(abs_path)
        self._stats.total_tags += len(tags)

        # Cache the tags
        if tags:
            tag_dicts = [
                {
                    "rel_fname": tag.rel_fname,
                    "fname": tag.fname,
                    "name": tag.name,
                    "kind": tag.kind.value,
                    "line": tag.line,
                }
                for tag in tags
            ]
            self._cache.set_tags(abs_path, tag_dicts)

        return tags

    def _get_rel_path(self, abs_path: str) -> str:
        """Get relative path from workspace."""
        try:
            return os.path.relpath(abs_path, self.workspace)
        except ValueError:
            return abs_path

    def get_tags_for_file(self, abs_path: str) -> list[FileTag]:
        """Get tags for a specific file (with caching)."""
        if abs_path in self._tags_by_file:
            return self._tags_by_file[abs_path]
        return self._extract_and_cache_tags(abs_path)

    def get_repo_map(
        self,
        *,
        chat_files: Iterable[str] | None = None,
        mentioned_idents: Iterable[str] | None = None,
        mentioned_fnames: Iterable[str] | None = None,
        max_files: int = 50,
        max_symbols: int = 100,
        include_loi: bool = True,
    ) -> RepoMapResult:
        """Generate repo map with personalized ranking.

        Args:
            chat_files: Files directly in the conversation.
            mentioned_idents: Identifiers mentioned in conversation.
            mentioned_fnames: File paths mentioned in conversation.
            max_files: Maximum number of ranked files.
            max_symbols: Maximum number of ranked symbols.
            include_loi: Whether to include LoI rendering.

        Returns:
            RepoMapResult with ranked candidates and LoI.
        """
        # Ensure repository is scanned
        if not self._scanned:
            self.scan_repository()

        # Reset ranker
        self._ranker = RepoIntelligenceRanker(
            self.workspace,
            personalization_boost=self._ranker.personalization_boost,
        )

        # Add all tags to ranker
        for tag in self._all_tags:
            self._ranker.add_tags([tag])

        # Add personalization
        if chat_files:
            self._ranker.add_chat_files(chat_files)

        if mentioned_idents:
            self._ranker.add_mentioned_idents(mentioned_idents)

        if mentioned_fnames:
            self._ranker.add_mentioned_fnames(mentioned_fnames)

        # Get ranked candidates
        ranked_files = self._ranker.get_ranked_files(max_count=max_files)
        ranked_symbols = self._ranker.get_ranked_symbols(max_count=max_symbols)

        self._stats.ranked_files = len(ranked_files)
        self._stats.ranked_symbols = len(ranked_symbols)

        # Generate LoI
        loi_result = LoIRenderResult()
        if include_loi and ranked_symbols:
            loi_file_lines = [(cand.fname, cand.line) for cand in ranked_symbols[:max_symbols]]
            self._loi_renderer.clear_loi()
            self._loi_renderer.add_loi_from_tags(loi_file_lines)
            loi_result = self._loi_renderer.render()

        return RepoMapResult(
            ranked_files=ranked_files,
            ranked_symbols=ranked_symbols,
            loi_result=loi_result,
            stats=self._stats,
        )

    def invalidate_cache(self, abs_path: str | None = None) -> None:
        """Invalidate cache.

        Args:
            abs_path: Specific file to invalidate, or None for all.
        """
        if abs_path:
            self._cache.invalidate_file(abs_path)
            self._tags_by_file.pop(abs_path, None)
            # Remove from _all_tags
            self._all_tags = [tag for tag in self._all_tags if tag.fname != abs_path]
            # Adjust stats for removed file
            self._stats.files_scanned = max(0, self._stats.files_scanned - 1)
        else:
            self._cache.invalidate_all()
            self._tags_by_file.clear()
            self._all_tags.clear()
            self._scanned = False
            # Reset stats
            self._stats = RepoIntelligenceStats()

    def get_stats(self) -> RepoIntelligenceStats:
        """Get operation statistics."""
        return self._stats


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


_repo_intelligence_instances: dict[str, RepoIntelligenceFacade] = {}


def get_repo_intelligence(
    workspace: str | Path,
    *,
    languages: list[str] | None = None,
    personalization_boost: float = 1.0,
) -> RepoIntelligenceFacade:
    """Get or create a RepoIntelligenceFacade for a workspace.

    Args:
        workspace: Workspace root path.
        languages: Optional language filter.
        personalization_boost: Personalization boost multiplier.

    Returns:
        RepoIntelligenceFacade instance.
    """
    workspace_str = str(workspace)
    cache_key = f"{workspace_str}:{','.join(languages or [])}"

    if cache_key not in _repo_intelligence_instances:
        _repo_intelligence_instances[cache_key] = RepoIntelligenceFacade(
            workspace,
            languages=languages,
            personalization_boost=personalization_boost,
        )

    return _repo_intelligence_instances[cache_key]


def clear_repo_intelligence(workspace: str | Path) -> None:
    """Clear repo intelligence instance (useful for testing)."""
    workspace_str = str(workspace)
    # Clear all instances that match the workspace
    keys_to_remove = [k for k in _repo_intelligence_instances if k.startswith(workspace_str)]
    for key in keys_to_remove:
        del _repo_intelligence_instances[key]


__all__ = [
    "LoIEntry",
    "LoIRenderResult",
    "RankedCandidate",
    "RepoIntelligenceFacade",
    "RepoIntelligenceStats",
    "RepoMapResult",
    "clear_repo_intelligence",
    "get_repo_intelligence",
    "render_loi",
]
