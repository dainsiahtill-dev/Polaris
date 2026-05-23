# Role Session Workflow Export Desktop Control Blueprint (2026-05-23)

## Scope

This blueprint covers the desktop control that exports an active RoleSession into an existing workflow target.

The previous increment made RoleSession state visible in the shared PM, Chief Engineer, and Director dialogue panel. This increment adds an explicit operator action for PM and Director so a persisted dialogue session can be handed off to the backend workflow export endpoint without duplicating backend orchestration code.

## Current Evidence

- `polaris.delivery.http.routers.role_session` owns `POST /v2/roles/sessions/{session_id}/actions/export-to-workflow`.
- The endpoint accepts `target` values `pm`, `director`, or `factory`.
- PM and Director legacy workbench panels already call the endpoint directly.
- The shared desktop `AIDialoguePanel` is now the common dialogue surface used by PM, Chief Engineer, and Director workspaces.
- Chief Engineer has no dedicated `export-to-workflow` target in the current backend contract.

## Boundary

- Frontend implementation:
  - `src/frontend/src/app/components/ai-dialogue/AIDialoguePanel.tsx`
  - `src/frontend/src/app/components/ai-dialogue/useAIDialogue.ts`
  - `src/frontend/src/app/components/pm/PMAIDialoguePanel.tsx`
  - `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- Backend implementation: no new endpoint. This increment reuses the existing RoleSession workflow export route.
- Backend cells involved:
  - `roles.session`: RoleSession lifecycle, artifacts, audit, and export bundle ownership.
  - `roles.runtime`: delivery router ownership for role-session HTTP APIs.
  - `orchestration.pm_dispatch`: PM and Director run creation from exported session bundles.

## Design

```text
PM/Director workspace
  -> AIDialoguePanel
  -> RoleSession strip
  -> operator clicks export
  -> useAIDialogue.handleExportToWorkflow
  -> POST /v2/roles/sessions/{id}/actions/export-to-workflow
       { target, export_kind: "session_bundle", include_audit_log: true }
  -> strip displays resulting workflow run id or export error
```

Chief Engineer remains read-only for this action until the backend exposes a specific Chief Engineer workflow target or an approved handoff target contract.

## UX Rules

- Export must be explicit. Chat messages, session creation, and task attachment must not start workflows automatically.
- The export control is hidden unless the caller passes a `workflowExportTarget`.
- The control is disabled until a RoleSession id exists.
- The result must be visible inline as workflow run evidence.
- Use Lucide icons and compact operational controls consistent with the existing desktop strip.

## Verification Plan

- Panel tests prove the export button posts the correct target and renders the returned run id.
- Existing hook tests continue to prove RoleSession creation, attachment, streaming, and legacy fallback behavior.
- Frontend lint, targeted Vitest, typecheck, and build cover the changed TypeScript surface.
- Backend role-session route tests remain unchanged because no backend contract changed.
