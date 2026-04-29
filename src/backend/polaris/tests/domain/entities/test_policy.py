"""Comprehensive tests for polaris.domain.entities.policy."""

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

# ---------------------------------------------------------------------------
# Sub-policy classes
# ---------------------------------------------------------------------------


class TestRepairPolicy:
    def test_defaults(self):
        p = RepairPolicy()
        assert p.auto_repair is True
        assert p.max_attempts == 3
        assert p.reviewer_enabled is True
        assert p.reviewer_rounds == 1
        assert p.rollback_on_fail is True

    def test_custom_values(self):
        p = RepairPolicy(
            auto_repair=False, max_attempts=5, reviewer_enabled=False, reviewer_rounds=2, rollback_on_fail=False
        )
        assert p.auto_repair is False
        assert p.max_attempts == 5
        assert p.reviewer_enabled is False
        assert p.reviewer_rounds == 2
        assert p.rollback_on_fail is False


class TestRiskPolicy:
    def test_defaults(self):
        p = RiskPolicy()
        assert p.block_threshold == 7
        assert p.rollback_on_block is False

    def test_custom_values(self):
        p = RiskPolicy(block_threshold=5, rollback_on_block=True)
        assert p.block_threshold == 5
        assert p.rollback_on_block is True


class TestEvidencePolicy:
    def test_defaults(self):
        p = EvidencePolicy()
        assert p.verbosity == "summary"
        assert p.write_enabled is True

    def test_custom_values(self):
        p = EvidencePolicy(verbosity="full", write_enabled=False)
        assert p.verbosity == "full"
        assert p.write_enabled is False

    def test_invalid_verbosity_still_accepted(self):
        # Dataclass has no validation; any string is accepted
        p = EvidencePolicy(verbosity="invalid")
        assert p.verbosity == "invalid"


class TestRagPolicy:
    def test_defaults(self):
        p = RagPolicy()
        assert p.topk == 5

    def test_custom_values(self):
        p = RagPolicy(topk=10)
        assert p.topk == 10


class TestMemoryPolicy:
    def test_defaults(self):
        p = MemoryPolicy()
        assert p.enabled is True
        assert p.backend == "file"
        assert p.store_enabled is True
        assert p.store_every == 1
        assert p.store_on_accept is False

    def test_custom_values(self):
        p = MemoryPolicy(enabled=False, backend="lancedb", store_enabled=False, store_every=5, store_on_accept=True)
        assert p.enabled is False
        assert p.backend == "lancedb"
        assert p.store_enabled is False
        assert p.store_every == 5
        assert p.store_on_accept is True


class TestBudgetPolicy:
    def test_defaults(self):
        p = BudgetPolicy()
        assert p.max_tool_rounds == 10
        assert p.max_total_lines_read == 50000

    def test_custom_values(self):
        p = BudgetPolicy(max_tool_rounds=20, max_total_lines_read=100000)
        assert p.max_tool_rounds == 20
        assert p.max_total_lines_read == 100000

    def test_zero_values(self):
        p = BudgetPolicy(max_tool_rounds=0, max_total_lines_read=0)
        assert p.max_tool_rounds == 0
        assert p.max_total_lines_read == 0


class TestQaPolicy:
    def test_defaults(self):
        p = QaPolicy()
        assert p.enabled is True
        assert p.default_tools is True

    def test_custom_values(self):
        p = QaPolicy(enabled=False, default_tools=False)
        assert p.enabled is False
        assert p.default_tools is False


class TestContextPolicy:
    def test_defaults(self):
        p = ContextPolicy()
        assert p.pm_tasks_max_chars == 4000
        assert p.known_files_max_chars == 8000
        assert p.last_result_max_chars == 4000
        assert p.tool_output_max_chars == 12000
        assert p.planner_output_max_chars == 4000
        assert p.ollama_output_max_chars == 4000

    def test_custom_values(self):
        p = ContextPolicy(pm_tasks_max_chars=2000, tool_output_max_chars=6000)
        assert p.pm_tasks_max_chars == 2000
        assert p.tool_output_max_chars == 6000
        assert p.known_files_max_chars == 8000


