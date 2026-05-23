# Role Session Desktop Dialogue Bridge Blueprint (2026-05-23)

## Scope

This blueprint covers the desktop dialogue bridge shared by PM, Chief Engineer, and Director workspaces.

The goal is to reuse the existing `roles.runtime` delivery routers and `roles.session` lifecycle contracts so desktop role chat is not a detached legacy chat surface. Desktop panels should create RoleSession records, stream messages through the RoleSession message endpoint when a session exists, and attach sessions to the active task when the workspace provides task context.

## Current Evidence

- `roles.runtime` owns `polaris/delivery/http/routers/role_chat.py` and `polaris/delivery/http/routers/role_session.py`.
- `roles.session` owns `runtime/role_sessions/*`, `runtime/conversations/*`, and `runtime/session_attachments/*`.
- `role_session.py` already exposes:
  - `POST /v2/roles/sessions`
  - `POST /v2/roles/sessions/{session_id}/messages/stream`
  - `POST /v2/roles/sessions/{session_id}/actions/attach`
- The frontend `AIDialoguePanel` already exposes `sessionId`, `workspace`, `attachmentMode`, `attachedRunId`, and `attachedTaskId`, but `attachedRunId` and `attachedTaskId` were not wired into the hook and streaming still used `/v2/role/{role}/chat/stream`.

## Cell Boundary

- Target backend cells: `roles.runtime`, `roles.session`, `llm.dialogue`.
- Frontend integration point: `src/frontend/src/app/components/ai-dialogue/*` plus the role workspaces that instantiate it.
- No new backend business implementation is required. The implementation reuses existing public HTTP boundaries and does not add PM/Director/Chief Engineer business logic to unrelated cells.

## Data Flow

```text
PM/Chief Engineer/Director workspace
  -> AIDialoguePanel
  -> useAIDialogue
  -> POST /v2/roles/sessions
  -> optional POST /v2/roles/sessions/{id}/actions/attach
  -> POST /v2/roles/sessions/{id}/messages/stream
  -> RoleSessionService + llm.dialogue streaming
  -> persisted session messages and session attachment evidence
```

Fallback remains:

```text
useAIDialogue without a session id
  -> POST /v2/role/{role}/chat/stream
```

This keeps startup behavior resilient if session creation is delayed or unavailable.

## Desktop Behavior

- PM dialogue passes the explicit workspace and selected PM task as readonly attachment context.
- Director dialogue passes the explicit workspace and selected/current Director task as readonly attachment context.
- Chief Engineer dialogue continues to use the explicit workspace and RoleSession creation path; it remains isolated unless the workspace later provides a precise task/run attachment.
- The generic dialogue hook owns the decision to use RoleSession streaming when a session exists.

## Verification Plan

- Frontend hook test proving:
  - session creation sends workspace and attachment mode,
  - task attachment calls `/actions/attach`,
  - message streaming uses `/messages/stream` once a session exists.
- Frontend role workspace tests proving PM/Director/Chief Engineer panels still render and call role readiness checks.
- Frontend typecheck/build and targeted Vitest tests.
- Backend targeted RoleSession tests to verify the reused endpoint contract still passes.

## Risks

- Session creation is asynchronous, so the first user message can still fall back to legacy role chat if the user sends before session creation completes.
- Existing conversation save APIs remain as a separate persistence surface. This bridge does not remove legacy conversation persistence in this increment.
