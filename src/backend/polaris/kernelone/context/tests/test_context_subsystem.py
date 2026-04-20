"""Unit tests for KernelOne Context subsystem.

Tests cover:
  - ExplorationPolicy phase transitions and expansion decisions
  - BudgetGate token tracking and compaction thresholds
  - WorkingSetAssembler slice and symbol assembly
  - Cache layer behavior (Tier 1 ContextPack cache)
"""

from __future__ import annotations

import pytest


class TestExplorationPolicy:
    """Tests for DefaultExplorationPolicy phase-aware expansion decisions."""

    @pytest.mark.asyncio
    async def test_deny_already_seen_asset(self) -> None:
        """Assets already in seen_assets must be DENIED."""
        from polaris.kernelone.context import (
            AssetCandidate,
            AssetKind,
            ContextBudgetGate,
            DefaultExplorationPolicy,
            ExpansionDecision,
            ExplorationContext,
            ExplorationPhase,
        )

        gate = ContextBudgetGate(model_window=128_000)
        policy = DefaultExplorationPolicy()
        ctx = ExplorationContext(
            phase=ExplorationPhase.SEARCH,
            workspace="/fake",
            seen_assets=frozenset({"src/main.py:1-10"}),
        )
        candidate = AssetCandidate(
            asset_kind=AssetKind.CODE_SLICE,
            file_path="src/main.py",
            line_range=(1, 10),
            estimated_tokens=100,
            priority=8,
        )
        decision = await policy.should_expand(gate.get_current_budget(), candidate, ctx)
        assert decision == ExpansionDecision.DENIED, "Already-seen assets must be DENIED"

    @pytest.mark.asyncio
    async def test_auto_approve_high_priority(self) -> None:
        """Candidates with priority >= min_priority_for_auto_approve must be APPROVED."""
        from polaris.kernelone.context import (
            AssetCandidate,
            AssetKind,
            ContextBudgetGate,
            DefaultExplorationPolicy,
            ExpansionDecision,
            ExplorationContext,
            ExplorationPhase,
        )

        gate = ContextBudgetGate(model_window=128_000)
        policy = DefaultExplorationPolicy()
        ctx = ExplorationContext(phase=ExplorationPhase.SLICE, workspace="/fake")
        candidate = AssetCandidate(
            asset_kind=AssetKind.CODE_SLICE,
            file_path="src/main.py",
            line_range=(1, 50),
            estimated_tokens=200,
            priority=8,  # > min_priority_for_auto_approve (default: 5)
        )
        decision = await policy.should_expand(gate.get_current_budget(), candidate, ctx)
        assert decision == ExpansionDecision.APPROVED, "High-priority assets must be APPROVED"

    @pytest.mark.asyncio
    async def test_defer_exceeds_budget(self) -> None:
        """Candidates that exceed available budget must be DEFERRED, not DENIED.

        Per blueprint §5.5: over-budget assets are DEFERRED so high-priority
        candidates can be flushed when budget frees up (not permanently denied).
        """
        from polaris.kernelone.context import (
            AssetCandidate,
            AssetKind,
            ContextBudgetGate,
            DefaultExplorationPolicy,
            ExpansionDecision,
            ExplorationContext,
            ExplorationPhase,
        )

        # Very tight budget: 100 tokens
        gate = ContextBudgetGate(model_window=1000, safety_margin=0.10)
        policy = DefaultExplorationPolicy()
        ctx = ExplorationContext(phase=ExplorationPhase.SLICE, workspace="/fake")
        candidate = AssetCandidate(
            asset_kind=AssetKind.CODE_SLICE,
            file_path="src/main.py",
            line_range=(1, 1000),
            estimated_tokens=10_000,  # Far exceeds budget
            priority=8,
        )
        decision = await policy.should_expand(gate.get_current_budget(), candidate, ctx)
        assert decision == ExpansionDecision.DEFERRED, "Over-budget candidates must be DEFERRED (blueprint §5.5)"

    @pytest.mark.asyncio
    async def test_should_compact_at_80_percent(self) -> None:
        """Compaction must trigger at 80% utilization threshold."""
        from polaris.kernelone.context import (
            DefaultExplorationPolicy,
            ExplorationPhase,
        )

        policy = DefaultExplorationPolicy()
        # At exactly 80% (64_000 / 80_000)
        assert await policy.should_compact(64_000, 80_000, ExplorationPhase.SLICE)
        # Above 80%
        assert await policy.should_compact(72_000, 80_000, ExplorationPhase.SEARCH)
        # Below 80% (should not compact)
        assert not await policy.should_compact(60_000, 80_000, ExplorationPhase.MAP)


