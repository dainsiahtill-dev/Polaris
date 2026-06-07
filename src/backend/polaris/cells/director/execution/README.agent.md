# Director Execution

## Kind

`composite` / `facade`

## Purpose

**FACADE CELL (⚠️ MIGRATION IN PROGRESS — NOT COMPLETED).** Acts as the stable public
contract and backward-compatibility surface for Director task execution. The full
implementation still lives in `execution/internal/`; the split into sub-Cells is only
partially done:

- `director.planning` — Director main loop, rules, context gathering (COPIED, not migrated;
  `director_agent.py` / `context_gatherer.py` / `director_logic_rules.py` still remain in
  `execution/internal/`)
- `director.tasking` — Task lifecycle, worker pool, executor (implementation present)
- `director.runtime` — Patch/file application, existence gate, repair, tool chain
  (SKELETON ONLY — `runtime/internal/` contains only `__init__.py`)
- `director.delivery` — CLI and terminal console transport (SKELETON ONLY —
  `delivery/cli/director/` has no `director_cli.py`)

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
| Phase 2 | Migrate `director.planning` implementation | ⚠️ Copied, not migrated (source still in `execution/internal/`) | 2026-04-05 |
| Phase 3 | Migrate `director.tasking` implementation | ✅ Implementation present | 2026-04-05 |
| Phase 4 | Migrate `director.runtime` implementation | ❌ Not done (`runtime/internal/` is empty skeleton) | — |
| Phase 5 | Migrate `director.delivery` implementation | ❌ Not done (`delivery/cli/director/` has no `director_cli.py`) | — |

> **Migration NOT complete (2026-06-07 audit)**: Phases 4/5 are not done and Phase 2 was a
> copy rather than a migration; the full implementation still resides in `execution/internal/`.

## Verification

- `tests/test_director_logic.py`
- `tests/test_director_service_convergence.py`
