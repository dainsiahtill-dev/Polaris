# Role Session Desktop Detach Control Blueprint (2026-05-23)

## Scope

This blueprint covers explicit RoleSession detach control in the shared PM, Chief Engineer, and Director desktop dialogue panel.

The shared panel already creates and auto-attaches RoleSessions when PM or Director task context exists. The backend also exposes `POST /v2/roles/sessions/{session_id}/actions/detach`, but the shared desktop panel did not provide a way to break attachment intentionally.

## Current Evidence

- `polaris.delivery.http.routers.role_session` exposes:
  - `POST /v2/roles/sessions/{session_id}/actions/attach`
  - `POST /v2/roles/sessions/{session_id}/actions/detach`
- `AIDialoguePanel` is shared by PM, Chief Engineer, and Director desktop workspaces.
- PM and Director currently pass task context into the shared panel, so the hook auto-attaches RoleSessions to selected/current task context.

## Boundary

- Frontend implementation:
  - `src/frontend/src/app/components/ai-dialogue/useAIDialogue.ts`
  - `src/frontend/src/app/components/ai-dialogue/AIDialoguePanel.tsx`
  - `src/frontend/src/app/components/ai-dialogue/__tests__/AIDialoguePanel.test.tsx`
- Backend implementation: no new endpoint. This increment reuses the existing RoleSession detach route.
- Backend cells involved:
  - `roles.session`: RoleSession attachment state owner.
  - `roles.runtime`: HTTP delivery surface for RoleSession attachment routes.

## Design

```text
PM/Director workspace with selected task
  -> AIDialoguePanel
  -> RoleSession strip
  -> operator clicks detach
  -> POST /v2/roles/sessions/{id}/actions/detach
  -> active session detail refreshes
  -> auto-attach is suppressed for the same session/task key
```

If the operator creates a new RoleSession or changes task/run context, the normal auto-attach path can run again.

## UX Rules

- Detach must be explicit and only enabled when the panel has a session id and attached task/run context.
- After detach, show inline status evidence.
- Do not hide backend errors.
- Do not use detach as a client-side policy decision; backend remains authoritative.
- Use Lucide icons and compact strip controls only.

## Verification Plan

- Panel tests prove clicking detach posts to the backend route and suppresses immediate reattach for the same session/task key.
- Existing tests continue to cover create, attach, active detail, capabilities, evidence, resume, stream, and workflow export.
- Frontend lint, targeted Vitest, typecheck, and build cover the changed TypeScript surface.
- Backend RoleSession route tests remain unchanged because no backend contract changed.