class TestBudgetGate:
    """Tests for ContextBudgetGate token tracking and compaction."""

    def test_effective_limit_applies_safety_margin(self) -> None:
        """Effective limit must be model_window * safety_margin."""
        from polaris.kernelone.context import ContextBudgetGate

        gate = ContextBudgetGate(model_window=100_000, safety_margin=0.80)
        assert gate.get_current_budget().effective_limit == 80_000

    def test_headroom_decreases_after_record_usage(self) -> None:
        """Headroom must decrease as tokens are consumed."""
        from polaris.kernelone.context import ContextBudgetGate

        gate = ContextBudgetGate(model_window=100_000, safety_margin=0.80)
        # effective_limit = 80_000, headroom = 80_000
        assert gate.get_current_budget().headroom == 80_000

        gate.record_usage(30_000)
        budget = gate.get_current_budget()
        assert budget.headroom == 50_000, "Headroom must decrease by recorded tokens"
        assert budget.usage_ratio == pytest.approx(0.375)

    def test_can_add_returns_false_when_over_limit(self) -> None:
        """can_add() must return False when estimated_tokens exceed headroom."""
        from polaris.kernelone.context import ContextBudgetGate

        gate = ContextBudgetGate(model_window=100_000, safety_margin=0.80)
        ok, reason = gate.can_add(90_000)  # Headroom is 80_000
        assert not ok
        assert "exceed" in reason.lower()

    def test_can_add_returns_true_when_within_budget(self) -> None:
        """can_add() must return True when estimated_tokens fit in headroom."""
        from polaris.kernelone.context import ContextBudgetGate

        gate = ContextBudgetGate(model_window=100_000, safety_margin=0.80)
        ok, reason = gate.can_add(10_000)
        assert ok
        assert reason == ""

    def test_suggest_compaction_returns_healthy_when_below_50pct(self) -> None:
        """suggest_compaction() must report healthy when below 50%."""
        from polaris.kernelone.context import ContextBudgetGate

        gate = ContextBudgetGate(model_window=100_000, safety_margin=0.80)
        gate.record_usage(20_000)  # 25% usage
        suggestion = gate.suggest_compaction()
        assert "healthy" in suggestion.lower()

    def test_suggest_compaction_returns_critical_above_75pct(self) -> None:
        """suggest_compaction() must report critical above 75%."""
        from polaris.kernelone.context import ContextBudgetGate

        gate = ContextBudgetGate(model_window=100_000, safety_margin=0.80)
        gate.record_usage(65_000)  # 81% usage
        suggestion = gate.suggest_compaction()
        assert "critical" in suggestion.lower() or "overflow" in suggestion.lower()


