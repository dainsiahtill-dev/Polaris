# Chief Engineer Desktop Director Task Fallback

Date: 2026-05-23

## Finding

The Chief Engineer desktop workspace already uses `/v2/director/workers` as a
backend fallback when realtime Director worker heartbeats are absent. Its
Director task lifecycle metrics, however, were derived only from the
parent-provided task rows. When the desktop snapshot did not carry Director
tasks, the CE workspace could show zero Director task evidence even though
`/v2/director/tasks` had auditable backend rows.

## Contract

Chief Engineer desktop must use Director task backend rows as fallback evidence:

- Primary live input: `tasks` prop from the desktop runtime snapshot.
- Backend fallback: `listDirectorTaskFallbackRows(directorRunning)`, which reads
  `/v2/director/tasks` from the appropriate workflow/local sources.
- Merge key: task `id`, with live prop rows taking precedence over backend
  fallback rows.

The fallback powers Director task lifecycle metrics and the dialogue context
task count; it does not create CE blueprint-generation candidates from backend
Director rows.

## Verification

- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`
- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx`
- Frontend typecheck and focused Vitest suite.
