# Director Tasking

## Kind

`workflow`

## Purpose

Task lifecycle management, worker pool orchestration, and worker executor for Director tasks.
Owns TaskService, WorkerService, WorkerExecutor, and bootstrap template catalog.

## Migration Status

✅ **MIGRATION COMPLETED** (2026-04-09)

Implementation migrated from `polaris/cells/director/execution/internal/` (Phase 3, 2026-03-23).
All remaining work completed per FULL_CONVERGENCE_AUDIT_20260405.

### Implementation Files (Phase 3 Complete)

| File | Lines | Phase 4 Deps |
|------|-------|-------------|
| `bootstrap_template_catalog.py` | 479 | None |
| `worker_executor.py` | ~900 | CodeGenerationEngine, FileApplyService |
| `worker_pool_service.py` | ~350 | WorkerExecutor |
| `task_lifecycle_service.py` | ~1365 | RepairService |

### Phase 4 Deps (Deferred via Lazy Import)

- `CodeGenerationEngine` — Phase 4: `director.runtime`
- `FileApplyService` — Phase 4: `director.runtime`
- `RepairService` / `RepairContext` — Phase 4: `director.runtime`

### All Work Completed

1. ✅ Add director.tasking integration tests
2. ✅ Cutover Facade (`director.execution`) to import from `director.tasking.public`
3. ✅ Update `director.execution/internal` star re-exports
