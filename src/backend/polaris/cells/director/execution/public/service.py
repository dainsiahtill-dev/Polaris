"""Public service exports for `director.execution` cell.

Backward-compatible facade. Implementation migrated to sub-Cells:
- director.planning  → already migrated (Phase 2)
- director.tasking   → TaskQueueConfig, TaskService, WorkerPoolConfig, WorkerService (Phase 3)
- director.runtime   → PatchApplyEngine, FileApplyService, ExistenceGate (Phase 4, pending)
- director.delivery  → director_cli (Phase 5, pending)
"""

from __future__ import annotations

from polaris.cells.director.execution.internal.director_agent import DirectorAgent
from polaris.cells.director.execution.internal.patch_apply_engine import (
    ApplyIntegrity,
    EditType,
    parse_all_operations,
    parse_full_file_blocks,
    parse_search_replace_blocks,
    validate_before_apply,
)
from polaris.cells.director.execution.logic import extract_defect_ticket, parse_acceptance, write_gate_check
from polaris.cells.director.execution.service import DirectorConfig, DirectorService, DirectorState
from polaris.cells.director.tasking.public import (
    TaskQueueConfig,
    TaskService,
    WorkerPoolConfig,
    WorkerService,
)

__all__ = [
    "ApplyIntegrity",
    "DirectorAgent",
    "DirectorConfig",
    "DirectorService",
    "DirectorState",
    "EditType",
    "TaskQueueConfig",
    "TaskService",
    "WorkerPoolConfig",
    "WorkerService",
    "extract_defect_ticket",
    "parse_acceptance",
    "parse_all_operations",
    "parse_full_file_blocks",
    "parse_search_replace_blocks",
    "validate_before_apply",
    "write_gate_check",
]
