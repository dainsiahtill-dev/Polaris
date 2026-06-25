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
)
from .diagnostics import normalize_artifact_quality_errors
from .executor import TransactionalRepairExecutor
from .legacy_bridge import build_legacy_repair_kernel_summary
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate
from .receipt_context import build_repair_receipt_context
from .strategy_catalog import (
    KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS,
    DeterministicRepairStrategy,
    describe_deterministic_repair_strategy,
    deterministic_repair_source_tool_known,
    deterministic_repair_strategy_catalog,
    summarize_deterministic_repair_source_tools,
)

__all__ = [
    "KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS",
    "ComposedPatch",
    "CompositionIssue",
    "CompositionResult",
    "DeterministicRepairStrategy",
    "PatchComposer",
    "PolicyDecision",
    "RepairAdvisorNote",
    "RepairDiagnostic",
    "RepairExecutionResult",
    "RepairOperation",
    "RepairPlan",
    "RepairPolicyContext",
    "RepairPolicyGate",
    "RepairReceipt",
    "TransactionalRepairExecutor",
    "build_legacy_repair_kernel_summary",
    "build_repair_receipt_context",
    "describe_deterministic_repair_strategy",
    "deterministic_repair_source_tool_known",
    "deterministic_repair_strategy_catalog",
    "normalize_artifact_quality_errors",
    "summarize_deterministic_repair_source_tools",
]
