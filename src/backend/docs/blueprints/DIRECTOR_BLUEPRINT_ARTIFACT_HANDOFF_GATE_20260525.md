# Director Blueprint Artifact Handoff Gate - 2026-05-25

## Problem

Director readiness diagnostics treated a workflow task as executable when its
metadata contained a `blueprint_id`, `blueprint_path`, or
`runtime_blueprint_path` string. That proved only that a label was present, not
that Chief Engineer had produced a readable blueprint artifact for the current
task. A stale or fabricated metadata value could therefore make Director look
ready even when PM/Chief Engineer handoff evidence was missing.

## Design

Director diagnostics remain side-effect-free and continue to read workflow
projection rows first, then local queue rows as a fallback.

For workflow rows only, readiness now requires:

1. A blueprint identifier or blueprint path is present.
2. The referenced Chief Engineer artifact is loadable from the public
   blueprint persistence boundary or from the runtime artifact path.
3. The artifact is a JSON object, is not marked as a hard failure, and can be
   matched to the current task through `task_id`, `pm_task_id`,
   `task_update_map`, or `task_updates`.

Local manually created Director tasks keep their current behavior because they
are an explicit local queue path, not a PM/Chief Engineer workflow handoff.

## User Experience

The Director desktop readiness strip should distinguish:

- `missing BP`: no blueprint reference exists.
- `invalid BP`: a reference exists but the artifact is missing, unreadable,
  failed, or belongs to another task.

The primary Director execute control must stay disabled when invalid blueprint
artifacts are present, with the backend blocker visible in the title and
diagnostic strip.

## Verification Plan

- Add router tests proving valid persisted blueprint payloads unlock workflow
  tasks.
- Add router tests proving stale/missing/mismatched blueprint references block
  Director execution.
- Add frontend service and workspace tests for the new invalid blueprint
  diagnostics fields and blocker copy.
- Run targeted Ruff, Mypy, Pytest, Vitest, typecheck, lint, and diff checks.
