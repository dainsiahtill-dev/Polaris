"""Director Repair Kernel.

This package provides the structured, auditable substrate for Director
deterministic repairs. The current production repair functions remain in
``deterministic_repairs``; this kernel supplies contracts, composition,
policy, receipt, and shadow-mode primitives that can wrap the legacy path
without changing its behavior.
"""

from __future__ import annotations

from .composer import PatchComposer
from .contracts import (
    ComposedPatch,
    CompositionIssue,
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairExecutionResult,
    RepairOperation,
    RepairPlan,
    RepairReceipt,
    RepairRevalidationEvidence,
)
from .diagnostics import normalize_artifact_quality_errors
from .executor import TransactionalRepairExecutor
from .legacy_bridge import build_legacy_repair_kernel_summary
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate
from .receipt_context import build_repair_receipt_context
from .receipts import attach_revalidation_evidence
from .registry import (
    RepairArchetype,
    RepairCoverageReport,
    RepairDiagnosticCoverage,
    RepairLanguageSlot,
    RepairRuleDefinition,
    RepairRuleRegistry,
    build_repair_coverage_report,
    default_repair_rule_registry,
    repair_language_slots,
)
from .schedule_catalog import PostExecutionRepairScheduleStep, post_execution_repair_schedule
from .scheduler import (
    BaseFilesProviderFn,
    PlannerFn,
    RepairConvergenceResult,
    RepairConvergenceScheduler,
    RepairPlanSchedule,
    RepairRoundResult,
    RepairVerifierSnapshot,
    VerifierFn,
    order_repair_plans,
)
from .shadow import RepairShadowComparison, compare_legacy_and_kernel_repairs
from .strategy_catalog import (
    KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS,
    DeterministicRepairStrategy,
    describe_deterministic_repair_strategy,
    deterministic_repair_source_tool_known,
    deterministic_repair_strategy_catalog,
    summarize_deterministic_repair_source_tools,
)
from .typescript_syntax import (
    TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
    build_typescript_object_literal_comma_plan,
    repair_typescript_object_literal_commas,
)

__all__ = [
    "KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS",
    "TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL",
    "BaseFilesProviderFn",
    "ComposedPatch",
    "CompositionIssue",
    "CompositionResult",
    "DeterministicRepairStrategy",
    "PatchComposer",
    "PlannerFn",
    "PolicyDecision",
    "PostExecutionRepairScheduleStep",
    "RepairAdvisorNote",
    "RepairArchetype",
    "RepairConvergenceResult",
    "RepairConvergenceScheduler",
    "RepairCoverageReport",
    "RepairDiagnostic",
    "RepairDiagnosticCoverage",
    "RepairExecutionResult",
    "RepairLanguageSlot",
    "RepairOperation",
    "RepairPlan",
    "RepairPlanSchedule",
    "RepairPolicyContext",
    "RepairPolicyGate",
    "RepairReceipt",
    "RepairRevalidationEvidence",
    "RepairRoundResult",
    "RepairRuleDefinition",
    "RepairRuleRegistry",
    "RepairShadowComparison",
    "RepairVerifierSnapshot",
    "TransactionalRepairExecutor",
    "VerifierFn",
    "attach_revalidation_evidence",
    "build_legacy_repair_kernel_summary",
    "build_repair_coverage_report",
    "build_repair_receipt_context",
    "build_typescript_object_literal_comma_plan",
    "compare_legacy_and_kernel_repairs",
    "default_repair_rule_registry",
    "describe_deterministic_repair_strategy",
    "deterministic_repair_source_tool_known",
    "deterministic_repair_strategy_catalog",
    "normalize_artifact_quality_errors",
    "order_repair_plans",
    "post_execution_repair_schedule",
    "repair_language_slots",
    "repair_typescript_object_literal_commas",
    "summarize_deterministic_repair_source_tools",
]
