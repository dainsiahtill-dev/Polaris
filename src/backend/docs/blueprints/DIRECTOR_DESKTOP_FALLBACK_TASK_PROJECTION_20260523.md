# Director Desktop Fallback Task Projection

Date: 2026-05-23

## Problem

Director desktop views consumed `/v2/director/tasks` from multiple layers with duplicated source selection:

- main Director workspace
- extracted Director workspace hook
- runtime Director workspace VM

This made `auto` / `workflow` / `local` fallback handling drift across surfaces and weakened the audit trail for `metadata.director_task_source`.

## Contract

`listDirectorTaskFallbackRows(directorRunning)` is the shared desktop-facing projection.

- idle Director: read `auto`, then `local`
- running Director: read `workflow`, then `local`
- invalid rows without an id are discarded
- duplicate task ids are merged by last source read
- each row is stamped with `metadata.director_task_source`

Runtime push rows remain the owner of volatile execution state. Fallback rows fill missing task-contract fields such as PM task id, blueprint refs, execution steps, target files, and acceptance criteria.

## Verification

- `src/frontend/src/services/__tests__/pmService.test.ts`
- `src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`
