"""Tests for internal/tot.py — ToTEngine branch lifecycle and pruning."""

from __future__ import annotations

import uuid

from polaris.cells.roles.engine.internal.base import EngineContext
from polaris.cells.roles.engine.internal.tot import (
    BranchStatus,
    ThoughtBranch,
    ToTEngine,
)


def _make_engine(**kwargs) -> ToTEngine:
    return ToTEngine(workspace="/tmp", **kwargs)


def _ctx() -> EngineContext:
    return EngineContext(workspace="/tmp", role="architect", task="design system")


class TestThoughtBranch:
    """ThoughtBranch dataclass — requires `id` as first positional arg."""

    def test_thought_branch_requires_id(self) -> None:
        branch = ThoughtBranch(id="branch-1", thought="initial thought")
        assert branch.id == "branch-1"
        assert branch.thought == "initial thought"

    def test_thought_branch_defaults(self) -> None:
        branch = ThoughtBranch(id="b1", thought="x")
        assert branch.status == BranchStatus.PENDING
        assert branch.score == 0.0
        assert branch.reasoning == ""
        assert branch.confidence == 0.5
        assert branch.children == []

    def test_thought_branch_with_fields(self) -> None:
        branch = ThoughtBranch(
            id="best",
            thought="solve it well",
            reasoning="good reasoning",
            confidence=0.9,
            score=0.85,
            status=BranchStatus.EVALUATED,
            depth=2,
        )
        assert branch.score == 0.85
        assert branch.depth == 2
        assert branch.status == BranchStatus.EVALUATED

    def test_thought_branch_to_dict(self) -> None:
        branch = ThoughtBranch(id="b1", thought="x")
        d = branch.to_dict()
        assert d["id"] == "b1"
        assert d["thought"] == "x"
        assert d["status"] == "pending"


class TestBranchStatus:
    """BranchStatus enum values."""

    def test_branch_status_values(self) -> None:
        assert BranchStatus.PENDING.value == "pending"
        assert BranchStatus.EXPANDED.value == "expanded"
        assert BranchStatus.EVALUATED.value == "evaluated"
        assert BranchStatus.PRUNED.value == "pruned"
        assert BranchStatus.COMPLETED.value == "completed"


class TestToTEngineDefaults:
    """Engine initialization."""

    def test_engine_has_empty_branches_dict(self) -> None:
        """Branches are stored as a dict[str, ThoughtBranch]."""
        engine = _make_engine()
        assert isinstance(engine._branches, dict)
        assert len(engine._branches) == 0

    def test_engine_strategy(self) -> None:
        from polaris.cells.roles.engine.internal.base import EngineStrategy

        engine = _make_engine()
        assert engine.strategy == EngineStrategy.TOT

    def test_engine_max_branches_capped_at_10(self) -> None:
        """max_branches is capped at 10 to prevent resource exhaustion."""
        engine = _make_engine(max_branches=999)
        assert engine.max_branches == 10

    def test_engine_max_depth_capped_at_20(self) -> None:
        engine = _make_engine(max_depth=999)
        assert engine.max_depth == 20

    def test_pruning_threshold_clamped_to_0_1(self) -> None:
        """pruning_threshold is clamped to [0.0, 1.0]."""
        engine = _make_engine(pruning_threshold=5.0)
        assert engine.pruning_threshold == 1.0
        engine2 = _make_engine(pruning_threshold=-1.0)
        assert engine2.pruning_threshold == 0.0


class TestToTPruning:
    """_prune_branches — takes no arguments, uses instance pruning_threshold."""

    def test_prune_branches_prunes_below_threshold(self) -> None:
        """Branches with score below threshold get PRUNED status."""
        engine = _make_engine(pruning_threshold=0.5)
        bid1 = str(uuid.uuid4())
        bid2 = str(uuid.uuid4())
        bid3 = str(uuid.uuid4())
        engine._branches = {
            bid1: ThoughtBranch(id=bid1, thought="high", status=BranchStatus.EVALUATED, score=0.9, depth=0),
            bid2: ThoughtBranch(id=bid2, thought="low", status=BranchStatus.EXPANDED, score=0.1, depth=0),
            bid3: ThoughtBranch(id=bid3, thought="medium", status=BranchStatus.EXPANDED, score=0.3, depth=0),
        }
        # _prune_branches() takes no args; uses self.pruning_threshold internally
        engine._prune_branches()
        # Branches with score < threshold and status EXPANDED get PRUNED
        assert engine._branches[bid2].status == BranchStatus.PRUNED
        assert engine._branches[bid3].status == BranchStatus.PRUNED
        # High-score branch survives
        assert engine._branches[bid1].status == BranchStatus.EVALUATED

    def test_prune_branches_keeps_high_scoring(self) -> None:
        """Highest-scoring branch survives pruning."""
        engine = _make_engine(pruning_threshold=0.5)
        bid_best = str(uuid.uuid4())
        bid_worst = str(uuid.uuid4())
        engine._branches = {
            bid_best: ThoughtBranch(id=bid_best, thought="best", status=BranchStatus.EXPANDED, score=0.95, depth=0),
            bid_worst: ThoughtBranch(id=bid_worst, thought="worst", status=BranchStatus.EXPANDED, score=0.05, depth=0),
        }
        engine._prune_branches()
        assert engine._branches[bid_best].status != BranchStatus.PRUNED
        assert engine._branches[bid_worst].status == BranchStatus.PRUNED


class TestToTEngineLifecycle:
    """End-to-end lifecycle via step() and _initialize_root."""

    def test_initialize_root_sets_root_id(self) -> None:
        """_initialize_root (async) sets _root_id and populates _branches."""
        import asyncio

        engine = _make_engine()

        async def run():
            ctx = _ctx()
            await engine._initialize_root(ctx, "design system")
            return engine._root_id

        loop = asyncio.new_event_loop()
        try:
            root_id = loop.run_until_complete(run())
            # _initialize_root sets engine._root_id to "root" (no return value)
            assert root_id == "root"
            assert root_id in engine._branches
            assert engine._branches[root_id].depth == 0
        finally:
            loop.close()
