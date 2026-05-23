# Role Session Desktop Active Detail Blueprint (2026-05-23)

## Scope

This blueprint covers active RoleSession metadata visibility in the shared PM, Chief Engineer, and Director desktop dialogue panel.

The shared panel can create, resume, attach, stream, inspect evidence, show capabilities, and export RoleSessions. The backend also exposes `GET /v2/roles/sessions/{session_id}` for authoritative session metadata, but the shared desktop panel did not display that current-session detail.

## Current Evidence

- `polaris.delivery.http.routers.role_session` exposes `GET /v2/roles/sessions/{session_id}`.
- Existing backend tests verify single-session detail behavior.
- `AIDialoguePanel` is shared by PM, Chief Engineer, and Director desktop workspaces.
- Before this increment, the panel showed only the local active session id and could drift from backend state fields such as `state`, `message_count`, `host_kind`, or `attachment_mode`.

## Boundary

- Frontend implementation:
  - `src/frontend/src/app/components/ai-dialogue/useAIDialogue.ts`
  - `src/frontend/src/app/components/ai-dialogue/AIDialoguePanel.tsx`
  - `src/frontend/src/app/components/ai-dialogue/__tests__/AIDialoguePanel.test.tsx`
- Backend implementation: no new endpoint. This increment reuses the existing RoleSession detail route.
- Backend cells involved:
  - `roles.session`: RoleSession metadata owner.
  - `roles.runtime`: HTTP delivery surface for RoleSession routes.

## Design

```text
PM/Chief Engineer/Director workspace
  -> AIDialoguePanel
  -> useAIDialogue observes active session id
  -> GET /v2/roles/sessions/{id}
  -> RoleSession strip metadata chip
       - backend state
       - message count
       - tooltip with title/host/attachment mode
```

This is display-only. The backend remains authoritative for session state and policy.

## UX Rules

- Keep metadata compact and stable inside the existing strip.
- If detail loading fails, show a small unavailable chip instead of inventing state.
- Do not use metadata display as a client-side security decision.
- Use Lucide icons only and avoid adding another large panel.

## Verification Plan

- Panel tests prove the detail route is called and backend state/message count appear in the strip.
- Existing RoleSession tests continue to cover create, attach, resume, evidence, capabilities, stream, and export.
- Frontend lint, targeted Vitest, typecheck, and build cover the changed TypeScript surface.
- Backend RoleSession route tests remain unchanged because no backend contract changed.
