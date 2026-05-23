# Role Session Desktop Capability Visibility Blueprint (2026-05-23)

## Scope

This blueprint covers role capability visibility in the shared PM, Chief Engineer, and Director desktop dialogue panel.

The backend already exposes role capabilities through `GET /v2/roles/capabilities/{role}`. A separate `SessionInspector` uses that route, but the shared desktop `AIDialoguePanel` does not. This increment adds a compact capability chip to the RoleSession strip so operators can see whether the current role/host capability matrix was loaded.

## Current Evidence

- `polaris.delivery.http.routers.role_session` exposes `GET /v2/roles/capabilities/{role}?host_kind=...`.
- `polaris.domain.entities.capability.get_role_capabilities` owns the role/host capability matrix.
- `AIDialoguePanel` is shared by PM, Chief Engineer, and Director desktop workspaces.
- Before this increment, the shared desktop panel could send capability profiles on session creation but did not display the active backend capability matrix.

## Boundary

- Frontend implementation:
  - `src/frontend/src/app/components/ai-dialogue/useAIDialogue.ts`
  - `src/frontend/src/app/components/ai-dialogue/AIDialoguePanel.tsx`
  - `src/frontend/src/app/components/ai-dialogue/__tests__/AIDialoguePanel.test.tsx`
- Backend implementation: no new endpoint. This increment reuses the existing role capabilities route.
- Backend cells involved:
  - `policy.permission` / domain capability entity: role capability truth.
  - `roles.runtime`: HTTP delivery surface for role/session routes.

## Design

```text
PM/Chief Engineer/Director workspace
  -> AIDialoguePanel
  -> useAIDialogue
  -> GET /v2/roles/capabilities/{role}?host_kind={host_kind}
  -> RoleSession strip capability chip
       - loading state
       - capability count
       - error state
       - tooltip with compact capability list
```

This is display-only. It does not authorize actions client-side and does not mutate role capabilities.

## UX Rules

- Keep the chip compact and stable.
- Do not use the chip as a security decision. Backend policy remains authoritative.
- Show unavailable/error state rather than inventing capabilities.
- Use Lucide icons only.

## Verification Plan

- Panel tests prove the capabilities route is called and the loaded count appears in the RoleSession strip.
- Existing RoleSession tests continue to cover session creation, attachment, evidence, resume, stream, and export.
- Frontend lint, targeted Vitest, typecheck, and build cover the changed TypeScript surface.
- Existing backend capability route tests remain unchanged because no backend contract changed.
