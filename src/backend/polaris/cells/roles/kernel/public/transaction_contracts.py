"""Transaction contracts - public boundary for transaction-related kernel internals.

This module exposes Phase management, intent classification, verification patterns,
and CognitiveGateway from roles.kernel.internal.transaction for use by other Cells
(especially roles.runtime), following the Public/Internal Fence principle.

Public exports:
- Phase, PhaseManager, ToolResult: phase state machine
- extract_tool_results_from_batch_receipt: batch receipt parsing
- resolve_delivery_mode: intent-driven delivery contract resolution
- VERIFICATION_TOOLS, get_verification_patterns: evidence-based verification
- CognitiveGateway: unified cognitive gateway (async interface)
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
    CognitiveGateway,
)
from polaris.cells.roles.kernel.internal.transaction.constants import (
    VERIFICATION_TOOLS,
    get_verification_patterns,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    resolve_delivery_mode,
)
from polaris.cells.roles.kernel.internal.transaction.phase_manager import (
    Phase,
    PhaseManager,
    ToolResult,
    extract_tool_results_from_batch_receipt,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract import (
    ModificationIntent,
    ReadinessVerdict,
    TaskContract,
    TaskContractStatus,
    evaluate_readiness,
)

__all__ = [
    "VERIFICATION_TOOLS",
    "CognitiveGateway",
    "ModificationIntent",
    "Phase",
    "PhaseManager",
    "ReadinessVerdict",
    "TaskContract",
    "TaskContractStatus",
    "ToolResult",
    "evaluate_readiness",
    "extract_tool_results_from_batch_receipt",
    "get_verification_patterns",
    "resolve_delivery_mode",
]
