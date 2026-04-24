"""Policy system for Director v2.

Migrated from: core/polaris_loop/director_policy_runtime.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RepairPolicy:
    """Policy for auto-repair behavior."""

    auto_repair: bool = True
    max_attempts: int = 3
    reviewer_enabled: bool = True
    reviewer_rounds: int = 1
    rollback_on_fail: bool = True


@dataclass
class RiskPolicy:
    """Policy for risk management."""

    block_threshold: int = 7  # 0-10 scale
    rollback_on_block: bool = False


@dataclass
class EvidencePolicy:
    """Policy for evidence collection."""

    verbosity: str = "summary"  # "full", "summary", "minimal"
    write_enabled: bool = True


@dataclass
class RagPolicy:
    """Policy for RAG (Retrieval Augmented Generation)."""

    topk: int = 5


@dataclass
class MemoryPolicy:
    """Policy for memory management."""

    enabled: bool = True
    backend: str = "file"  # "none", "file", "lancedb"
    store_enabled: bool = True
    store_every: int = 1
    store_on_accept: bool = False


@dataclass
class BudgetPolicy:
    """Policy for resource budgets."""

    max_tool_rounds: int = 10
    max_total_lines_read: int = 50000


@dataclass
class QaPolicy:
    """Policy for QA/verification."""

    enabled: bool = True
    default_tools: bool = True


@dataclass
class ContextPolicy:
    """Policy for context management."""

    pm_tasks_max_chars: int = 4000
    known_files_max_chars: int = 8000
    last_result_max_chars: int = 4000
    tool_output_max_chars: int = 12000
    planner_output_max_chars: int = 4000
    ollama_output_max_chars: int = 4000


@dataclass
class BuildLoopPolicy:
    """Policy for build loop behavior."""

    budget: int = 4
    stall_round_threshold: int = 2
    verify_requires_ready: bool = False


@dataclass
class IoPolicy:
    """Policy for I/O behavior."""

    jsonl_buffered: bool = True
    flush_interval_sec: float = 5.0
    flush_batch: int = 100
    max_buffer: int = 1000


@dataclass
class FactoryPolicy:
    """Policy for factory-mode (v3 schema)."""

    default_strategy: str = "rollback"  # "rollback" or "defect_loop"
    auditor_failure_action: str = ""
    max_fix_attempts: int = 3
    require_defect_ticket: bool = False
    defect_ticket_fields: list[str] = field(default_factory=list)
    require_evidence_run: bool = False
    require_fast_loop_before_evidence_run: bool = False
    hard_rollback_enabled: bool = False
    hard_rollback_trigger_conditions: list[str] = field(default_factory=list)
    gate_set_default: str = "full"
    allow_reduced_gate: bool = False
    enforce_hp_flow: bool = True
    required_pipeline: list[str] = field(default_factory=list)
    standalone_allowed: bool = True
    budget_overflow_ratio: float = 1.5
    forbid_missing_evidence: bool = False


@dataclass
class Policy:
    """Complete policy configuration for Director."""

    repair: RepairPolicy = field(default_factory=RepairPolicy)
    risk: RiskPolicy = field(default_factory=RiskPolicy)
    evidence: EvidencePolicy = field(default_factory=EvidencePolicy)
    rag: RagPolicy = field(default_factory=RagPolicy)
    memory: MemoryPolicy = field(default_factory=MemoryPolicy)
    budgets: BudgetPolicy = field(default_factory=BudgetPolicy)
    qa: QaPolicy = field(default_factory=QaPolicy)
    context: ContextPolicy = field(default_factory=ContextPolicy)
    build_loop: BuildLoopPolicy = field(default_factory=BuildLoopPolicy)
    io: IoPolicy = field(default_factory=IoPolicy)
    factory: FactoryPolicy = field(default_factory=FactoryPolicy)

    def __post_init__(self) -> None:
        # Ensure nested dataclasses are properly instantiated
        if isinstance(self.repair, dict):
            object.__setattr__(self, "repair", RepairPolicy(**self.repair))
        if isinstance(self.risk, dict):
            object.__setattr__(self, "risk", RiskPolicy(**self.risk))
        if isinstance(self.evidence, dict):
            object.__setattr__(self, "evidence", EvidencePolicy(**self.evidence))
        if isinstance(self.rag, dict):
            object.__setattr__(self, "rag", RagPolicy(**self.rag))
        if isinstance(self.memory, dict):
            object.__setattr__(self, "memory", MemoryPolicy(**self.memory))
        if isinstance(self.budgets, dict):
            object.__setattr__(self, "budgets", BudgetPolicy(**self.budgets))
        if isinstance(self.qa, dict):
            object.__setattr__(self, "qa", QaPolicy(**self.qa))
        if isinstance(self.context, dict):
            object.__setattr__(self, "context", ContextPolicy(**self.context))
        if isinstance(self.build_loop, dict):
            object.__setattr__(self, "build_loop", BuildLoopPolicy(**self.build_loop))
        if isinstance(self.io, dict):
            object.__setattr__(self, "io", IoPolicy(**self.io))
        if isinstance(self.factory, dict):
            object.__setattr__(self, "factory", FactoryPolicy(**self.factory))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        """Create Policy from dictionary."""
        if not isinstance(data, dict):
            return cls()

        return cls(
            repair=RepairPolicy(
                auto_repair=data.get("repair", {}).get("auto_repair", True),
                max_attempts=data.get("repair", {}).get("max_attempts", 3),
                reviewer_enabled=data.get("repair", {}).get("reviewer_enabled", True),
                reviewer_rounds=data.get("repair", {}).get("reviewer_rounds", 1),
                rollback_on_fail=data.get("repair", {}).get("rollback_on_fail", True),
            ),
            risk=RiskPolicy(
                block_threshold=data.get("risk", {}).get("block_threshold", 7),
                rollback_on_block=data.get("risk", {}).get("rollback_on_block", False),
            ),
            evidence=EvidencePolicy(
                verbosity=data.get("evidence", {}).get("verbosity", "summary"),
                write_enabled=data.get("evidence", {}).get("write_enabled", True),
            ),
            rag=RagPolicy(
                topk=data.get("rag", {}).get("topk", 5),
            ),
            memory=MemoryPolicy(
                enabled=data.get("memory", {}).get("enabled", True),
                backend=data.get("memory", {}).get("backend", "file"),
                store_enabled=data.get("memory", {}).get("store_enabled", True),
                store_every=data.get("memory", {}).get("store_every", 1),
                store_on_accept=data.get("memory", {}).get("store_on_accept", False),
            ),
            budgets=BudgetPolicy(
                max_tool_rounds=data.get("budgets", {}).get("max_tool_rounds", 10),
                max_total_lines_read=data.get("budgets", {}).get("max_total_lines_read", 50000),
            ),
            qa=QaPolicy(
                enabled=data.get("qa", {}).get("enabled", True),
                default_tools=data.get("qa", {}).get("default_tools", True),
            ),
            context=ContextPolicy(
                pm_tasks_max_chars=data.get("context", {}).get("pm_tasks_max_chars", 4000),
                known_files_max_chars=data.get("context", {}).get("known_files_max_chars", 8000),
                last_result_max_chars=data.get("context", {}).get("last_result_max_chars", 4000),
                tool_output_max_chars=data.get("context", {}).get("tool_output_max_chars", 12000),
                planner_output_max_chars=data.get("context", {}).get("planner_output_max_chars", 4000),
                ollama_output_max_chars=data.get("context", {}).get("ollama_output_max_chars", 4000),
            ),
            build_loop=BuildLoopPolicy(
                budget=data.get("build_loop", {}).get("budget", 4),
                stall_round_threshold=data.get("build_loop", {}).get("stall_round_threshold", 2),
                verify_requires_ready=data.get("build_loop", {}).get("verify_requires_ready", False),
            ),
            io=IoPolicy(
                jsonl_buffered=data.get("io", {}).get("jsonl_buffered", True),
                flush_interval_sec=data.get("io", {}).get("flush_interval_sec", 5.0),
                flush_batch=data.get("io", {}).get("flush_batch", 100),
                max_buffer=data.get("io", {}).get("max_buffer", 1000),
            ),
            factory=FactoryPolicy(
                default_strategy=data.get("factory", {}).get("default_strategy", "rollback"),
                auditor_failure_action=data.get("factory", {}).get("auditor_failure_action", ""),
                max_fix_attempts=data.get("factory", {}).get("max_fix_attempts", 3),
                require_defect_ticket=data.get("factory", {}).get("require_defect_ticket", False),
                defect_ticket_fields=data.get("factory", {}).get("defect_ticket_fields", []),
                require_evidence_run=data.get("factory", {}).get("require_evidence_run", False),
                require_fast_loop_before_evidence_run=data.get("factory", {}).get(
                    "require_fast_loop_before_evidence_run", False
                ),
                hard_rollback_enabled=data.get("factory", {}).get("hard_rollback_enabled", False),
                hard_rollback_trigger_conditions=data.get("factory", {}).get("hard_rollback_trigger_conditions", []),
                gate_set_default=data.get("factory", {}).get("gate_set_default", "full"),
                allow_reduced_gate=data.get("factory", {}).get("allow_reduced_gate", False),
                enforce_hp_flow=data.get("factory", {}).get("enforce_hp_flow", True),
                required_pipeline=data.get("factory", {}).get("required_pipeline", []),
                standalone_allowed=data.get("factory", {}).get("standalone_allowed", True),
                budget_overflow_ratio=data.get("factory", {}).get("budget_overflow_ratio", 1.5),
                forbid_missing_evidence=data.get("factory", {}).get("forbid_missing_evidence", False),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert Policy to dictionary."""
        return {
            "repair": {
                "auto_repair": self.repair.auto_repair,
                "max_attempts": self.repair.max_attempts,
                "reviewer_enabled": self.repair.reviewer_enabled,
                "reviewer_rounds": self.repair.reviewer_rounds,
                "rollback_on_fail": self.repair.rollback_on_fail,
            },
            "risk": {
                "block_threshold": self.risk.block_threshold,
                "rollback_on_block": self.risk.rollback_on_block,
            },
            "evidence": {
                "verbosity": self.evidence.verbosity,
                "write_enabled": self.evidence.write_enabled,
            },
            "rag": {"topk": self.rag.topk},
            "memory": {
                "enabled": self.memory.enabled,
                "backend": self.memory.backend,
                "store_enabled": self.memory.store_enabled,
                "store_every": self.memory.store_every,
                "store_on_accept": self.memory.store_on_accept,
            },
            "budgets": {
                "max_tool_rounds": self.budgets.max_tool_rounds,
                "max_total_lines_read": self.budgets.max_total_lines_read,
            },
            "qa": {
                "enabled": self.qa.enabled,
                "default_tools": self.qa.default_tools,
            },
            "context": {
                "pm_tasks_max_chars": self.context.pm_tasks_max_chars,
                "known_files_max_chars": self.context.known_files_max_chars,
                "last_result_max_chars": self.context.last_result_max_chars,
                "tool_output_max_chars": self.context.tool_output_max_chars,
                "planner_output_max_chars": self.context.planner_output_max_chars,
                "ollama_output_max_chars": self.context.ollama_output_max_chars,
            },
            "build_loop": {
                "budget": self.build_loop.budget,
                "stall_round_threshold": self.build_loop.stall_round_threshold,
                "verify_requires_ready": self.build_loop.verify_requires_ready,
            },
            "io": {
                "jsonl_buffered": self.io.jsonl_buffered,
                "flush_interval_sec": self.io.flush_interval_sec,
                "flush_batch": self.io.flush_batch,
                "max_buffer": self.io.max_buffer,
            },
            "factory": {
                "default_strategy": self.factory.default_strategy,
                "auditor_failure_action": self.factory.auditor_failure_action,
                "max_fix_attempts": self.factory.max_fix_attempts,
                "require_defect_ticket": self.factory.require_defect_ticket,
                "defect_ticket_fields": self.factory.defect_ticket_fields,
                "require_evidence_run": self.factory.require_evidence_run,
                "require_fast_loop_before_evidence_run": self.factory.require_fast_loop_before_evidence_run,
                "hard_rollback_enabled": self.factory.hard_rollback_enabled,
                "hard_rollback_trigger_conditions": self.factory.hard_rollback_trigger_conditions,
                "gate_set_default": self.factory.gate_set_default,
                "allow_reduced_gate": self.factory.allow_reduced_gate,
                "enforce_hp_flow": self.factory.enforce_hp_flow,
                "required_pipeline": self.factory.required_pipeline,
                "standalone_allowed": self.factory.standalone_allowed,
                "budget_overflow_ratio": self.factory.budget_overflow_ratio,
                "forbid_missing_evidence": self.factory.forbid_missing_evidence,
            },
        }
