"""Backward-compatibility re-export layer for director.execution internal modules.

.. deprecated::
    All implementation modules are being migrated to sub-Cells:
    - director.planning   → DirectorAgent, rules, context_gatherer, logic
    - director.tasking   → TaskService, WorkerPool, WorkerExecutor (Phase 3 ✅)
    - director.runtime   → PatchApplyEngine, FileApplyService, ExistenceGate, RepairService
    - director.delivery  → director_cli

    Phase 3 tasking modules (task_lifecycle_service, worker_pool_service,
    worker_executor, bootstrap_template_catalog) have been migrated to
    polaris.cells.director.tasking.internal. The stub re-exports in those
    individual module files provide backward compatibility.

    New code should import directly from the sub-Cells once migration is complete.

Consumers of this module will receive deprecation warnings until all
implementation has been migrated out of director.execution/internal/.
"""

from __future__ import annotations

import warnings

# --------------------------------------------------------------------------
# Planning group (director.planning) — Phase 2 ✅
# Direct imports (not star) to avoid triggering Phase 3 stub chain during
# importlib.import_module resolution in director.tasking.internal.worker_executor.
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Tasking group (director.tasking) — Phase 3 ✅
# Phase 3 modules migrated; stubs in execution/internal/* re-export from
# polaris.cells.director.tasking.internal for backward compatibility.
# NOTE: No star imports here — importing Phase 3 stubs from __init__ would
# create a circular dependency with director.tasking.internal.worker_executor's
# deferred Phase 4 import block.
# --------------------------------------------------------------------------
# These star imports removed (Phase 3 complete):
#   from polaris.cells.director.execution.internal.task_lifecycle_service import *
#   from polaris.cells.director.execution.internal.worker_executor import *
#   from polaris.cells.director.execution.internal.worker_pool_service import *
#   from polaris.cells.director.execution.internal.bootstrap_template_catalog import *
# --------------------------------------------------------------------------
# Runtime group (director.runtime) — Phase 4 pending
# Direct imports (not star) to avoid triggering the Phase 3 stub chain.
# --------------------------------------------------------------------------
from polaris.cells.director.execution.internal.code_generation_engine import (
    CODE_WRITING_FORBIDDEN_WARNING,
    CodeGenerationEngine,
    CodeGenerationPolicyViolationError,
    _raise_policy_violation,
)
from polaris.cells.director.execution.internal.context_gatherer import (
    GatheredContext,
    gather,
)
from polaris.cells.director.execution.internal.director_agent import (
    DirectorAgent,
    ExecutionRecord,
    QualityTracker,
    RiskRegistry,
)
from polaris.cells.director.execution.internal.director_logic_rules import (
    compact_pm_payload,
    extract_defect_ticket,
    extract_required_evidence,
    parse_acceptance,
    validate_defect_ticket,
    validate_files_to_edit,
    write_gate_check,
)
from polaris.cells.director.execution.internal.existence_gate import (
    GateResult,
    check_mode,
    is_any_missing,
    is_pure_create,
)
from polaris.cells.director.execution.internal.file_apply_service import (
    FileApplyService,
)
from polaris.cells.director.execution.internal.patch_apply_engine import (
    ApplyIntegrity,
    ApplyResult,
    apply_all_operations,
    apply_operation,
    apply_operations_strict,
    parse_all_operations,
    parse_delete_operations,
    parse_full_file_blocks,
    parse_search_replace_blocks,
    validate_before_apply,
)
from polaris.cells.director.execution.internal.repair_service import (
    RepairContext,
    RepairResult,
    RepairService,
)

# --------------------------------------------------------------------------
# Delivery group (director.delivery) — Phase 5 pending
# --------------------------------------------------------------------------
# Lazy import to avoid ImportError when director_cli is not yet migrated
try:
    from polaris.cells.director.execution.internal.director_cli import (  # type: ignore[attr-defined]
        DirectorCLI,  # type: ignore[attr-defined]
    )
except ImportError:
    pass

warnings.warn(
    "polaris.cells.director.execution.internal.* is deprecated and will be removed. "
    "Implementation has been migrated to sub-Cells: "
    "director.planning (✅ Phase 2), director.tasking (✅ Phase 3), "
    "director.runtime (Phase 4), director.delivery (Phase 5). "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = []  # Re-export all via direct imports above
