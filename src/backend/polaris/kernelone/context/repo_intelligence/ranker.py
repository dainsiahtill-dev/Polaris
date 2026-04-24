"""Personalized ranking using PageRank.

This module provides file/symbol ranking based on:
- Mention frequency (symbols mentioned in the conversation)
- Symbol/definition matching (identifiers matching path components)
- Chat file proximity (files directly in the conversation)
- Cross-file reference graph (PageRank over def/ref edges)

The ranking algorithm is inspired by Aider's approach but adapted for
KernelOne's architecture.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from polaris.kernelone.context.repo_intelligence.tags import FileTag, TagKind

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedCandidate:
    """A ranked file or symbol candidate.

    Attributes:
        fname: Relative file path.
        rank: Computed rank score.
        kind: "file" or "symbol".
        symbol_name: Symbol name if kind is "symbol".
        line: Line number if kind is "symbol".
    """

    fname: str
    rank: float
    kind: Literal["file", "symbol"] = "file"
    symbol_name: str = ""
    line: int = 0

    def __repr__(self) -> str:
        if self.kind == "symbol":
            return f"RankedCandidate({self.fname}:{self.line} {self.symbol_name}={self.rank:.4f})"
        return f"RankedCandidate({self.fname}={self.rank:.4f})"


# ---------------------------------------------------------------------------
# RepoIntelligenceRanker
# ---------------------------------------------------------------------------


class RepoIntelligenceRanker:
    """Personalized repo ranking using PageRank.

    This ranker computes a relevance score for each file/symbol based on:
    1. Mention frequency in the conversation
    2. Path component matching (identifiers in paths)
    3. Cross-file reference graph (PageRank)

    Usage:
        ranker = RepoIntelligenceRanker(workspace="/repo")
        ranker.add_tags(tags)  # Add extracted tags
        ranker.add_chat_files(["src/main.py", "src/utils.py"])
        ranker.add_mentioned_idents({"main", "utils", "parse"})

        candidates = ranker.get_ranked_files(max_count=20)
        for cand in candidates:
            print(f"{cand.fname}: {cand.rank}")
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        personalization_boost: float = 1.0,
    ) -> None:
        self.workspace = str(workspace)
        self._personalization_boost = personalization_boost

        # Data structures for ranking
        self._defines: dict[str, set[str]] = defaultdict(set)  # name -> {fname}
        self._references: dict[str, list[str]] = defaultdict(list)  # name -> [fname]
        self._definitions: dict[tuple[str, str], set[FileTag]] = defaultdict(set)  # (fname, name) -> {tags}
        self._all_files: set[str] = set()
        self._chat_rel_fnames: set[str] = set()
        self._mentioned_fnames: set[str] = set()
        self._mentioned_idents: set[str] = set()
        self._tags: list[FileTag] = []

    @property
    def personalization_boost(self) -> float:
        """Boost multiplier for personalized (chat/mentioned) files."""
        return self._personalization_boost

    @personalization_boost.setter
    def personalization_boost(self, value: float) -> None:
        """Set personalization boost multiplier."""
        self._personalization_boost = max(0.0, value)

    def add_tags(self, tags: Iterable[FileTag]) -> None:
        """Add tags from file processing.

        Args:
            tags: Iterable of FileTag objects.
        """
        for tag in tags:
            self._tags.append(tag)
            self._all_files.add(tag.rel_fname)

            if tag.kind == TagKind.DEFINITION:
                self._defines[tag.name].add(tag.rel_fname)
                key = (tag.rel_fname, tag.name)
                self._definitions[key].add(tag)
            elif tag.kind == TagKind.REFERENCE:
                self._references[tag.name].append(tag.rel_fname)

    def add_chat_files(self, fnames: Iterable[str]) -> None:
        """Add files that are directly in the conversation.

        Args:
            fnames: Iterable of file paths (relative to workspace).
        """
        for fname in fnames:
            try:
                rel = Path(fname).relative_to(self.workspace)
                self._chat_rel_fnames.add(str(rel))
            except ValueError:
                self._chat_rel_fnames.add(fname)

    def add_mentioned_fnames(self, fnames: Iterable[str]) -> None:
        """Add files mentioned in the conversation.

        Args:
            fnames: Iterable of file paths.
        """
        for fname in fnames:
            try:
                rel = Path(fname).relative_to(self.workspace)
                self._mentioned_fnames.add(str(rel))
            except ValueError:
                self._mentioned_fnames.add(fname)

    def add_mentioned_idents(self, idents: Iterable[str]) -> None:
        """Add identifiers mentioned in the conversation.

        Args:
            idents: Iterable of symbol names.
        """
        for ident in idents:
            self._mentioned_idents.add(ident)

    def get_ranked_files(
        self,
        max_count: int = 50,
        *,
        include_chat_files: bool = True,
        min_rank: float = 0.0,
    ) -> list[RankedCandidate]:
        """Get ranked file candidates.

        Args:
            max_count: Maximum number of candidates to return.
            include_chat_files: Whether to include chat files in ranking.
            min_rank: Minimum rank threshold.

        Returns:
            List of ranked file candidates.
        """
        if not self._tags:
            return []

        # Build the reference graph
        ranked = self._compute_pagerank()

        # Apply personalization boost
        ranked = self._apply_personalization(ranked)

        # Filter and sort
        candidates = []
        for fname, rank in sorted(ranked.items(), key=lambda x: -x[1]):
            if (include_chat_files or fname not in self._chat_rel_fnames) and rank >= min_rank:
                candidates.append(RankedCandidate(fname=fname, rank=rank))

            if len(candidates) >= max_count:
                break

        return candidates

    def get_ranked_symbols(
        self,
        max_count: int = 100,
        *,
        exclude_chat_files: bool = True,
        min_rank: float = 0.0,
    ) -> list[RankedCandidate]:
        """Get ranked symbol candidates.

        Args:
            max_count: Maximum number of candidates to return.
            exclude_chat_files: Whether to exclude symbols from chat files.
            min_rank: Minimum rank threshold.

        Returns:
            List of ranked symbol candidates.
        """
        if not self._tags:
            return []

        # Compute symbol-level ranks
        ranked = self._compute_symbol_ranks()

        # Build candidates
        candidates = []
        for (fname, name), rank in sorted(ranked.items(), key=lambda x: -x[1]):
            if exclude_chat_files and fname in self._chat_rel_fnames:
                continue

            # Find line number from tags
            line = 0
            tags = self._definitions.get((fname, name), set())
            if tags:
                line = min(t.line for t in tags)

            if rank >= min_rank:
                candidates.append(
                    RankedCandidate(
                        fname=fname,
                        rank=rank,
                        kind="symbol",
                        symbol_name=name,
                        line=line,
                    )
                )

            if len(candidates) >= max_count:
                break

        return candidates

    def _compute_pagerank(self) -> dict[str, float]:
        """Compute PageRank over the file reference graph."""
        try:
            import networkx as nx
        except ImportError:
            logger.warning("RepoIntelligenceRanker: networkx not available, using simple ranking")
            return self._compute_simple_ranking()

        if not self._defines:
            return dict.fromkeys(self._all_files, 1.0)

        # Build graph
        graph = nx.MultiDiGraph()

        # Add all files as nodes
        for fname in self._all_files:
            graph.add_node(fname)

        # Add self-edges for definitions without references
        for name, definers in self._defines.items():
            if name not in self._references:
                for definer in definers:
                    graph.add_edge(definer, definer, weight=0.1, ident=name)

        # Add edges from references to definitions
        idents = set(self._defines.keys()) & set(self._references.keys())
        for ident in idents:
            definers = self._defines[ident]
            mul = self._compute_ident_multiplier(ident)

            for referencer, count in Counter(self._references[ident]).items():
                for definer in definers:
                    # Scale down high frequency mentions
                    scaled_count = math.sqrt(count)
                    graph.add_edge(
                        referencer,
                        definer,
                        weight=mul * scaled_count,
                        ident=ident,
                    )

        # Compute PageRank with personalization
        personalization = {}
        default_pers = 1.0 / len(self._all_files) if self._all_files else 0.0

        for fname in self._all_files:
            pers = default_pers

            if fname in self._chat_rel_fnames:
                pers += self._personalization_boost

            if fname in self._mentioned_fnames:
                pers = max(pers, self._personalization_boost)

            # Check path components
            path_obj = Path(fname)
            path_components = set(path_obj.parts)
            basename = path_obj.stem
            components = path_components | {path_obj.name, basename}

            matched = components & self._mentioned_idents
            if matched:
                pers += self._personalization_boost

            personalization[fname] = pers

        try:
            ranked = nx.pagerank(graph, weight="weight", personalization=personalization)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "RepoIntelligenceRanker: pagerank failed: %s, using fallback",
                exc,
            )
            return self._compute_simple_ranking()

        return ranked

    def _compute_symbol_ranks(self) -> dict[tuple[str, str], float]:
        """Compute symbol-level ranks by distributing file ranks."""
        file_ranks = self._compute_pagerank()
        symbol_ranks: dict[tuple[str, str], float] = defaultdict(float)

        # Build definition graph for symbol ranking
        self._build_symbol_graph()

        # Distribute rank from files to their definitions
        for fname, file_rank in file_ranks.items():
            defs_in_file = {name for (f, name), tags in self._definitions.items() if f == fname}
            if not defs_in_file:
                continue

            # Distribute file rank across definitions
            per_def = file_rank / len(defs_in_file)
            for name in defs_in_file:
                symbol_ranks[(fname, name)] += per_def

        return symbol_ranks

    def _build_symbol_graph(self) -> Any:
        """Build symbol-level reference graph."""
        try:
            import networkx as nx

            return nx.MultiDiGraph()
        except ImportError:
            return None

    def _compute_ident_multiplier(self, ident: str) -> float:
        """Compute weight multiplier for an identifier."""
        mul = 1.0

        # Boost if mentioned in conversation
        if ident in self._mentioned_idents:
            mul *= 10

        # Boost long compound names (snake/kebab/camel case with length >= 8)
        is_snake = "_" in ident and any(c.isalpha() for c in ident)
        is_kebab = "-" in ident and any(c.isalpha() for c in ident)
        is_camel = any(c.isupper() for c in ident) and any(c.islower() for c in ident)
        if (is_snake or is_kebab or is_camel) and len(ident) >= 8:
            mul *= 10

        # Reduce for very common names (defined in many places)
        if len(self._defines.get(ident, set())) > 5:
            mul *= 0.1

        # Reduce private names
        if ident.startswith("_"):
            mul *= 0.1

        return mul

    def _apply_personalization(
        self,
        ranked: dict[str, float],
    ) -> dict[str, float]:
        """Apply personalization boost to ranked files."""
        if self._personalization_boost <= 0:
            return ranked

        result = {}
        for fname, rank in ranked.items():
            boost = 1.0

            # Chat files get a significant boost
            if fname in self._chat_rel_fnames:
                boost += self._personalization_boost * 5

            # Mentioned files get a moderate boost
            if fname in self._mentioned_fnames:
                boost += self._personalization_boost

            # Path component matching
            path_obj = Path(fname)
            path_components = set(path_obj.parts)
            basename = path_obj.stem
            components = path_components | {path_obj.name, basename}

            if components & self._mentioned_idents:
                boost += self._personalization_boost * 2

            result[fname] = rank * boost

        return result

    def _compute_simple_ranking(self) -> dict[str, float]:
        """Compute simple ranking without PageRank (fallback)."""
        # Count baseline signal per file so fallback still produces deterministic,
        # non-empty candidates even without explicit mentions.
        scores: dict[str, float] = defaultdict(float)

        for tag in self._tags:
            if tag.kind == TagKind.DEFINITION:
                scores[tag.rel_fname] += 1.0
            elif tag.kind == TagKind.REFERENCE:
                scores[tag.rel_fname] += 0.3

            if tag.name in self._mentioned_idents:
                scores[tag.rel_fname] += 2.0

        for fname in self._chat_rel_fnames:
            scores[fname] += 5.0

        for fname in self._mentioned_fnames:
            scores[fname] += 3.0

        # Ensure all discovered files appear in fallback ranking.
        for fname in self._all_files:
            scores[fname] = max(scores.get(fname, 0.0), 0.1)

        if not scores:
            return {}

        max_score = max(scores.values()) if scores else 1.0
        if max_score <= 0:
            return dict.fromkeys(scores, 0.0)
        return {fname: float(score) / float(max_score) for fname, score in scores.items()}


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def rank_files(
    workspace: str | Path,
    tags: list[FileTag],
    *,
    chat_files: list[str] | None = None,
    mentioned_idents: list[str] | None = None,
    mentioned_fnames: list[str] | None = None,
    max_count: int = 50,
) -> list[RankedCandidate]:
    """Convenience function to rank files.

    Args:
        workspace: Workspace root path.
        tags: List of FileTag objects.
        chat_files: Files directly in conversation.
        mentioned_idents: Identifiers mentioned in conversation.
        mentioned_fnames: File paths mentioned in conversation.
        max_count: Maximum number of candidates.

    Returns:
        List of ranked file candidates.
    """
    ranker = RepoIntelligenceRanker(workspace)

    if tags:
        ranker.add_tags(tags)

    if chat_files:
        ranker.add_chat_files(chat_files)

    if mentioned_idents:
        ranker.add_mentioned_idents(mentioned_idents)

    if mentioned_fnames:
        ranker.add_mentioned_fnames(mentioned_fnames)

    return ranker.get_ranked_files(max_count=max_count)


__all__ = [
    "RankedCandidate",
    "RepoIntelligenceRanker",
    "rank_files",
]
