"""Public surface for director.tasking cell.

External consumers (Facade, other Cells) import from this module.
Contains both public contracts (commands, queries, results, errors) and
the public service/API classes owned by this cell.
"""

from __future__ import annotations

from polaris.cells.director.tasking.internal import (
    TaskQueueConfig,
    TaskService,
    WorkerPoolConfig,
    WorkerService,
)
from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
    get_generic_bootstrap_files,
    get_intelligent_bootstrap_files,
    get_python_bootstrap_files,
    get_typescript_bootstrap_files,
)
from polaris.cells.director.tasking.internal.existence_gate import (
    ExecutionMode,
    GateResult,
    check_mode,
    is_any_missing,
    is_pure_create,
)
from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService
from polaris.cells.director.tasking.internal.patch_apply_engine import (
    ApplyIntegrity,
    ApplyResult,
    EditType,
    apply_all_operations,
    apply_operation,
    apply_operations_strict,
    parse_all_operations,
    parse_delete_operations,
    parse_full_file_blocks,
    parse_search_replace_blocks,
    validate_before_apply,
)
from polaris.cells.director.tasking.internal.repair_service import (
    RepairContext,
    RepairResult,
    RepairService,
)
from polaris.cells.director.tasking.internal.task_lifecycle_service import (
    TaskLifecycleService,
    TaskServiceDeps,
)
from polaris.cells.director.tasking.internal.worker_executor import (
    CodeGenerationResult,
    WorkerExecutor,
)
from polaris.cells.director.tasking.public.contracts import (
    CancelTaskCommandV1,
    CreateTaskCommandV1,
    DirectorTaskingError,
    TaskCreatedResultV1,
    TaskResultQueryV1,
    TaskResultResultV1,
    TaskStatusQueryV1,
    TaskStatusResultV1,
)

__all__ = [
    # Patch apply
    "ApplyIntegrity",
    "ApplyResult",
    # Contracts
    "CancelTaskCommandV1",
    # Worker
    "CodeGenerationResult",
    "CreateTaskCommandV1",
    "DirectorTaskingError",
    "EditType",
    # Existence gate
    "ExecutionMode",
    # File apply
    "FileApplyService",
    "GateResult",
    # Repair
    "RepairContext",
    "RepairResult",
    "RepairService",
    "TaskCreatedResultV1",
    # Task lifecycle
    "TaskLifecycleService",
    # Services
    "TaskQueueConfig",
    "TaskResultQueryV1",
    "TaskResultResultV1",
    "TaskService",
    "TaskServiceDeps",
    "TaskStatusQueryV1",
    "TaskStatusResultV1",
    "WorkerExecutor",
    "WorkerPoolConfig",
    "WorkerService",
    "apply_all_operations",
    "apply_operation",
    "apply_operations_strict",
    "check_mode",
    # Bootstrap catalog
    "get_generic_bootstrap_files",
    "get_intelligent_bootstrap_files",
    "get_python_bootstrap_files",
    "get_typescript_bootstrap_files",
    "is_any_missing",
    "is_pure_create",
    "parse_all_operations",
    "parse_delete_operations",
    "parse_full_file_blocks",
    "parse_search_replace_blocks",
    "validate_before_apply",
]
