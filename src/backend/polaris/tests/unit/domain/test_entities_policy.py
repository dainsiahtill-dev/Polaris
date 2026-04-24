"""Tests for polaris.domain.entities.policy."""

from __future__ import annotations

from polaris.domain.entities.policy import (
    BudgetPolicy,
    BuildLoopPolicy,
    ContextPolicy,
    EvidencePolicy,
    FactoryPolicy,
    IoPolicy,
    MemoryPolicy,
    Policy,
    QaPolicy,
    RagPolicy,
    RepairPolicy,
    RiskPolicy,
)


class TestRepairPolicy:
    def test_defaults(self) -> None:
        p = RepairPolicy()
        assert p.auto_repair is True
        assert p.max_attempts == 3
        assert p.reviewer_enabled is True
        assert p.rollback_on_fail is True


class TestRiskPolicy:
    def test_defaults(self) -> None:
        p = RiskPolicy()
        assert p.block_threshold == 7
        assert p.rollback_on_block is False


class TestEvidencePolicy:
    def test_defaults(self) -> None:
        p = EvidencePolicy()
        assert p.verbosity == "summary"
        assert p.write_enabled is True


class TestRagPolicy:
    def test_defaults(self) -> None:
        p = RagPolicy()
        assert p.topk == 5


class TestMemoryPolicy:
    def test_defaults(self) -> None:
        p = MemoryPolicy()
        assert p.enabled is True
        assert p.backend == "file"
        assert p.store_enabled is True


class TestBudgetPolicy:
    def test_defaults(self) -> None:
        p = BudgetPolicy()
        assert p.max_tool_rounds == 10
        assert p.max_total_lines_read == 50000


class TestQaPolicy:
    def test_defaults(self) -> None:
        p = QaPolicy()
        assert p.enabled is True
        assert p.default_tools is True


class TestContextPolicy:
    def test_defaults(self) -> None:
        p = ContextPolicy()
        assert p.pm_tasks_max_chars == 4000
        assert p.tool_output_max_chars == 12000


class TestBuildLoopPolicy:
    def test_defaults(self) -> None:
        p = BuildLoopPolicy()
        assert p.budget == 4
        assert p.stall_round_threshold == 2


class TestIoPolicy:
    def test_defaults(self) -> None:
        p = IoPolicy()
        assert p.jsonl_buffered is True
        assert p.flush_interval_sec == 5.0


class TestFactoryPolicy:
    def test_defaults(self) -> None:
        p = FactoryPolicy()
        assert p.default_strategy == "rollback"
        assert p.max_fix_attempts == 3
        assert p.enforce_hp_flow is True
        assert p.budget_overflow_ratio == 1.5


class TestPolicy:
    def test_defaults(self) -> None:
        policy = Policy()
        assert isinstance(policy.repair, RepairPolicy)
        assert isinstance(policy.risk, RiskPolicy)
        assert isinstance(policy.budgets, BudgetPolicy)

    def test_from_dict(self) -> None:
        data = {
            "repair": {"auto_repair": False, "max_attempts": 5},
            "risk": {"block_threshold": 5},
            "budgets": {"max_tool_rounds": 20},
        }
        policy = Policy.from_dict(data)
        assert policy.repair.auto_repair is False
        assert policy.repair.max_attempts == 5
        assert policy.risk.block_threshold == 5
        assert policy.budgets.max_tool_rounds == 20

    def test_from_dict_empty(self) -> None:
        policy = Policy.from_dict({})
        assert policy.repair.auto_repair is True
        assert policy.budgets.max_tool_rounds == 10

    def test_from_dict_non_dict(self) -> None:
        policy = Policy.from_dict(None)  # type: ignore[arg-type]
        assert isinstance(policy, Policy)

    def test_to_dict(self) -> None:
        policy = Policy()
        d = policy.to_dict()
        assert "repair" in d
        assert "risk" in d
        assert "evidence" in d
        assert d["repair"]["auto_repair"] is True

    def test_post_init_dict_conversion(self) -> None:
        policy = Policy(repair={"auto_repair": False})
        assert isinstance(policy.repair, RepairPolicy)
        assert policy.repair.auto_repair is False

    def test_post_init_nested_dicts(self) -> None:
        policy = Policy(
            repair={"auto_repair": False},
            risk={"block_threshold": 3},
            evidence={"verbosity": "full"},
            rag={"topk": 10},
            memory={"enabled": False},
            budgets={"max_tool_rounds": 5},
            qa={"enabled": False},
            context={"pm_tasks_max_chars": 2000},
            build_loop={"budget": 2},
            io={"flush_interval_sec": 1.0},
            factory={"default_strategy": "defect_loop"},
        )
        assert policy.risk.block_threshold == 3
        assert policy.evidence.verbosity == "full"
        assert policy.rag.topk == 10
        assert policy.memory.enabled is False
        assert policy.budgets.max_tool_rounds == 5
        assert policy.qa.enabled is False
        assert policy.context.pm_tasks_max_chars == 2000
        assert policy.build_loop.budget == 2
        assert policy.io.flush_interval_sec == 1.0
        assert policy.factory.default_strategy == "defect_loop"