class TestBuildLoopPolicy:
    def test_defaults(self):
        p = BuildLoopPolicy()
        assert p.budget == 4
        assert p.stall_round_threshold == 2
        assert p.verify_requires_ready is False

    def test_custom_values(self):
        p = BuildLoopPolicy(budget=8, stall_round_threshold=3, verify_requires_ready=True)
        assert p.budget == 8
        assert p.stall_round_threshold == 3
        assert p.verify_requires_ready is True


class TestIoPolicy:
    def test_defaults(self):
        p = IoPolicy()
        assert p.jsonl_buffered is True
        assert p.flush_interval_sec == 5.0
        assert p.flush_batch == 100
        assert p.max_buffer == 1000

    def test_custom_values(self):
        p = IoPolicy(jsonl_buffered=False, flush_interval_sec=1.0, flush_batch=50, max_buffer=500)
        assert p.jsonl_buffered is False
        assert p.flush_interval_sec == 1.0
        assert p.flush_batch == 50
        assert p.max_buffer == 500


class TestFactoryPolicy:
    def test_defaults(self):
        p = FactoryPolicy()
        assert p.default_strategy == "rollback"
        assert p.auditor_failure_action == ""
        assert p.max_fix_attempts == 3
        assert p.require_defect_ticket is False
        assert p.defect_ticket_fields == []
        assert p.require_evidence_run is False
        assert p.require_fast_loop_before_evidence_run is False
        assert p.hard_rollback_enabled is False
        assert p.hard_rollback_trigger_conditions == []
        assert p.gate_set_default == "full"
        assert p.allow_reduced_gate is False
        assert p.enforce_hp_flow is True
        assert p.required_pipeline == []
        assert p.standalone_allowed is True
        assert p.budget_overflow_ratio == 1.5
        assert p.forbid_missing_evidence is False

    def test_custom_values(self):
        p = FactoryPolicy(
            default_strategy="defect_loop",
            max_fix_attempts=5,
            require_defect_ticket=True,
            defect_ticket_fields=["id", "severity"],
            hard_rollback_enabled=True,
            gate_set_default="minimal",
            enforce_hp_flow=False,
            budget_overflow_ratio=2.0,
        )
        assert p.default_strategy == "defect_loop"
        assert p.max_fix_attempts == 5
        assert p.require_defect_ticket is True
        assert p.defect_ticket_fields == ["id", "severity"]
        assert p.hard_rollback_enabled is True
        assert p.gate_set_default == "minimal"
        assert p.enforce_hp_flow is False
        assert p.budget_overflow_ratio == 2.0


# ---------------------------------------------------------------------------
# Policy (aggregate)
# ---------------------------------------------------------------------------


class TestPolicyDefaults:
    def test_default_creation(self):
        p = Policy()
        assert isinstance(p.repair, RepairPolicy)
        assert isinstance(p.risk, RiskPolicy)
        assert isinstance(p.evidence, EvidencePolicy)
        assert isinstance(p.rag, RagPolicy)
        assert isinstance(p.memory, MemoryPolicy)
        assert isinstance(p.budgets, BudgetPolicy)
        assert isinstance(p.qa, QaPolicy)
        assert isinstance(p.context, ContextPolicy)
        assert isinstance(p.build_loop, BuildLoopPolicy)
        assert isinstance(p.io, IoPolicy)
        assert isinstance(p.factory, FactoryPolicy)

    def test_default_values(self):
        p = Policy()
        assert p.repair.auto_repair is True
        assert p.risk.block_threshold == 7
        assert p.evidence.verbosity == "summary"
        assert p.rag.topk == 5
        assert p.memory.backend == "file"
        assert p.budgets.max_tool_rounds == 10
        assert p.qa.enabled is True
        assert p.context.pm_tasks_max_chars == 4000
        assert p.build_loop.budget == 4
        assert p.io.flush_interval_sec == 5.0
        assert p.factory.default_strategy == "rollback"


