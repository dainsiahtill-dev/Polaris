# Director Execution

## Kind

`composite` / `facade`

## Purpose

**FACADE CELL (⚠️ MIGRATION IN PROGRESS — NOT COMPLETED).** Acts as the stable public
contract for Director task execution. It must not regain compatibility
re-exports for implementation modules that already moved to sub-Cells.

- `director.planning` — Director rules live in `director.planning`; only the
  remaining execution agent/context implementation still lives in
  `execution/internal/`.
- `director.tasking` — Task lifecycle, worker pool, executor (implementation present)
- `director.runtime` — Patch/file application, existence gate, repair, tool chain
  (repair kernel implementation present; tasking still owns some file apply
  helpers during cutover)
- `director.delivery` — CLI and terminal console transport through
  `polaris/delivery/cli/director/cli_thin.py`

## Public Contracts

- commands: ExecuteDirectorTaskCommandV1, RetryDirectorTaskCommandV1
- queries: GetDirectorTaskStatusQueryV1
- events: DirectorTaskStartedEventV1, DirectorTaskCompletedEventV1
- results: DirectorExecutionResultV1
- errors: DirectorExecutionErrorV1
- services: `execute_director_task(ExecuteDirectorTaskCommandV1) -> DirectorExecutionResultV1`
  is the stable public facade used by `roles.runtime`; it delegates to the
  current DirectorService implementation and does not move state ownership into
  `roles.runtime`.

## Migration Status

| Phase | Description | Status | Date |
|-------|-------------|--------|------|
| Phase 0 | `polaris/kernelone/tools/` canonical consolidation | ✅ Complete | 2026-04-05 |
| Phase 1 | 4 sub-Cell skeletons (planning, tasking, runtime, delivery) | ✅ Complete | 2026-04-05 |
| Phase 2 | Migrate `director.planning` implementation | ⚠️ Partial (`director_logic_rules` moved; execution agent/context remain) | 2026-06-30 |
| Phase 3 | Migrate `director.tasking` implementation | ✅ Implementation present | 2026-04-05 |
| Phase 4 | Migrate `director.runtime` implementation | ⚠️ Partial (repair kernel present; some file helpers still tasking-owned) | 2026-06-30 |
| Phase 5 | Migrate `director.delivery` implementation | ⚠️ Thin entrypoint present (`cli_thin.py`) | 2026-06-30 |

> **Migration NOT complete (2026-06-30 audit)**: `execution/internal/` now only
> retains remaining execution implementation modules. Do not restore deleted
> compatibility shims for tasking/planning/runtime/delivery symbols.

## Verification

- `tests/test_director_logic.py`
- `tests/test_director_service_convergence.py`
