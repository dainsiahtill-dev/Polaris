# Role Session Desktop Evidence Panel Blueprint (2026-05-23)

## Scope

This blueprint covers a shared desktop evidence panel for PM, Chief Engineer, and Director RoleSession dialogue.

The shared AI dialogue panel already creates, attaches, streams, resumes, and exports RoleSessions. The backend also exposes persisted RoleSession artifacts and audit logs, but those evidence routes are not visible in the shared desktop role panels. This increment adds a compact read-only evidence panel backed by the existing RoleSession HTTP routes.

## Current Evidence

- `polaris.delivery.http.routers.role_session` exposes:
  - `GET /v2/roles/sessions/{session_id}/artifacts`
  - `GET /v2/roles/sessions/{session_id}/audit`
- `polaris.tests.unit.delivery.http.routers.test_role_session_v2` already verifies artifacts and audit route behavior.
- `AIDialoguePanel` is shared by PM, Chief Engineer, and Director desktop workspaces.
- Before this increment, the shared panel did not expose artifacts or audit events, so operators could not inspect session evidence without leaving the role workspace.

## Boundary

- Frontend implementation:
  - `src/frontend/src/app/components/ai-dialogue/useAIDialogue.ts`
  - `src/frontend/src/app/components/ai-dialogue/AIDialoguePanel.tsx`
  - `src/frontend/src/app/components/ai-dialogue/__tests__/AIDialoguePanel.test.tsx`
- Backend implementation: no new endpoint. This increment reuses existing RoleSession artifacts and audit routes.
- Backend cells involved:
  - `roles.session`: RoleSession artifact and audit ownership.
  - `roles.runtime`: HTTP delivery surface for RoleSession routes.

## Design

```text
PM/Chief Engineer/Director workspace
  -> AIDialoguePanel
  -> RoleSession strip
  -> operator opens evidence panel
  -> GET /v2/roles/sessions/{id}/artifacts
  -> GET /v2/roles/sessions/{id}/audit?limit=20&offset=0
  -> compact read-only evidence panel
       - artifact count and recent artifact rows
       - audit event count and recent audit rows
       - refresh action
```

The panel does not mutate sessions, artifacts, audit logs, task contracts, or workflow state.

## UX Rules

- Evidence loading must be explicit and refreshable.
- The control is disabled until a RoleSession id exists.
- Empty and error states must be visible.
- Use Lucide icons only and keep the panel dense enough for the PM/CE/Director right-side workbench width.
- Display raw identifiers and compact payload summaries without pretending to understand artifact semantics.

## Verification Plan

- Panel tests prove the evidence button calls both backend endpoints and renders artifact/audit evidence.
- Existing RoleSession tests continue to cover create, attach, resume, stream, export, and new-session behavior.
- Frontend lint, targeted Vitest, typecheck, and build cover the changed TypeScript surface.
- Backend RoleSession router tests remain unchanged because no backend contract changed.