class TestPolicyPostInit:
    def test_dict_init_repair(self):
        p = Policy(repair={"auto_repair": False})
        assert isinstance(p.repair, RepairPolicy)
        assert p.repair.auto_repair is False
        assert p.repair.max_attempts == 3

    def test_dict_init_risk(self):
        p = Policy(risk={"block_threshold": 3})
        assert isinstance(p.risk, RiskPolicy)
        assert p.risk.block_threshold == 3

    def test_dict_init_evidence(self):
        p = Policy(evidence={"verbosity": "minimal"})
        assert isinstance(p.evidence, EvidencePolicy)
        assert p.evidence.verbosity == "minimal"

    def test_dict_init_rag(self):
        p = Policy(rag={"topk": 20})
        assert isinstance(p.rag, RagPolicy)
        assert p.rag.topk == 20

    def test_dict_init_memory(self):
        p = Policy(memory={"backend": "lancedb"})
        assert isinstance(p.memory, MemoryPolicy)
        assert p.memory.backend == "lancedb"

    def test_dict_init_budgets(self):
        p = Policy(budgets={"max_tool_rounds": 50})
        assert isinstance(p.budgets, BudgetPolicy)
        assert p.budgets.max_tool_rounds == 50

    def test_dict_init_qa(self):
        p = Policy(qa={"enabled": False})
        assert isinstance(p.qa, QaPolicy)
        assert p.qa.enabled is False

    def test_dict_init_context(self):
        p = Policy(context={"pm_tasks_max_chars": 2000})
        assert isinstance(p.context, ContextPolicy)
        assert p.context.pm_tasks_max_chars == 2000

    def test_dict_init_build_loop(self):
        p = Policy(build_loop={"budget": 10})
        assert isinstance(p.build_loop, BuildLoopPolicy)
        assert p.build_loop.budget == 10

    def test_dict_init_io(self):
        p = Policy(io={"flush_interval_sec": 1.0})
        assert isinstance(p.io, IoPolicy)
        assert p.io.flush_interval_sec == 1.0

    def test_dict_init_factory(self):
        p = Policy(factory={"default_strategy": "defect_loop"})
        assert isinstance(p.factory, FactoryPolicy)
        assert p.factory.default_strategy == "defect_loop"

    def test_object_init_unchanged(self):
        repair = RepairPolicy(auto_repair=False)
        p = Policy(repair=repair)
        assert p.repair is repair


class TestPolicyFromDict:
    def test_empty_dict(self):
        p = Policy.from_dict({})
        assert p.repair.auto_repair is True
        assert p.risk.block_threshold == 7
        assert p.memory.backend == "file"

    def test_none_input(self):
        p = Policy.from_dict(None)  # type: ignore[arg-type]
        assert p.repair.auto_repair is True

    def test_non_dict_input(self):
        p = Policy.from_dict("invalid")  # type: ignore[arg-type]
        assert p.repair.auto_repair is True

    def test_partial_dict(self):
        p = Policy.from_dict({"repair": {"auto_repair": False}})
        assert p.repair.auto_repair is False
        assert p.repair.max_attempts == 3
        assert p.risk.block_threshold == 7

    def test_full_dict(self):
        data = {
            "repair": {
                "auto_repair": False,
                "max_attempts": 5,
                "reviewer_enabled": False,
                "reviewer_rounds": 2,
                "rollback_on_fail": False,
            },
            "risk": {"block_threshold": 5, "rollback_on_block": True},
            "evidence": {"verbosity": "full", "write_enabled": False},
            "rag": {"topk": 10},
            "memory": {
                "enabled": False,
                "backend": "lancedb",
                "store_enabled": False,
                "store_every": 5,
                "store_on_accept": True,
            },
            "budgets": {"max_tool_rounds": 20, "max_total_lines_read": 100000},
            "qa": {"enabled": False, "default_tools": False},
            "context": {
                "pm_tasks_max_chars": 2000,
                "known_files_max_chars": 4000,
                "last_result_max_chars": 2000,
                "tool_output_max_chars": 6000,
                "planner_output_max_chars": 2000,
                "ollama_output_max_chars": 2000,
            },
            "build_loop": {"budget": 8, "stall_round_threshold": 3, "verify_requires_ready": True},
            "io": {"jsonl_buffered": False, "flush_interval_sec": 1.0, "flush_batch": 50, "max_buffer": 500},
            "factory": {
                "default_strategy": "defect_loop",
                "auditor_failure_action": "warn",
                "max_fix_attempts": 5,
                "require_defect_ticket": True,
                "defect_ticket_fields": ["id"],
                "require_evidence_run": True,
                "require_fast_loop_before_evidence_run": True,
                "hard_rollback_enabled": True,
                "hard_rollback_trigger_conditions": ["critical"],
                "gate_set_default": "minimal",
                "allow_reduced_gate": True,
                "enforce_hp_flow": False,
                "required_pipeline": ["lint"],
                "standalone_allowed": False,
                "budget_overflow_ratio": 2.0,
                "forbid_missing_evidence": True,
            },
        }
        p = Policy.from_dict(data)
        assert p.repair.auto_repair is False
        assert p.repair.max_attempts == 5
        assert p.risk.block_threshold == 5
        assert p.risk.rollback_on_block is True
        assert p.evidence.verbosity == "full"
        assert p.rag.topk == 10
        assert p.memory.enabled is False
        assert p.memory.backend == "lancedb"
        assert p.budgets.max_tool_rounds == 20
        assert p.qa.enabled is False
        assert p.context.pm_tasks_max_chars == 2000
        assert p.build_loop.budget == 8
        assert p.io.jsonl_buffered is False
        assert p.factory.default_strategy == "defect_loop"
        assert p.factory.max_fix_attempts == 5
        assert p.factory.require_defect_ticket is True
        assert p.factory.defect_ticket_fields == ["id"]
        assert p.factory.hard_rollback_enabled is True
        assert p.factory.gate_set_default == "minimal"
        assert p.factory.allow_reduced_gate is True
        assert p.factory.enforce_hp_flow is False
        assert p.factory.required_pipeline == ["lint"]
        assert p.factory.standalone_allowed is False
        assert p.factory.budget_overflow_ratio == 2.0
        assert p.factory.forbid_missing_evidence is True

    def test_nested_partial_dict(self):
        p = Policy.from_dict({"factory": {"max_fix_attempts": 10}})
        assert p.factory.max_fix_attempts == 10
        assert p.factory.default_strategy == "rollback"


