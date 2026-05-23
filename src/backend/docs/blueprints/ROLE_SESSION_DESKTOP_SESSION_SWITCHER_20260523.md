# Role Session Desktop Session Switcher Blueprint (2026-05-23)

## Scope

This blueprint covers desktop RoleSession continuity for PM, Chief Engineer, and Director dialogue panels.

The shared AI dialogue panel can create RoleSessions, attach them to workflow context, stream through them, and export PM/Director sessions to workflows. This increment adds a compact session switcher so users can recover a prior RoleSession without leaving the role workspace.

## Current Evidence

- `POST /v2/roles/sessions` creates persisted role sessions.
- `GET /v2/roles/sessions` lists persisted role sessions by role, host kind, workspace, and state filters.
- `GET /v2/roles/sessions/{session_id}/messages` returns persisted messages for a session.
- `AIDialoguePanel` is shared across PM, Chief Engineer, and Director desktop workspaces.
- Before this increment, the shared desktop panel could not show or resume existing RoleSessions.

## Boundary

- Frontend implementation:
  - `src/frontend/src/app/components/ai-dialogue/useAIDialogue.ts`
  - `src/frontend/src/app/components/ai-dialogue/AIDialoguePanel.tsx`
- Backend implementation: no new endpoint. This increment reuses existing RoleSession list and message routes.
- Backend cells involved:
  - `roles.session`: persisted session and message ownership.
  - `roles.runtime`: HTTP delivery surface for RoleSession routes.

## Design

```text
PM/Chief Engineer/Director workspace
  -> AIDialoguePanel
  -> RoleSession strip
  -> operator opens session list
  -> GET /v2/roles/sessions?role={role}&host_kind={host_kind}&workspace={workspace}
  -> operator selects a session
  -> GET /v2/roles/sessions/{id}/messages
  -> active session id and visible transcript are restored
```

The switcher is intentionally local to the dialogue panel. It is not a global run browser, task board, or artifact explorer.

## UX Rules

- Keep the list compact and attached to the existing RoleSession strip.
- Do not auto-switch sessions. The operator must select one.
- If message recovery fails, keep the selected session id visible and show an inline error system message.
- Existing task attachment behavior must re-run after switching sessions so the restored RoleSession has current desktop context.
- Use Lucide icons only.

## Verification Plan

- Panel tests prove the list endpoint is called, a prior session can be selected, and persisted messages appear in the transcript.
- Existing hook tests continue to prove RoleSession creation, task attachment, streaming, and legacy fallback.
- Frontend lint, targeted Vitest, typecheck, and build cover the changed TypeScript surface.
