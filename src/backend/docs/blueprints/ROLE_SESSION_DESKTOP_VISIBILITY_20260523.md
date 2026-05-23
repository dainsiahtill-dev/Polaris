# Role Session Desktop Visibility Blueprint (2026-05-23)

## Scope

This blueprint covers the desktop visibility layer for PM, Chief Engineer, and Director AI dialogue sessions.

The previous increment connected desktop dialogue to the existing RoleSession stream and attachment endpoints. This increment makes that backend state visible in the shared AI panel so users can tell whether the conversation is backed by a persisted RoleSession, whether it is isolated or attached to task context, and when a new RoleSession is created.

## Current Evidence

- `AIDialoguePanel` is shared by PM, Chief Engineer, and Director workspaces.
- `useAIDialogue` now creates RoleSession records through `POST /v2/roles/sessions`.
- `useAIDialogue` now attaches task context through `POST /v2/roles/sessions/{id}/actions/attach`.
- `useAIDialogue` now streams through `POST /v2/roles/sessions/{id}/messages/stream` when a session id exists.
- The desktop UI still only showed provider/model status, so persisted session state was not visible to the operator.

## Boundary

- Frontend implementation: `src/frontend/src/app/components/ai-dialogue/*`.
- Backend implementation: no new backend endpoint. This increment reuses existing `roles.runtime` and `roles.session` HTTP routes.
- Backend cells involved:
  - `roles.runtime`: delivery router ownership for role/session HTTP APIs.
  - `roles.session`: RoleSession lifecycle and attachment state owner.
  - `llm.dialogue`: role response stream provider.

## Design

```text
PM/Chief Engineer/Director workspace
  -> AIDialoguePanel
  -> useAIDialogue
  -> role session state returned to panel
  -> compact session evidence strip
       - session id / initializing / unavailable
       - attachment mode
       - attached run or task id
       - new session action
```

The strip is deliberately compact. It is a status/control surface, not a second task board or hidden workflow launcher.

## UX Rules

- Use Lucide icons only.
- Keep controls stable and dense.
- Do not invent session state. If the backend has not returned a session id, display an unavailable/initializing state.
- Do not add export-to-workflow here yet; that operation starts backend workflows and needs a separate confirmation design.

## Verification Plan

- Hook tests prove RoleSession creation, attachment, session streaming, and fallback behavior.
- Panel tests prove session state is rendered and the new-session action resets and recreates a RoleSession.
- Existing PM/Chief Engineer/Director workspace tests continue to pass because the shared panel is used by all three surfaces.
- Frontend typecheck/build and targeted lint.