class TestPolicyToDict:
    def test_structure(self):
        p = Policy()
        d = p.to_dict()
        expected_keys = {
            "repair",
            "risk",
            "evidence",
            "rag",
            "memory",
            "budgets",
            "qa",
            "context",
            "build_loop",
            "io",
            "factory",
        }
        assert set(d.keys()) == expected_keys

    def test_values(self):
        p = Policy()
        d = p.to_dict()
        assert d["repair"]["auto_repair"] is True
        assert d["risk"]["block_threshold"] == 7
        assert d["rag"]["topk"] == 5
        assert d["memory"]["backend"] == "file"
        assert d["budgets"]["max_tool_rounds"] == 10
        assert d["qa"]["enabled"] is True
        assert d["context"]["pm_tasks_max_chars"] == 4000
        assert d["build_loop"]["budget"] == 4
        assert d["io"]["flush_interval_sec"] == 5.0
        assert d["factory"]["default_strategy"] == "rollback"

    def test_factory_fields_present(self):
        p = Policy()
        d = p.to_dict()
        factory = d["factory"]
        assert "default_strategy" in factory
        assert "auditor_failure_action" in factory
        assert "max_fix_attempts" in factory
        assert "require_defect_ticket" in factory
        assert "defect_ticket_fields" in factory
        assert "require_evidence_run" in factory
        assert "require_fast_loop_before_evidence_run" in factory
        assert "hard_rollback_enabled" in factory
        assert "hard_rollback_trigger_conditions" in factory
        assert "gate_set_default" in factory
        assert "allow_reduced_gate" in factory
        assert "enforce_hp_flow" in factory
        assert "required_pipeline" in factory
        assert "standalone_allowed" in factory
        assert "budget_overflow_ratio" in factory
        assert "forbid_missing_evidence" in factory


class TestPolicyRoundtrip:
    def test_to_dict_from_dict(self):
        original = Policy.from_dict({"repair": {"auto_repair": False, "max_attempts": 5}})
        d = original.to_dict()
        restored = Policy.from_dict(d)
        assert restored.repair.auto_repair is False
        assert restored.repair.max_attempts == 5
        assert restored.risk.block_threshold == 7

    def test_custom_roundtrip(self):
        original = Policy(
            repair=RepairPolicy(auto_repair=False),
            risk=RiskPolicy(block_threshold=3),
            evidence=EvidencePolicy(verbosity="minimal"),
        )
        d = original.to_dict()
        restored = Policy.from_dict(d)
        assert restored.repair.auto_repair is False
        assert restored.risk.block_threshold == 3
        assert restored.evidence.verbosity == "minimal"
        assert restored.rag.topk == 5
