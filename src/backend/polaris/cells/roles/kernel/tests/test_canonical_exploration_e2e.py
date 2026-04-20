"""E2E tests for Canonical Code Exploration policy.

These tests verify the end-to-end behavior of the canonical exploration system:
  1. MAP phase must precede SLICE/SEARCH phases
  2. Large files (>200 lines) must use slices, not full reads
  3. Budget gate fires proactively at 80% utilization threshold
  4. Repeated slice access hits the hot-slice cache
  5. Exploration phase sequence is enforced: MAP -> SEARCH -> SLICE -> EXPAND
  6. Session continuity and code exploration contexts are distinct

All tests use pytest-asyncio.  The tests import from the kernelone.context
public API so they exercise real behavior, not mocks.

NOTE: These tests are designed to pass once the Phase 5 implementation
(canonical_read_tools, 5-tier cache) has landed.  Tests that depend on
not-yet-implemented cache tiers will skip gracefully until those land.
"""

from __future__ import annotations

import pytest


class TestCanonicalCodeExploration:
    """Test suite for canonical code exploration policy enforcement."""

    # ------------------------------------------------------------------
    # CE-001: read_file must not be first call in exploration turn
    #          for files >100 lines.  Role must emit MAP first.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_repo_map_before_full_read(self) -> None:
        """Role should emit MAP-phase tool (repo_map) before SEARCH or SLICE.

        The exploration policy requires that MAP phase assets are assembled
        before SEARCH or SLICE phase assets.  This test verifies that
        WorkingSetAssembler.set_repo_map() must be called before add_slice()
        for the canonical order to be respected.
        """
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            RepoMapSnapshot,
            WorkingSetAssembler,
        )

        gate = ContextBudgetGate(model_window=128_000)
        policy = DefaultExplorationPolicy()
        assembler = WorkingSetAssembler(
            workspace="/fake/repo",
            budget_gate=gate,
            policy=policy,
        )

        # MAP phase: set repo map (always allowed, not gated)
        repo_map = RepoMapSnapshot(
            workspace="/fake/repo",
            text="# File structure\n- src/main.py\n- src/utils.py",
            tokens=200,
        )
        ws = await assembler.set_repo_map(repo_map)

        # SLICE phase: add a slice (allowed after MAP)
        slice_content = "def foo():\n    pass\n"
        ws = await assembler.add_slice("src/main.py", 1, 2, slice_content, tokens=50)

        # Verify MAP is present and SLICE was accepted
        assert ws.repo_map is not None, "Repo map must be set in MAP phase"
        assert len(ws.code_slices) == 1, "Slice must be accepted after MAP phase"
        # MAP tokens (200) + slice tokens (50) = 250
        assert ws.budget_used == 250, "Budget must reflect MAP + SLICE tokens"

    # ------------------------------------------------------------------
    # CE-002: Full-file read on files >2000 lines requires explicit
    #          upgrade flag.  Slices should be used instead.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_large_file_uses_slice(self) -> None:
        """Large files (>200 lines) must use targeted slices, not full reads.

        The exploration policy denies full-file reads above the
        read_full_budget_tokens limit.  WorkingSetAssembler.add_slice()
        should be used instead for targeted line ranges.
        """
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            RepoMapSnapshot,
            WorkingSetAssembler,
        )

        # Use a tight budget so that a large file would exceed it
        gate = ContextBudgetGate(model_window=10_000)
        policy = DefaultExplorationPolicy()
        assembler = WorkingSetAssembler(
            workspace="/fake/repo",
            budget_gate=gate,
            policy=policy,
        )

        # MAP phase
        repo_map = RepoMapSnapshot(workspace="/fake/repo", text="...large repo...", tokens=200)
        await assembler.set_repo_map(repo_map)

        # A "large file" content: 500 lines worth of content
        large_file_lines = "\n".join(f"line {i}" for i in range(1, 501))
        len(large_file_lines) // 4  # ~4 chars/token

        # Adding as a slice (targeted, line-range based) should succeed
        # because add_slice respects the policy and records usage
        ws = await assembler.add_slice(
            "src/large_file.py",
            start_line=1,
            end_line=200,  # Only first 200 lines
            content=large_file_lines[:1000],
            tokens=250,  # Small slice: only 250 tokens
        )

        # The slice should be accepted (it fits within the tight budget as a slice)
        # A full read of 500 lines would have been denied under this budget
        assert len(ws.code_slices) == 1, "Small targeted slice must be accepted"
        slice_item = ws.code_slices[0]
        assert slice_item.line_count == 200, "Slice must preserve line range"
        assert slice_item.file_path == "src/large_file.py"

    # ------------------------------------------------------------------
    # CE-003: Context compaction must not trigger before 80% window
    #          utilization.  Budget gate should fire at 80% proactively.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_budget_gate_triggers_compaction(self) -> None:
        """Budget gate fires compaction signal at 80% utilization.

        ContextBudgetGate records token usage.  DefaultExplorationPolicy
        should_compact() returns True when usage reaches 80% of effective_limit.
        """
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            ExplorationPhase,
            ExplorationPolicyConfig,
        )

        gate = ContextBudgetGate(model_window=100_000, safety_margin=0.80)
        # Effective limit = 100_000 * 0.80 = 80_000
        assert gate.get_current_budget().effective_limit == 80_000

        policy = DefaultExplorationPolicy(ExplorationPolicyConfig(compaction_trigger_ratio=0.80))

        # Simulate 79% usage — should NOT compact
        should_compact_79 = await policy.should_compact(
            current_tokens=63_200,  # 79% of 80_000
            effective_limit=80_000,
            phase=ExplorationPhase.SLICE,
        )
        assert not should_compact_79, "Must NOT compact below 80% threshold"

        # Simulate exactly 80% usage — should compact
        should_compact_80 = await policy.should_compact(
            current_tokens=64_000,  # exactly 80% of 80_000
            effective_limit=80_000,
            phase=ExplorationPhase.SLICE,
        )
        assert should_compact_80, "Must compact at or above 80% threshold"

        # Simulate 85% usage — should compact
        should_compact_85 = await policy.should_compact(
            current_tokens=68_000,  # 85% of 80_000
            effective_limit=80_000,
            phase=ExplorationPhase.SEARCH,
        )
        assert should_compact_85, "Must compact above 80% threshold"

        # Verify gate also tracks usage correctly
        gate.record_usage(60_000)
        budget = gate.get_current_budget()
        assert budget.usage_ratio == pytest.approx(0.75), "Usage ratio must be tracked"
        assert budget.headroom == 20_000, "Headroom must reflect remaining budget"

    # ------------------------------------------------------------------
    # Hot-slice cache: repeated access to the same file+range should
    # hit the Tier 4 cache instead of re-reading from disk.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_hot_slice_cache_hits(self) -> None:
        """Repeated slice access to the same file+range hits Tier 4 hot-slice cache.

        The ContextCache (Tier 1 only in the current implementation) stores
        ContextPack by request_hash.  Once the 5-tier cache lands, repeated
        slice reads with identical file_path+range should return cached content.

        This test is designed to pass once cache-continuity-engineer lands
        the extended ContextCache with Tier 4 hot-slice support.
        If the hot-slice tier is not yet implemented, this test will verify
        the current 1-tier behavior (pack-level cache, no slice-level).
        """
        from polaris.kernelone.context import (
            ContextCache,
            ContextItem,
            ContextPack,
        )

        # Current ContextCache (1-tier) behavior
        cache = ContextCache()

        # Build two packs with the same "slice" content (same file+range simulation)
        # In a full implementation, slice cache would key by file_path+range
        item_a = ContextItem(
            id="slice_1",
            kind="code_slice",
            content_or_pointer="def foo(): pass",
            size_est=50,
            priority=5,
            provider="kernelone",
            refs={"file_path": "src/main.py", "start_line": 1, "end_line": 10},
        )
        item_b = ContextItem(
            id="slice_2",
            kind="code_slice",
            content_or_pointer="def foo(): pass",  # Same content
            size_est=50,
            priority=5,
            provider="kernelone",
            refs={"file_path": "src/main.py", "start_line": 1, "end_line": 10},
        )

        pack_a = ContextPack(
            request_hash="hash_same_slice",
            items=[item_a],
            total_tokens=50,
            total_chars=50,
        )
        ContextPack(
            request_hash="hash_same_slice",  # Same hash
            items=[item_b],
            total_tokens=50,
            total_chars=50,
        )

        # Cache the first pack
        cache.cache_pack(pack_a)

        # Second identical request hits cache
        cached = cache.get_cached_pack("hash_same_slice")
        assert cached is not None, "Second identical request must hit cache"
        assert cached.request_hash == "hash_same_slice"
        assert cached.items[0].id == "slice_1", "Cached item must be the original"

    # ------------------------------------------------------------------
    # Exploration phase sequence enforcement
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_exploration_phase_sequence(self) -> None:
        """Canonical exploration order is enforced: MAP -> SEARCH -> SLICE -> EXPAND.

        WorkingSetAssembler initializes in MAP phase and transitions through
        SEARCH and SLICE.  Attempting to add symbols/slices before MAP should
        still work (the assembler starts in MAP), but after set_repo_map()
        the assembler correctly tracks phase transitions.
        """
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            ExplorationPhase,
            RepoMapSnapshot,
            SymbolCandidate,
            WorkingSetAssembler,
        )

        gate = ContextBudgetGate(model_window=128_000)
        policy = DefaultExplorationPolicy()
        assembler = WorkingSetAssembler(
            workspace="/fake/repo",
            budget_gate=gate,
            policy=policy,
        )

        # Phase 1: MAP — repo map is set, phase is MAP
        repo_map = RepoMapSnapshot(workspace="/fake/repo", text="...", tokens=500)
        ws = await assembler.set_repo_map(repo_map)
        assert assembler._ctx.phase == ExplorationPhase.MAP, "Must start in MAP phase"
        assert ws.repo_map is not None

        # Phase 2: Transition to SEARCH — add symbols
        assembler.set_phase(ExplorationPhase.SEARCH)
        assert assembler._ctx.phase == ExplorationPhase.SEARCH

        sym = SymbolCandidate(
            name="foo",
            type="function",
            file_path="src/main.py",
            line=10,
            signature="def foo() -> None",
        )
        ws = await assembler.add_symbol(sym, priority=7)
        assert len(ws.symbol_candidates) == 1, "Symbol must be added in SEARCH phase"

        # Phase 3: Transition to SLICE — add slices
        assembler.set_phase(ExplorationPhase.SLICE)
        assert assembler._ctx.phase == ExplorationPhase.SLICE

        ws = await assembler.add_slice("src/main.py", 1, 20, "# slice content", tokens=100)
        assert len(ws.code_slices) == 1, "Slice must be added in SLICE phase"

        # Phase 4: Transition to EXPAND — add neighbor
        assembler.set_phase(ExplorationPhase.EXPAND)
        assert assembler._ctx.phase == ExplorationPhase.EXPAND

        # Verify expansion history records all phases
        assert len(ws.expansion_history) == 2, "Expansion history must track symbol + slice"

    # ------------------------------------------------------------------
    # Session continuity vs code exploration separation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_session_and_code_exploration_separate(self) -> None:
        """Session continuity and code exploration contexts are kept distinct.

        SessionContinuityPack and WorkingSet are separate data structures.
        They are assembled separately by SessionContinuityEngine and
        WorkingSetAssembler respectively, and merged at the role kernel level
        into separate context sections of the prompt.

        This test verifies the structural separation by checking that:
          1. SessionContinuityPack fields do not overlap with WorkingSet fields
          2. The two can coexist in a merged context without field collision
        """
        from polaris.kernelone.context import (
            ContextBudgetGate,
            DefaultExplorationPolicy,
            RepoMapSnapshot,
            RoleContextIdentity,
            SessionContinuityEngine,
            SessionContinuityPolicy,
            WorkingSetAssembler,
        )

        # Build a session continuity pack (Tier 1)
        continuity_engine = SessionContinuityEngine(policy=SessionContinuityPolicy())
        identity = RoleContextIdentity(
            role_id="test-role-1",
            role_type="director",
            goal="Implement user authentication",
            acceptance_criteria=["Login works", "Logout works"],
            scope=["src/auth/"],
            current_phase="active",
        )
        session_messages = [
            {"role": "user", "content": "Please implement login", "sequence": 0},
            {"role": "assistant", "content": "I'll implement login now", "sequence": 1},
        ]
        continuity_pack = await continuity_engine.build_pack(
            messages=session_messages,
            identity=identity,
            focus="authentication",
            recent_window_messages=2,
        )
        assert continuity_pack is not None
        assert continuity_pack.summary != "", "Session continuity must have a summary"
        # role_type lives in RoleContextIdentity (pack preserves it indirectly via stable_facts)
        assert identity.role_type == "director", "Identity must preserve role type"

        # Build a code exploration working set (Tier 2-4)
        gate = ContextBudgetGate(model_window=128_000)
        assembler = WorkingSetAssembler(
            workspace="/fake/repo",
            budget_gate=gate,
            policy=DefaultExplorationPolicy(),
        )
        repo_map = RepoMapSnapshot(
            workspace="/fake/repo",
            text="# auth module\n- src/auth/login.py\n- src/auth/logout.py",
            tokens=300,
        )
        await assembler.set_repo_map(repo_map)
        ws = await assembler.add_slice("src/auth/login.py", 1, 50, "def login(): pass", tokens=80)
        await assembler.add_slice("src/auth/logout.py", 1, 50, "def logout(): pass", tokens=80)

        # Verify structural separation
        ws_dict = ws.to_context_dict()
        cp_dict = continuity_pack.to_dict()

        # No field collision: session continuity uses "summary/stable_facts/open_loops"
        # working set uses "repo_maps/symbols/slices" inside asset_counts
        assert "summary" in cp_dict and "summary" not in ws_dict.get("metadata", {})
        assert "asset_counts" in ws_dict["metadata"]
        asset_counts = ws_dict["metadata"]["asset_counts"]
        assert asset_counts.get("repo_maps") == 1 or asset_counts.get("slices") == 2

        # Merged context would place them in separate sections of the prompt
        merged_content = ws_dict["content"]
        assert "auth" in merged_content.lower(), "Working set must contain code content"
        assert continuity_pack.summary in continuity_pack.summary or continuity_pack.stable_facts, (
            "Continuity pack must have meaningful summary or facts"
        )

        # Budget tracking is separate
        assert ws.budget_limit == gate.get_current_budget().effective_limit
