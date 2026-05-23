# RoleSession Desktop Service Contract

Date: 2026-05-23

## Problem

PM, Chief Engineer, and Director desktop dialogue panels share `AIDialoguePanel`, but the hook embedded RoleSession request construction and response parsing directly. That made the desktop bridge harder to audit because capabilities, session creation, attachment, listing, detail loading, and message restoration were all interpreted inside UI state code.

## Contract

`src/frontend/src/services/roleSessionService.ts` is the typed frontend boundary for the stable backend RoleSession API:

- `GET /v2/roles/capabilities/{role}`
- `POST /v2/roles/sessions`
- `GET /v2/roles/sessions`
- `GET /v2/roles/sessions/{session_id}`
- `GET /v2/roles/sessions/{session_id}/messages`
- `POST /v2/roles/sessions/{session_id}/actions/attach`
- `POST /v2/roles/sessions/{session_id}/actions/detach`
- `GET /v2/roles/sessions/{session_id}/artifacts`
- `GET /v2/roles/sessions/{session_id}/audit`
- `GET /v2/roles/sessions/{session_id}/memory/search`
- `GET /v2/roles/sessions/{session_id}/memory/artifacts/{artifact_id}`
- `GET /v2/roles/sessions/{session_id}/memory/episodes/{episode_id}`
- `GET /v2/roles/sessions/{session_id}/memory/state`
- `POST /v2/roles/sessions/{session_id}/actions/export`
- `POST /v2/roles/sessions/{session_id}/actions/export-to-workflow`

`useAIDialogue` remains responsible for UI state, streaming chat orchestration, and user interactions. PM and Director workbench wrappers also use this service for session listing, session creation, and workflow export. `SessionInspector` uses the same boundary for capability display, detach, and snapshot export. The desktop no longer has ad hoc RoleSession REST parsing outside the typed boundary, except for raw SSE streaming and legacy compatibility helpers. The service owns path construction, basic response unwrapping, host-scoped capability normalization, invalid-row filtering, and Context OS memory read payload extraction.

## Scope

This slice covers the non-stream RoleSession lifecycle used by PM, Chief Engineer, and Director desktop dialogue surfaces, including the standalone PM and Director workbench panels and the session inspector sidebar. SSE message streaming still stays inside `useAIDialogue` because it consumes the raw `Response.body` stream.

## 2026-05-23 Follow-up: Active Workspace Contract

The backend RoleSession router is also part of the PM, Chief Engineer, and
Director desktop contract. Any route that creates, lists, streams, exports, or
loads session evidence must use the active desktop workspace:

1. request/payload workspace when explicitly provided;
2. stored `session.workspace` when operating on an existing session;
3. `settings.workspace_path`;
4. `settings.workspace`.

This keeps legacy fallback while preventing desktop RoleSession artifacts,
audits, Context OS continuity, and workflow exports from using the Polaris repo
workspace after Electron has selected a target project.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py`
- `src/frontend/src/services/__tests__/roleSessionService.test.ts`
- `src/frontend/src/app/components/ai-dialogue/__tests__/useAIDialogue.test.ts`
- `src/frontend/src/app/components/ai-dialogue/__tests__/AIDialoguePanel.test.tsx`
- `src/frontend/src/app/components/pm/PMWorkbenchPanel.test.tsx`
- `src/frontend/src/app/components/director/__tests__/DirectorWorkbenchPanel.test.tsx`
- `src/frontend/src/app/components/session/SessionInspector.test.tsx`
