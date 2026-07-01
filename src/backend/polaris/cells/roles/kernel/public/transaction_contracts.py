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

from typing import TYPE_CHECKING

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
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.modification_contract import (
    ModificationContract,
    ModificationContractStatus,
    ModificationIntent,
    ReadinessVerdict,
    evaluate_modification_readiness,
)
from polaris.cells.roles.kernel.internal.transaction.phase_manager import (
    Phase,
    PhaseManager,
    ToolResult,
    extract_tool_results_from_batch_receipt,
    has_authoritative_write_receipt,
)
from polaris.cells.roles.kernel.internal.transaction.slm_coprocessor import (
    SLMCoprocessor,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_continuation_prompt_metadata,
)

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
    from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest


def create_role_transaction_kernel(
    kernel: RoleExecutionKernel,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
) -> TransactionKernel:
    """Create a kernel-backed TransactionKernel through the public Cell boundary.

    roles.runtime must not import roles.kernel internal modules directly. This
    wrapper keeps the construction logic owned by roles.kernel while giving
    orchestration layers a stable public contract.
    """
    from polaris.cells.roles.kernel.internal.kernel.transaction_factory import (
        create_transaction_kernel,
    )

    return create_transaction_kernel(kernel, role, profile, request)


__all__ = [
    "VERIFICATION_TOOLS",
    "CognitiveGateway",
    "ModificationContract",
    "ModificationContractStatus",
    "ModificationIntent",
    "Phase",
    "PhaseManager",
    "ReadinessVerdict",
    "SLMCoprocessor",
    "ToolResult",
    "TransactionConfig",
    "create_role_transaction_kernel",
    "evaluate_modification_readiness",
    "extract_continuation_prompt_metadata",
    "extract_tool_results_from_batch_receipt",
    "get_verification_patterns",
    "has_authoritative_write_receipt",
    "resolve_delivery_mode",
]
