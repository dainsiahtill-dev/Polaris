"""Repo Intelligence Engine for KernelOne.

This package provides code intelligence capabilities:
- Tags extraction: tree-sitter def/ref symbol extraction
- Persistent cache: mtime-based tags cache with SQLite backend
- Personalized ranking: mention/symbol/chat file based ranking using PageRank
- LoI rendering: lines-of-interest neighborhood rendering

Architecture:
- This is NOT a replacement for graph truth (docs/graph/**)
- This is runtime intelligence for working-set assembly
- Integrates with TieredAssetCacheManager for workspace-persistent caching
"""

from __future__ import annotations

from polaris.kernelone.context.repo_intelligence.cache import (
    TagsCache,
    TagsCacheStats,
    get_tags_cache,
)
from polaris.kernelone.context.repo_intelligence.facade import (
    RepoIntelligenceFacade,
    clear_repo_intelligence,
    get_repo_intelligence,
)
from polaris.kernelone.context.repo_intelligence.ranker import (
    RankedCandidate,
    RepoIntelligenceRanker,
)
from polaris.kernelone.context.repo_intelligence.renderer import (
    LoIRenderer,
)
from polaris.kernelone.context.repo_intelligence.tags import (
    FileTag,
    TagKind,
    TagsExtractor,
    get_tags_for_file,
)

__all__ = [
    # Tags
    "FileTag",
    # Renderer
    "LoIRenderer",
    # Ranker
    "RankedCandidate",
    # Facade
    "RepoIntelligenceFacade",
    "RepoIntelligenceRanker",
    "TagKind",
    # Cache
    "TagsCache",
    "TagsCacheStats",
    "TagsExtractor",
    "clear_repo_intelligence",
    "get_repo_intelligence",
    "get_tags_cache",
    "get_tags_for_file",
]
