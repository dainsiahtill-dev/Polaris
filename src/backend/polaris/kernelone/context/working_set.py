"""Working-Set Assembler — incremental context assembly with budget enforcement.

This module owns the stateful assembly of a per-turn context working set:
    - CodeSlice: a line-range slice of a file
    - SymbolCandidate: a discovered symbol (function/class/etc.)
    - RepoMapSnapshot: the repo-map generated at the start of a turn
    - WorkingSet: the aggregated container with budget tracking

Architecture role:
    WorkingSetAssembler is the stateful "glue" between
    ExplorationPolicy, ContextBudgetGate, and the role's prompt builder.
    It is instantiated per turn (not shared across turns).

Design constraints:
    - Immutable building: add_* methods return a new WorkingSet (functional update).
    - All I/O is async; content is passed in (no file I/O in this module).
    - UTF-8 text throughout.
    - Budget headroom is tracked on every add; expansion is gated by the policy.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .exploration_policy import (
    AssetCandidate,
    AssetKind,
    DefaultExplorationPolicy,
    ExpansionDecision,
    ExplorationContext,
    ExplorationPhase,
    ExplorationPolicyPort,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .budget_gate import ContextBudgetGate
    from .cache import KernelOneCacheManager
    from .cache_manager import TieredAssetCacheManager

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CodeSlice:
    """A line-range slice from a single file."""

    file_path: str
    start_line: int  # 1-indexed inclusive
    end_line: int  # 1-indexed inclusive
    content: str
    tokens: int = 0

    def __post_init__(self) -> None:
        if self.start_line < 1:
            raise ValueError(f"start_line must be >= 1, got {self.start_line}")
        if self.end_line < self.start_line:
            raise ValueError(f"end_line ({self.end_line}) must be >= start_line ({self.start_line})")

    @property
    def line_range(self) -> tuple[int, int]:
        return (self.start_line, self.end_line)

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class SymbolCandidate:
    """A discovered code symbol (function, class, method, constant, etc.)."""

    name: str
    type: str  # "class" | "function" | "method" | "constant" | "module" | ...
    file_path: str
    line: int  # 1-indexed line number of the symbol definition
    signature: str = ""  # e.g. "def foo(a: int) -> str"

    @property
    def display_key(self) -> str:
        return f"{self.file_path}:{self.line}"


@dataclass(frozen=True)
class RepoMapSnapshot:
    """A repo-map generated at the start of a turn."""

    workspace: str
    generated_at: float = field(default_factory=time.time)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)  # file -> {skeleton, stats}
    text: str = ""  # Flat text representation
    tokens: int = 0


@dataclass
class WorkingSet:
    """The aggregated working set for one exploration/assembly pass.

    This is a mutable container (returned by assembler methods and modified
    in-place by the assembler). Use ``.to_context_dict()`` to produce
    the final prompt-injectable payload.
    """

    workspace: str
    budget_limit: int  # Total budget (from gate.effective_limit)

    # Core assets
    repo_map: RepoMapSnapshot | None = None
    symbol_candidates: list[SymbolCandidate] = field(default_factory=list)
    code_slices: list[CodeSlice] = field(default_factory=list)

    # Budget bookkeeping
    budget_used: int = 0  # Tokens consumed so far

    # Expansion tracking
    expansion_history: list[str] = field(default_factory=list)
    denied_count: int = 0
    deferred_assets: list[AssetCandidate] = field(default_factory=list)

    def to_context_dict(self) -> dict[str, Any]:
        """Render the working set as a prompt-injectable dict.

        Output structure::

            {
                "role": "system",
                "content": "...",
                "name": "working_set",
                "metadata": {
                    "budget_used": int,
                    "budget_limit": int,
                    "asset_counts": {...},
                    "expansion_history": [...],
                }
            }
        """
        parts: list[str] = []

        if self.repo_map is not None and self.repo_map.text:
            parts.append(f"【Repo Map — {self.repo_map.workspace}】\n{self.repo_map.text}")

        if self.symbol_candidates:
            sym_lines = ["【Discovered Symbols】"]
            for sym in self.symbol_candidates:
                sig = f" — {sym.signature}" if sym.signature else ""
                sym_lines.append(f"  {sym.type} {sym.name}{sig} @ {sym.file_path}:{sym.line}")
            parts.append("\n".join(sym_lines))

        if self.code_slices:
            slice_lines = ["【Code Slices】"]
            for sl in self.code_slices:
                slice_lines.append(f"\n--- {sl.file_path}:{sl.start_line}-{sl.end_line} ---\n{sl.content}")
            parts.append("\n".join(slice_lines))

        content = "[Working set is empty]" if not parts else "\n\n".join(parts)

        return {
            "role": "system",
            "content": content,
            "name": "working_set",
            "metadata": {
                "budget_used": self.budget_used,
                "budget_limit": self.budget_limit,
                "asset_counts": {
                    "repo_maps": 1 if self.repo_map else 0,
                    "symbols": len(self.symbol_candidates),
                    "slices": len(self.code_slices),
                    "denied": self.denied_count,
                    "deferred": len(self.deferred_assets),
                },
                "expansion_history": list(self.expansion_history),
            },
        }


# ------------------------------------------------------------------
# Assembler
# ------------------------------------------------------------------


class WorkingSetAssembler:
    """Incremental working-set assembler with policy-gated expansion.

    Usage::

        gate = ContextBudgetGate.from_role_policy(max_context_tokens=128_000)
        policy = DefaultExplorationPolicy()
        assembler = WorkingSetAssembler(
            workspace="/repo",
            budget_gate=gate,
            policy=policy,
        )

        ws = await assembler.set_repo_map(repo_map_snapshot)
        ws = await assembler.add_slice("src/main.py", 1, 50, content, tokens=...)
        ws = await assembler.add_symbol(symbol_candidate)
        if ws.budget_used >= gate.effective_limit:
            await assembler.should_trigger_compaction()
    """

    def __init__(
        self,
        workspace: str | Path,
        budget_gate: ContextBudgetGate,
        policy: ExplorationPolicyPort | None = None,
        max_depth: int = 3,
        cache_manager: KernelOneCacheManager | TieredAssetCacheManager | None = None,
    ) -> None:
        self.workspace = str(workspace)
        self._gate = budget_gate
        self._policy = policy or DefaultExplorationPolicy()
        self._max_depth = max_depth
        self._cache = cache_manager
        self._cache_stats: dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "invalidations": 0,
        }

        # Mutable per-pass state
        self._ctx = ExplorationContext(
            phase=ExplorationPhase.MAP,
            workspace=self.workspace,
            max_depth=max_depth,
        )
        self._working_set = WorkingSet(
            workspace=self.workspace,
            budget_limit=budget_gate.get_current_budget().effective_limit,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Cache-integrated helpers
    # ------------------------------------------------------------------

    async def get_or_build_repo_map(
        self,
        languages: list[str] | None = None,
        *,
        domain: str = "code",
        chat_files: list[str] | None = None,
        mentioned_idents: list[str] | None = None,
        mentioned_fnames: list[str] | None = None,
        builder: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Get repo map from cache, or build and cache it.

        Lookup order: Hot Slice Cache -> Repo Map Cache -> builder().

        Args:
            languages: Language filter for repo map (used as cache key).
            builder: Optional sync builder function. If provided, called when
                cache miss and result is stored in both Hot Slice and Repo Map cache.
                If not provided, only cache lookup is performed (for read-only use).

        Returns:
            Cached or newly built repo map dict, or empty dict if all miss.
        """
        if self._cache is None:
            if builder is not None:
                return builder()
            return {}

        lang_key = ",".join(sorted(languages or ["default"]))

        # Step 1: Check Hot Slice Cache
        hot_key = f"repo_map:hot:{lang_key}"
        hot_result = await self._cache.get_hot_slice(hot_key)
        if hot_result is not None:
            _logger.debug("get_or_build_repo_map: hot slice HIT for %s", lang_key)
            try:
                return json.loads(hot_result)
            except (json.JSONDecodeError, ValueError) as exc:
                _logger.debug(
                    "get_or_build_repo_map: failed to parse hot slice for %s: %s",
                    lang_key,
                    exc,
                )  # Fall through to next cache

        # Step 2: Check Repo Map Cache
        repo_map_dict = await self._cache.get_repo_map(self.workspace, lang_key)
        if repo_map_dict is not None:
            _logger.debug("get_or_build_repo_map: repo map cache HIT for %s", lang_key)
            # Promote to Hot Slice Cache
            try:
                await self._cache.put_hot_slice(hot_key, json.dumps(repo_map_dict, ensure_ascii=False))
            except (TypeError, AttributeError, RuntimeError) as exc:
                _logger.debug(
                    "get_or_build_repo_map: failed to promote to hot slice for %s: %s",
                    lang_key,
                    exc,
                )
            return repo_map_dict

        # Step 3: All miss - build if builder provided
        if builder is not None:
            _logger.debug("get_or_build_repo_map: cache MISS, building for %s", lang_key)
            result = builder()
            # Store in both caches
            await self._cache.put_repo_map(self.workspace, lang_key, result)
            try:
                await self._cache.put_hot_slice(hot_key, json.dumps(result, ensure_ascii=False))
            except (TypeError, AttributeError, RuntimeError) as exc:
                _logger.debug(
                    "get_or_build_repo_map: failed to cache built result for %s: %s",
                    lang_key,
                    exc,
                )
            return result

        # Step 4: Optional repo intelligence fallback for code/research domains.
        ws = await self.build_repo_map_with_intelligence(
            domain=domain,
            chat_files=chat_files,
            mentioned_idents=mentioned_idents,
            mentioned_fnames=mentioned_fnames,
            languages=languages,
            include_loi=True,
        )
        if ws.repo_map is not None:
            repo_map_dict = {
                "files": dict(ws.repo_map.files or {}),
                "text": ws.repo_map.text,
                "tokens": ws.repo_map.tokens,
                "generated_at": ws.repo_map.generated_at,
            }
            try:
                await self._cache.put_repo_map(self.workspace, lang_key, repo_map_dict)
                await self._cache.put_hot_slice(
                    hot_key,
                    json.dumps(repo_map_dict, ensure_ascii=False),
                )
            except (TypeError, AttributeError, RuntimeError) as exc:
                _logger.debug(
                    "get_or_build_repo_map: failed to cache intelligence result for %s: %s",
                    lang_key,
                    exc,
                )
            return repo_map_dict

        return {}

    async def get_symbol_index(
        self,
        file_path: str | Path,
    ) -> dict[str, Any] | None:
        """Get symbol index from cache for a file.

        Checks Hot Slice Cache first, then Symbol Index Cache.
        Returns None if not cached.
        """
        if self._cache is None:
            return None

        fp = str(file_path)
        # Step 1: Hot Slice
        hot_key = f"symbol_index:hot:{fp}"
        hot_result = await self._cache.get_hot_slice(hot_key)
        if hot_result is not None:
            try:
                return json.loads(hot_result)
            except (json.JSONDecodeError, ValueError) as exc:
                _logger.debug(
                    "get_symbol_index: failed to parse hot slice for %s: %s",
                    fp,
                    exc,
                )

        # Step 2: Symbol Index Cache
        result = await self._cache.get_symbol_index(Path(fp))
        if result is not None:
            # Promote to Hot Slice
            try:
                await self._cache.put_hot_slice(hot_key, json.dumps(result, ensure_ascii=False))
            except (TypeError, AttributeError, RuntimeError) as exc:
                _logger.debug(
                    "get_symbol_index: failed to promote to hot slice for %s: %s",
                    fp,
                    exc,
                )
        return result

    async def build_repo_map_with_intelligence(
        self,
        *,
        domain: str = "code",
        chat_files: list[str] | None = None,
        mentioned_idents: list[str] | None = None,
        mentioned_fnames: list[str] | None = None,
        languages: list[str] | None = None,
        max_files: int = 50,
        max_symbols: int = 100,
        include_loi: bool = True,
    ) -> WorkingSet:
        """Build and inject repo intelligence map into the working set.

        Domain-aware gating:
            - code/research: enabled
            - document/general: no-op (keeps current working set)
        """
        domain_token = str(domain or "").strip().lower() or "code"
        if domain_token not in {"code", "research"}:
            _logger.debug(
                "build_repo_map_with_intelligence: skipped for domain=%s",
                domain_token,
            )
            return self._working_set

        try:
            from .repo_intelligence import get_repo_intelligence
        except ImportError as exc:
            _logger.debug("build_repo_map_with_intelligence: import failed: %s", exc)
            return self._working_set

        try:
            facade = get_repo_intelligence(
                self.workspace,
                languages=languages,
            )
            result = facade.get_repo_map(
                chat_files=chat_files or [],
                mentioned_idents=mentioned_idents or [],
                mentioned_fnames=mentioned_fnames or [],
                max_files=max_files,
                max_symbols=max_symbols,
                include_loi=include_loi,
            )
            text = str(result.to_text() or "").strip()
            if not text:
                return self._working_set

            snapshot = RepoMapSnapshot(
                workspace=self.workspace,
                text=text,
                tokens=self._gate.estimate_tokens_for_text(text),
            )
            await self.set_repo_map(snapshot)
            self._working_set.expansion_history.append(f"repo_intelligence:{domain_token}")
            return self._working_set
        except (AttributeError, TypeError, RuntimeError, ValueError) as exc:
            _logger.debug("build_repo_map_with_intelligence: failed: %s", exc)
            return self._working_set

    async def cache_symbol_index(self, file_path: str | Path, index: dict[str, Any]) -> None:
        """Store symbol index in both Symbol Index and Hot Slice caches."""
        if self._cache is None:
            return
        fp = str(file_path)
        await self._cache.put_symbol_index(Path(fp), index)
        hot_key = f"symbol_index:hot:{fp}"
        try:
            await self._cache.put_hot_slice(hot_key, json.dumps(index, ensure_ascii=False))
        except (TypeError, AttributeError, RuntimeError) as exc:
            _logger.debug(
                "put_symbol_index: failed to cache hot slice for %s: %s",
                fp,
                exc,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def set_repo_map(self, repo_map: RepoMapSnapshot) -> WorkingSet:
        """Set (or replace) the repo-map snapshot.

        The repo-map is treated as a special asset: it always fits in MAP phase
        and is not subject to per-asset policy gating.

        If a cache manager is available, the repo map is also stored in the cache.
        """
        est_tokens = repo_map.tokens or self._gate.estimate_tokens_for_text(repo_map.text)
        # Patch tokens onto the snapshot if it has no token count so estimate_tokens() is consistent
        if not repo_map.tokens:
            object.__setattr__(repo_map, "tokens", est_tokens)
        self._working_set.repo_map = repo_map
        self._working_set.budget_used += est_tokens
        self._gate.record_usage(est_tokens)

        # Also store in cache if available
        if self._cache is not None and repo_map.files:
            try:
                repo_dict = {
                    "files": repo_map.files,
                    "text": repo_map.text,
                    "tokens": repo_map.tokens,
                    "generated_at": repo_map.generated_at,
                }
                langs = list(repo_map.files.keys())
                lang_key = ",".join(sorted(langs)) if langs else "default"
                await self._cache.put_repo_map(self.workspace, lang_key, repo_dict)
            except (TypeError, AttributeError, RuntimeError) as exc:
                _logger.debug("set_repo_map: cache store failed: %s", exc)

        _logger.debug(
            "set_repo_map: added %d tokens (total used: %d)",
            est_tokens,
            self._working_set.budget_used,
        )
        return self._working_set

    async def add_slice(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        content: str,
        *,
        tokens: int | None = None,
        priority: int = 5,
    ) -> WorkingSet:
        """Add a code slice after policy gating.

        Returns the current working set (possibly unchanged if the policy denied it).
        """
        est_tokens = tokens if tokens is not None else self._gate.estimate_tokens_for_text(content)

        candidate = AssetCandidate(
            asset_kind=AssetKind.CODE_SLICE,
            file_path=file_path,
            line_range=(start_line, end_line),
            estimated_tokens=est_tokens,
            priority=priority,
            metadata={"content_preview": content[:80]},
        )
        budget = self._gate.get_current_budget()
        decision = await self._policy.should_expand(budget, candidate, self._ctx)

        if decision == ExpansionDecision.APPROVED:
            sl = CodeSlice(
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                content=content,
                tokens=est_tokens,
            )
            self._working_set.code_slices.append(sl)
            self._working_set.budget_used += est_tokens
            self._working_set.expansion_history.append(candidate.display_key)
            self._gate.record_usage(est_tokens)
            self._ctx = self._ctx.with_approved_asset(candidate)
            _logger.debug(
                "add_slice: APPROVED %s (%d tokens, total used: %d)",
                candidate.display_key,
                est_tokens,
                self._working_set.budget_used,
            )

            # Cache the approved slice in the Hot Slice tier
            if self._cache is not None:
                slice_key = f"slice:{file_path}:{start_line}:{end_line}"
                try:
                    # Both TieredAssetCacheManager and KernelOneCacheManager expose put_hot_slice
                    put_fn = getattr(self._cache, "put_hot_slice", None)
                    if callable(put_fn):
                        import asyncio

                        asyncio.get_event_loop().run_until_complete(put_fn(slice_key, content, file_path=file_path))
                        self._cache_stats["sets"] += 1
                except (TypeError, AttributeError, RuntimeError) as exc:
                    _logger.debug("add_slice: cache set failed for %s: %s", slice_key, exc)

        elif decision == ExpansionDecision.DENIED:
            self._working_set.denied_count += 1
            self._ctx = self._ctx.with_denied_asset(candidate)
            _logger.debug("add_slice: DENIED %s", candidate.display_key)

        else:  # DEFERRED
            self._working_set.deferred_assets.append(candidate)
            _logger.debug("add_slice: DEFERRED %s", candidate.display_key)

        return self._working_set

    async def add_symbol(self, symbol: SymbolCandidate, *, priority: int = 5) -> WorkingSet:
        """Add a symbol candidate after policy gating.

        Symbols are low-cost; gating is lightweight but still enforced
        to prevent unbounded symbol lists from inflating context.
        """
        # Symbols are cheap — estimate just the signature + name
        sig_text = f"{symbol.type} {symbol.name} {symbol.signature}"
        est_tokens = self._gate.estimate_tokens_for_text(sig_text)

        candidate = AssetCandidate(
            asset_kind=AssetKind.SYMBOL,
            file_path=symbol.file_path,
            line_range=(symbol.line, symbol.line),
            estimated_tokens=est_tokens,
            priority=priority,
            metadata={"symbol_name": symbol.name},
        )
        budget = self._gate.get_current_budget()
        decision = await self._policy.should_expand(budget, candidate, self._ctx)

        if decision == ExpansionDecision.APPROVED:
            self._working_set.symbol_candidates.append(symbol)
            self._working_set.budget_used += est_tokens
            self._working_set.expansion_history.append(candidate.display_key)
            self._gate.record_usage(est_tokens)
            self._ctx = self._ctx.with_approved_asset(candidate)
            _logger.debug("add_symbol: APPROVED %s", candidate.display_key)

        elif decision == ExpansionDecision.DENIED:
            self._working_set.denied_count += 1
            self._ctx = self._ctx.with_denied_asset(candidate)
            _logger.debug("add_symbol: DENIED %s", candidate.display_key)

        else:  # DEFERRED
            self._working_set.deferred_assets.append(candidate)
            _logger.debug("add_symbol: DEFERRED %s", candidate.display_key)

        return self._working_set

    async def expand_to_neighbors(
        self,
        anchor: CodeSlice,
        *,
        neighbor_content_getter: Callable[[str, int, int], str | None] = lambda fp, s, e: None,
        priority: int = 3,
    ) -> WorkingSet:
        """Expand working set to files that reference or surround the anchor slice.

        Args:
            anchor: The slice to expand around.
            neighbor_content_getter: Optional callable that returns neighbor
                file content given (file_path, start_line, end_line).
                If None or returns None, no neighbor expansion is attempted.
            priority: Passed through to add_slice for the neighbor.
        """
        self._ctx = ExplorationContext(
            phase=ExplorationPhase.EXPAND,
            workspace=self.workspace,
            depth=self._ctx.depth + 1,
            max_depth=self._max_depth,
            seen_assets=self._ctx.seen_assets,
            denied_assets=self._ctx.denied_assets,
            expansion_history=self._ctx.expansion_history,
            phase_tool_calls=0,
            total_tool_calls=self._ctx.total_tool_calls,
        )

        neighbor_path = _neighbor_file_for_slice(anchor)
        if neighbor_path is None:
            _logger.debug("expand_to_neighbors: no neighbor candidate for %s", anchor.file_path)
            return self._working_set

        content = neighbor_content_getter(neighbor_path, 1, 200)
        if content is None:
            _logger.debug("expand_to_neighbors: no content from getter for %s", neighbor_path)
            return self._working_set

        return await self.add_slice(
            file_path=neighbor_path,
            start_line=1,
            end_line=min(200, content.count("\n") + 1),
            content=content,
            priority=priority,
        )

    async def estimate_tokens(self) -> int:
        """Estimate the total token cost of the current working set."""
        total = 0
        if self._working_set.repo_map:
            # Use stored tokens; fall back to text estimation if snapshot has no token count
            total += self._working_set.repo_map.tokens or self._gate.estimate_tokens_for_text(
                self._working_set.repo_map.text
            )
        for sl in self._working_set.code_slices:
            total += sl.tokens
        for sym in self._working_set.symbol_candidates:
            sig_text = f"{sym.type} {sym.name} {sym.signature}"
            total += self._gate.estimate_tokens_for_text(sig_text)
        return total

    async def should_trigger_compaction(self) -> bool:
        """Return True when compaction should be triggered.

        Delegates to the exploration policy's should_compact method.
        Checks whichever is larger: assembler-tracked budget_used or gate-recorded tokens.
        This handles cases where tokens are recorded directly on the gate.
        """
        budget = self._gate.get_current_budget()
        gate_tokens = budget.current_tokens
        effective_used = max(self._working_set.budget_used, gate_tokens)
        return await self._policy.should_compact(
            current_tokens=effective_used,
            effective_limit=budget.effective_limit,
            phase=self._ctx.phase,
        )

    def get_context_dict(self) -> dict[str, Any]:
        """Render the current working set as a prompt-injectable dict."""
        return self._working_set.to_context_dict()

    def set_phase(self, phase: ExplorationPhase) -> None:
        """Advance to the next exploration phase (resets phase_tool_calls)."""
        self._ctx = ExplorationContext(
            phase=phase,
            workspace=self.workspace,
            depth=self._ctx.depth,
            max_depth=self._max_depth,
            seen_assets=self._ctx.seen_assets,
            denied_assets=self._ctx.denied_assets,
            expansion_history=self._ctx.expansion_history,
            phase_tool_calls=0,
            total_tool_calls=self._ctx.total_tool_calls,
        )

    def flush_deferred(self) -> list[AssetCandidate]:
        """Return and clear the deferred asset queue."""
        assets = list(self._working_set.deferred_assets)
        self._working_set.deferred_assets.clear()
        return assets

    # ------------------------------------------------------------------
    # Cache integration
    # ------------------------------------------------------------------

    @property
    def cache_stats(self) -> dict[str, Any]:
        """Return cache hit/miss statistics accumulated during this assembly pass.

        Returns a dict with keys:
            hits: Number of cache hits
            misses: Number of cache misses
            sets: Number of cache set operations
            invalidations: Number of cache invalidations
            tier_stats: Full per-tier stats from the cache manager (if available)
        """
        result: dict[str, Any] = dict(self._cache_stats)
        if self._cache is not None:
            # Both KernelOneCacheManager and TieredAssetCacheManager expose get_stats()
            stats = getattr(self._cache, "get_stats", lambda: None)
            if stats is not None:
                s = stats()
                if s is not None:
                    result["tier_stats"] = (
                        s.to_dict()
                        if hasattr(s, "to_dict")
                        else {
                            k: getattr(s, k, 0)
                            for k in [
                                "hits_session_continuity",
                                "misses_session_continuity",
                                "hits_repo_map",
                                "misses_repo_map",
                                "hits_symbol_index",
                                "misses_symbol_index",
                                "hits_hot_slice",
                                "misses_hot_slice",
                                "hits_projection",
                                "misses_projection",
                                "total_hits",
                                "total_misses",
                                "hit_ratio",
                                "evictions",
                            ]
                        }
                    )
        return result


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _neighbor_file_for_slice(sl: CodeSlice) -> str | None:
    """Suggest a neighbor file path for a given slice.

    Simple heuristic:
        - For "foo.py", suggest "test_foo.py" or "tests/test_foo.py"
        - For "src/foo.py", also try "tests/src/test_foo.py"
    """
    import os

    base, ext = os.path.splitext(sl.file_path)
    # Test file candidates
    candidates = [
        f"test_{os.path.basename(base)}{ext}",
        f"{os.path.dirname(base)}/test_{os.path.basename(base)}{ext}",
    ]
    # Also try a "tests/" prefix
    candidates.append(f"tests/{os.path.basename(base)}{ext}")
    candidates.append(f"tests/{os.path.basename(base)}/test_{os.path.basename(base)}{ext}")

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


__all__ = [
    "CodeSlice",
    "RepoMapSnapshot",
    "SymbolCandidate",
    "WorkingSet",
    "WorkingSetAssembler",
]