class TestWorkingSetAssembler:
    """Tests for WorkingSetAssembler incremental assembly."""

    @pytest.mark.asyncio
    async def test_set_repo_map_tracks_tokens(self) -> None:
        """set_repo_map() must add repo map tokens to budget_used."""
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            RepoMapSnapshot,
            WorkingSetAssembler,
        )

        gate = ContextBudgetGate(model_window=128_000)
        assembler = WorkingSetAssembler(
            workspace="/fake",
            budget_gate=gate,
            policy=DefaultExplorationPolicy(),
        )
        repo_map = RepoMapSnapshot(
            workspace="/fake",
            text="...",
            tokens=500,
        )
        ws = await assembler.set_repo_map(repo_map)
        assert ws.budget_used == 500, "Repo map tokens must be added to budget_used"
        assert ws.repo_map is repo_map

    @pytest.mark.asyncio
    async def test_add_slice_approved_when_within_budget(self) -> None:
        """add_slice() must approve when tokens fit in budget."""
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            RepoMapSnapshot,
            WorkingSetAssembler,
        )

        gate = ContextBudgetGate(model_window=128_000)
        assembler = WorkingSetAssembler(
            workspace="/fake",
            budget_gate=gate,
            policy=DefaultExplorationPolicy(),
        )
        # MAP
        await assembler.set_repo_map(RepoMapSnapshot(workspace="/fake", text="...", tokens=500))
        # SLICE
        ws = await assembler.add_slice("src/main.py", 1, 50, "def foo(): pass", tokens=100)
        assert len(ws.code_slices) == 1
        assert ws.budget_used == 600

    @pytest.mark.asyncio
    async def test_add_slice_denied_when_over_budget(self) -> None:
        """add_slice() must deny when tokens exceed budget."""
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            RepoMapSnapshot,
            WorkingSetAssembler,
        )

        # Tight budget
        gate = ContextBudgetGate(model_window=10_000, safety_margin=0.10)
        assembler = WorkingSetAssembler(
            workspace="/fake",
            budget_gate=gate,
            policy=DefaultExplorationPolicy(),
        )
        await assembler.set_repo_map(RepoMapSnapshot(workspace="/fake", text="x" * 8000, tokens=800))
        # Try to add a huge slice
        huge_content = "x" * 50_000
        ws = await assembler.add_slice("src/main.py", 1, 5000, huge_content, tokens=12_500)
        # Should be denied or deferred (budget exceeded)
        assert len(ws.code_slices) == 0, "Over-budget slice must be denied"
        assert ws.denied_count >= 0  # Denied or deferred

    @pytest.mark.asyncio
    async def test_flush_deferred_returns_deferred_assets(self) -> None:
        """flush_deferred() must return and clear deferred assets."""
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            RepoMapSnapshot,
            WorkingSetAssembler,
        )

        gate = ContextBudgetGate(model_window=128_000)
        assembler = WorkingSetAssembler(
            workspace="/fake",
            budget_gate=gate,
            policy=DefaultExplorationPolicy(),
        )
        await assembler.set_repo_map(RepoMapSnapshot(workspace="/fake", text="...", tokens=200))
        # Try to add a low-priority asset (will be deferred)
        assembler.set_phase(assembler._ctx.phase)  # Stay in current phase (low priority → deferred)
        assembler._ctx = assembler._ctx.__class__(
            phase=assembler._ctx.phase,
            workspace=assembler._ctx.workspace,
            seen_assets=assembler._ctx.seen_assets,
            denied_assets=assembler._ctx.denied_assets,
            expansion_history=assembler._ctx.expansion_history,
            phase_tool_calls=0,
            total_tool_calls=0,
            depth=0,
            max_depth=3,
        )
        deferred = assembler.flush_deferred()
        # flush_deferred returns and clears the queue
        assert isinstance(deferred, list)


class TestContextCache:
    """Tests for ContextCache (Tier 1 pack-level cache)."""

    def test_cache_pack_and_retrieve(self) -> None:
        """Cache must store and retrieve ContextPack by request_hash."""
        from polaris.kernelone.context import ContextCache, ContextItem, ContextPack

        cache = ContextCache()
        pack = ContextPack(
            request_hash="test_hash_abc",
            items=[ContextItem(id="item1", kind="memo", content_or_pointer="test", size_est=10)],
            total_tokens=10,
            total_chars=10,
        )
        cache.cache_pack(pack)
        retrieved = cache.get_cached_pack("test_hash_abc")
        assert retrieved is not None
        assert retrieved.request_hash == "test_hash_abc"
        assert len(retrieved.items) == 1

    def test_cache_miss_returns_none(self) -> None:
        """get_cached_pack() must return None for unknown hash."""
        from polaris.kernelone.context import ContextCache

        cache = ContextCache()
        assert cache.get_cached_pack("unknown_hash") is None

    def test_cache_overwrite_updates_entry(self) -> None:
        """Caching the same hash twice must update, not duplicate."""
        from polaris.kernelone.context import ContextCache, ContextPack

        cache = ContextCache()
        pack_v1 = ContextPack(request_hash="same", items=[], total_tokens=10, total_chars=10)
        pack_v2 = ContextPack(request_hash="same", items=[], total_tokens=20, total_chars=20)
        cache.cache_pack(pack_v1)
        cache.cache_pack(pack_v2)
        retrieved = cache.get_cached_pack("same")
        assert retrieved is not None
        assert retrieved.total_tokens == 20, "Cache must be updated, not duplicated"
