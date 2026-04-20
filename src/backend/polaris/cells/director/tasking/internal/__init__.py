"""Internal implementation for director.tasking cell.

Migrated from ``polaris.cells.director.execution.internal`` (Phase 3).

Public contracts are defined in ``polaris.cells.director.tasking.public.contracts``.
"""

from __future__ import annotations

from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
    get_generic_bootstrap_files as get_generic_bootstrap_files,
    get_intelligent_bootstrap_files as get_intelligent_bootstrap_files,
    get_python_bootstrap_files as get_python_bootstrap_files,
    get_typescript_bootstrap_files as get_typescript_bootstrap_files,
)
from polaris.cells.director.tasking.internal.file_apply_service import (
    FileApplyService as FileApplyService,
)
from polaris.cells.director.tasking.internal.patch_apply_engine import (
    ApplyIntegrity as ApplyIntegrity,
    ApplyResult as ApplyResult,
    apply_all_operations as apply_all_operations,
    apply_operation as apply_operation,
    apply_operations_strict as apply_operations_strict,
    parse_all_operations as parse_all_operations,
    parse_delete_operations as parse_delete_operations,
    parse_full_file_blocks as parse_full_file_blocks,
    parse_search_replace_blocks as parse_search_replace_blocks,
    validate_before_apply as validate_before_apply,
)
from polaris.cells.director.tasking.internal.repair_service import (
    RepairContext as RepairContext,
    RepairResult as RepairResult,
    RepairService as RepairService,
)
from polaris.cells.director.tasking.internal.task_lifecycle_service import (
    TaskQueueConfig as TaskQueueConfig,
    TaskService as TaskService,
    TaskServiceDeps as TaskServiceDeps,
)
from polaris.cells.director.tasking.internal.worker_executor import (
    CodeGenerationResult as CodeGenerationResult,
    WorkerExecutor as WorkerExecutor,
)
from polaris.cells.director.tasking.internal.worker_pool_service import (
    WorkerPoolConfig as WorkerPoolConfig,
    WorkerService as WorkerService,
)

__all__ = [
    # Task lifecycle
    "TaskQueueConfig",
    "TaskService",
    "TaskServiceDeps",
    # Worker
    "CodeGenerationResult",
    "WorkerExecutor",
    "WorkerPoolConfig",
    "WorkerService",
    # Bootstrap catalog
    "get_generic_bootstrap_files",
    "get_intelligent_bootstrap_files",
    "get_python_bootstrap_files",
    "get_typescript_bootstrap_files",
    # File apply
    "FileApplyService",
    # Patch apply
    "ApplyIntegrity",
    "ApplyResult",
    "apply_all_operations",
    "apply_operation",
    "apply_operations_strict",
    "parse_all_operations",
    "parse_delete_operations",
    "parse_full_file_blocks",
    "parse_search_replace_blocks",
    "validate_before_apply",
    # Repair
    "RepairContext",
    "RepairResult",
    "RepairService",
]
