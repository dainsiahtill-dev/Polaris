"""Tests for polaris.kernelone.context.working_set."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.context.budget_gate import ContextBudget, ContextBudgetGate
from polaris.kernelone.context.exploration_policy import (
    AssetCandidate,
    AssetKind,
    DefaultExplorationPolicy,
    ExpansionDecision,
    ExplorationContext,
    ExplorationPhase,
)
from polaris.kernelone.context.working_set import (
    CodeSlice,
    RepoMapSnapshot,
    SymbolCandidate,
    WorkingSet,
    WorkingSetAssembler,
    _neighbor_file_for_slice,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestCodeSlice:
    def test_construction_valid(self) -> None:
        sl = CodeSlice(file_path="src/main.py", start_line=10, end_line=50, content="...", tokens=100)
        assert sl.file_path == "src/main.py"
        assert sl.start_line == 10
        assert sl.end_line == 50
        assert sl.tokens == 100
        assert sl.line_range == (10, 50)
        assert sl.line_count == 41  # 50 - 10 + 1

    def test_construction_invalid_start_line(self) -> None:
        with pytest.raises(ValueError, match="start_line"):
            CodeSlice(file_path="a.py", start_line=0, end_line=10, content="")

    def test_construction_invalid_range(self) -> None:
        with pytest.raises(ValueError, match=r"end_line.*must be.*start_line"):
            CodeSlice(file_path="a.py", start_line=50, end_line=10, content="")


class TestSymbolCandidate:
    def test_construction(self) -> None:
        sym = SymbolCandidate(name="Foo", type="class", file_path="src/foo.py", line=10, signature="class Foo:")
        assert sym.name == "Foo"
        assert sym.type == "class"
        assert sym.display_key == "src/foo.py:10"


class TestRepoMapSnapshot:
    def test_construction(self) -> None:
        rm = RepoMapSnapshot(workspace="/repo", text="src/\n  main.py", tokens=20)
        assert rm.workspace == "/repo"
        assert rm.text == "src/\n  main.py"
        assert rm.tokens == 20


class TestWorkingSet:
    def test_to_context_dict_empty(self) -> None:
        ws = WorkingSet(workspace="/repo", budget_limit=100_000)
        d = ws.to_context_dict()
        assert d["role"] == "system"
        assert d["name"] == "working_set"
        assert "[Working set is empty]" in d["content"]

    def test_to_context_dict_with_repo_map(self) -> None:
        rm = RepoMapSnapshot(workspace="/repo", text="src/\n  main.py", tokens=20)
        ws = WorkingSet(workspace="/repo", budget_limit=100_000, repo_map=rm)
        d = ws.to_context_dict()
        assert "Repo Map" in d["content"]
        assert d["metadata"]["asset_counts"]["repo_maps"] == 1

    def test_to_context_dict_with_slices(self) -> None:
        sl = CodeSlice(file_path="a.py", start_line=1, end_line=5, content="def f(): pass", tokens=10)
        ws = WorkingSet(workspace="/repo", budget_limit=100_000, code_slices=[sl])
        d = ws.to_context_dict()
        assert "Code Slices" in d["content"]
        assert "a.py" in d["content"]
        assert d["metadata"]["asset_counts"]["slices"] == 1

    def test_to_context_dict_with_symbols(self) -> None:
        sym = SymbolCandidate(name="Foo", type="class", file_path="foo.py", line=1, signature="class Foo:")
        ws = WorkingSet(workspace="/repo", budget_limit=100_000, symbol_candidates=[sym])
        d = ws.to_context_dict()
        assert "Discovered Symbols" in d["content"]
        assert "class Foo" in d["content"]

    def test_to_context_dict_metadata(self) -> None:
        ws = WorkingSet(workspace="/repo", budget_limit=100_000, budget_used=5_000, denied_count=2)
        d = ws.to_context_dict()
        assert d["metadata"]["budget_used"] == 5_000
        assert d["metadata"]["budget_limit"] == 100_000
        assert d["metadata"]["asset_counts"]["denied"] == 2


class TestWorkingSetAssembler:
    @pytest.fixture
    def gate(self) -> ContextBudgetGate:
        return ContextBudgetGate(model_window=128_000, safety_margin=0.80)

    @pytest.fixture
    def policy(self) -> DefaultExplorationPolicy:
        return DefaultExplorationPolicy()

    @pytest.mark.asyncio
    async def test_set_repo_map(self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        rm = RepoMapSnapshot(workspace="/repo", text="src/\n  main.py", tokens=20)
        ws = await asm.set_repo_map(rm)
        assert ws.repo_map is rm
        assert ws.budget_used > 0
        assert gate.get_current_budget().current_tokens == ws.budget_used

    @pytest.mark.asyncio
    async def test_add_slice_approved_high_priority(
        self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy
    ) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        ws = await asm.add_slice("a.py", 1, 10, "def foo(): pass", priority=10)
        assert len(ws.code_slices) == 1
        assert ws.code_slices[0].file_path == "a.py"

    @pytest.mark.asyncio
    async def test_add_slice_denied_low_priority(
        self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy
    ) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        ws = await asm.add_slice("a.py", 1, 10, "def foo(): pass", priority=0)
        assert len(ws.code_slices) == 0
        assert ws.denied_count == 1

    @pytest.mark.asyncio
    async def test_add_slice_deferred_when_exceeds_budget(
        self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy
    ) -> None:
        # Exhaust budget first: 128k * 0.80 = 102,400 effective limit
        # record_usage(103_000) -> headroom = -600
        gate.record_usage(103_000)
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        ws = await asm.add_slice("a.py", 1, 10, "x" * 2000, priority=5)  # ~500 tokens
        assert len(ws.code_slices) == 0
        assert len(ws.deferred_assets) == 1

    @pytest.mark.asyncio
    async def test_add_symbol_approved(self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        sym = SymbolCandidate(name="Foo", type="class", file_path="foo.py", line=1, signature="class Foo:")
        ws = await asm.add_symbol(sym, priority=10)
        assert len(ws.symbol_candidates) == 1

    @pytest.mark.asyncio
    async def test_add_symbol_denied_low_priority(
        self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy
    ) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        sym = SymbolCandidate(name="Foo", type="class", file_path="foo.py", line=1, signature="class Foo:")
        ws = await asm.add_symbol(sym, priority=0)
        assert len(ws.symbol_candidates) == 0
        assert ws.denied_count == 1

    @pytest.mark.asyncio
    async def test_deduplication(self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        await asm.add_slice("a.py", 1, 10, "def foo(): pass", priority=10)
        ws2 = await asm.add_slice("a.py", 1, 10, "def foo(): pass", priority=10)
        # Second add should be denied (already seen)
        assert len(ws2.code_slices) == 1
        assert ws2.denied_count == 1

    @pytest.mark.asyncio
    async def test_estimate_tokens(self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        # 400 'x' chars / 4 chars-per-token = 100 tokens
        rm = RepoMapSnapshot(workspace="/repo", text="x" * 400, tokens=0)
        await asm.set_repo_map(rm)
        est = await asm.estimate_tokens()
        # set_repo_map adds tokens via text estimate (=100), estimate_tokens includes it
        assert est >= 100

    @pytest.mark.asyncio
    async def test_should_trigger_compaction_healthy(
        self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy
    ) -> None:
        gate.record_usage(10_000)  # ~9% usage
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        trigger = await asm.should_trigger_compaction()
        assert trigger is False

    @pytest.mark.asyncio
    async def test_should_trigger_compaction_critical(
        self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy
    ) -> None:
        gate.record_usage(90_000)  # ~83% usage (above 80% threshold)
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        trigger = await asm.should_trigger_compaction()
        assert trigger is True

    @pytest.mark.asyncio
    async def test_get_context_dict(self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        rm = RepoMapSnapshot(workspace="/repo", text="src/\n  main.py")
        await asm.set_repo_map(rm)
        d = asm.get_context_dict()
        assert d["role"] == "system"
        assert d["name"] == "working_set"
        assert "Repo Map" in d["content"]

    @pytest.mark.asyncio
    async def test_flush_deferred(self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy) -> None:
        # Exhaust: 128k * 0.80 = 102,400 effective limit, record 103,000 -> headroom = -600
        gate.record_usage(103_000)
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        await asm.add_slice("a.py", 1, 10, "x" * 2000, priority=5)
        assets = asm.flush_deferred()
        assert len(assets) == 1
        assert asm._working_set.deferred_assets == []

    @pytest.mark.asyncio
    async def test_set_phase(self, gate: ContextBudgetGate, policy: DefaultExplorationPolicy) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        asm.set_phase(ExplorationPhase.SEARCH)
        assert asm._ctx.phase == ExplorationPhase.SEARCH

    @pytest.mark.asyncio
    async def test_build_repo_map_with_intelligence_skips_non_code_domains(
        self,
        gate: ContextBudgetGate,
        policy: DefaultExplorationPolicy,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        calls = {"count": 0}

        def _should_not_call(*_args: object, **_kwargs: object) -> object:
            calls["count"] += 1
            raise AssertionError("repo intelligence should not be called for document domain")

        monkeypatch.setattr(
            "polaris.kernelone.context.repo_intelligence.get_repo_intelligence",
            _should_not_call,
        )

        ws = await asm.build_repo_map_with_intelligence(domain="document")
        assert calls["count"] == 0
        assert ws.repo_map is None

    @pytest.mark.asyncio
    async def test_build_repo_map_with_intelligence_populates_repo_map(
        self,
        gate: ContextBudgetGate,
        policy: DefaultExplorationPolicy,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        asm = WorkingSetAssembler(workspace="/repo", budget_gate=gate, policy=policy)
        captured: dict[str, object] = {}

        class _FakeResult:
            def to_text(self) -> str:
                return "【Ranked Files】\n  0.900 src/main.py"

        class _FakeFacade:
            def get_repo_map(self, **kwargs: object) -> _FakeResult:
                captured.update(kwargs)
                return _FakeResult()

        monkeypatch.setattr(
            "polaris.kernelone.context.repo_intelligence.get_repo_intelligence",
            lambda *_args, **_kwargs: _FakeFacade(),
        )

        ws = await asm.build_repo_map_with_intelligence(
            domain="code",
            mentioned_idents=["main"],
            max_files=20,
        )
        assert ws.repo_map is not None
        assert "Ranked Files" in ws.repo_map.text
        assert captured.get("mentioned_idents") == ["main"]
        assert any(item == "repo_intelligence:code" for item in ws.expansion_history)


class TestDefaultExplorationPolicy:
    @pytest.fixture
    def policy(self) -> DefaultExplorationPolicy:
        return DefaultExplorationPolicy()

    @pytest.mark.asyncio
    async def test_auto_approve_high_priority(self, policy: DefaultExplorationPolicy) -> None:
        budget = ContextBudget(model_window=128_000, safety_margin=0.80, current_tokens=0)
        ctx = ExplorationContext(phase=ExplorationPhase.SLICE, workspace="/repo")
        candidate = AssetCandidate(AssetKind.CODE_SLICE, "a.py", (1, 10), 100, priority=10)
        decision = await policy.should_expand(budget, candidate, ctx)
        assert decision == ExpansionDecision.APPROVED

    @pytest.mark.asyncio
    async def test_deny_seen_asset(self, policy: DefaultExplorationPolicy) -> None:
        budget = ContextBudget(model_window=128_000, safety_margin=0.80, current_tokens=0)
        ctx = ExplorationContext(
            phase=ExplorationPhase.SLICE,
            workspace="/repo",
            seen_assets=frozenset({"a.py:1-10"}),
        )
        candidate = AssetCandidate(AssetKind.CODE_SLICE, "a.py", (1, 10), 100, priority=10)
        decision = await policy.should_expand(budget, candidate, ctx)
        assert decision == ExpansionDecision.DENIED

    @pytest.mark.asyncio
    async def test_defer_mid_priority(self, policy: DefaultExplorationPolicy) -> None:
        budget = ContextBudget(model_window=128_000, safety_margin=0.80, current_tokens=0)
        ctx = ExplorationContext(phase=ExplorationPhase.SLICE, workspace="/repo")
        candidate = AssetCandidate(AssetKind.CODE_SLICE, "a.py", (1, 10), 100, priority=3)
        decision = await policy.should_expand(budget, candidate, ctx)
        assert decision == ExpansionDecision.DEFERRED

    @pytest.mark.asyncio
    async def test_defer_when_exceeds_budget(self, policy: DefaultExplorationPolicy) -> None:
        budget = ContextBudget(model_window=128_000, safety_margin=0.80, current_tokens=100_000)
        ctx = ExplorationContext(phase=ExplorationPhase.SLICE, workspace="/repo")
        candidate = AssetCandidate(AssetKind.CODE_SLICE, "a.py", (1, 10), 10_000, priority=5)
        decision = await policy.should_expand(budget, candidate, ctx)
        assert decision == ExpansionDecision.DEFERRED

    @pytest.mark.asyncio
    async def test_should_compact_triggered(self, policy: DefaultExplorationPolicy) -> None:
        triggered = await policy.should_compact(
            current_tokens=85_000,
            effective_limit=102_400,  # 128k * 0.8
            phase=ExplorationPhase.SLICE,
        )
        assert triggered is True

    @pytest.mark.asyncio
    async def test_should_compact_not_triggered(self, policy: DefaultExplorationPolicy) -> None:
        triggered = await policy.should_compact(
            current_tokens=30_000,
            effective_limit=102_400,
            phase=ExplorationPhase.SLICE,
        )
        assert triggered is False

    @pytest.mark.asyncio
    async def test_select_next_tools_returns_list(self, policy: DefaultExplorationPolicy) -> None:
        ctx = ExplorationContext(phase=ExplorationPhase.SEARCH, workspace="/repo")
        tools = await policy.select_next_tools(ExplorationPhase.SEARCH, ctx)
        assert isinstance(tools, list)
        if tools:
            assert "tool" in tools[0]

    def test_infer_phase_empty_history_returns_map(self, policy: DefaultExplorationPolicy) -> None:
        assert policy.infer_phase([]) == ExplorationPhase.MAP

    def test_infer_phase_repo_map_returns_search(self, policy: DefaultExplorationPolicy) -> None:
        assert policy.infer_phase(["repo_map_build"]) == ExplorationPhase.SEARCH
        assert policy.infer_phase(["custom_repo_map_tool"]) == ExplorationPhase.SEARCH

    def test_infer_phase_ripgrep_returns_slice(self, policy: DefaultExplorationPolicy) -> None:
        assert policy.infer_phase(["ripgrep(pattern=foo)"]) == ExplorationPhase.SLICE
        assert policy.infer_phase(["repo_rg(pattern=bar)"]) == ExplorationPhase.SLICE
        assert policy.infer_phase(["search_code(query=baz)"]) == ExplorationPhase.SLICE

    def test_infer_phase_repo_read_returns_expand(self, policy: DefaultExplorationPolicy) -> None:
        assert policy.infer_phase(["repo_read(file=a.py)"]) == ExplorationPhase.EXPAND
        assert policy.infer_phase(["repo_read_slice(file=b.py)"]) == ExplorationPhase.EXPAND

    def test_infer_phase_unknown_defaults_to_search(self, policy: DefaultExplorationPolicy) -> None:
        assert policy.infer_phase(["unknown_tool"]) == ExplorationPhase.SEARCH
        assert policy.infer_phase(["list_directory"]) == ExplorationPhase.SEARCH


class TestNeighborFileForSlice:
    def test_no_neighbor(self, tmp_path: Path) -> None:
        sl = CodeSlice(file_path=str(tmp_path / "a.py"), start_line=1, end_line=10, content="")
        result = _neighbor_file_for_slice(sl)
        # No test file exists in tmp_path
        assert result is None
