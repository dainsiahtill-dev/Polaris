# Director Execution

## Kind

`composite` / `facade`

## Purpose

**FACADE CELL (✅ MIGRATION COMPLETED).** Acts as the stable public contract and
backward-compatibility surface for Director task execution. Implementation has been
split into sub-Cells:

- `director.planning` — Director main loop, rules, context gathering
- `director.tasking` — Task lifecycle, worker pool, executor
- `director.runtime` — Patch/file application, existence gate, repair, tool chain
- `director.delivery` — CLI and terminal console transport

## Public Contracts

- commands: ExecuteDirectorTaskCommandV1, RetryDirectorTaskCommandV1
- queries: GetDirectorTaskStatusQueryV1
- events: DirectorTaskStartedEventV1, DirectorTaskCompletedEventV1
- results: DirectorExecutionResultV1
- errors: DirectorExecutionErrorV1

## Migration Status

| Phase | Description | Status | Date |
|-------|-------------|--------|------|
| Phase 0 | `polaris/kernelone/tools/` canonical consolidation | ✅ Complete | 2026-04-05 |
| Phase 1 | 4 sub-Cell skeletons (planning, tasking, runtime, delivery) | ✅ Complete | 2026-04-05 |
| Phase 2 | Migrate `director.planning` implementation | ✅ Complete | 2026-04-05 |
| Phase 3 | Migrate `director.tasking` implementation | ✅ Complete | 2026-04-05 |
| Phase 4 | Migrate `director.runtime` implementation | ✅ Complete | 2026-04-09 |
| Phase 5 | Migrate `director.delivery` implementation | ✅ Complete | 2026-04-09 |

> **Migration Completed**: All phases are complete as of 2026-04-09 per FULL_CONVERGENCE_AUDIT_20260405.

## Verification

- `tests/test_director_logic.py`
- `tests/test_director_service_convergence.py`
