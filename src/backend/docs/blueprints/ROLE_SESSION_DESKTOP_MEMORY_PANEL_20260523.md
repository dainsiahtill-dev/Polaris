# RoleSession Desktop Memory Panel Blueprint

Date: 2026-05-23

## Scope

Expose existing Context OS RoleSession memory routes from the shared desktop dialogue panel used by PM, Chief Engineer, and Director.

## Existing Backend Contracts

- `GET /v2/roles/sessions/{session_id}/memory/search?q=...&kind=...&entity=...&limit=...`
- `GET /v2/roles/sessions/{session_id}/memory/artifacts/{artifact_id}`
- `GET /v2/roles/sessions/{session_id}/memory/episodes/{episode_id}`
- `GET /v2/roles/sessions/{session_id}/memory/state?path=...`

## Frontend Plan

1. Extend `useAIDialogue` with memory search state, loading/error state, and selected detail state.
2. Add a compact `RoleSessionMemoryPanel` under the shared RoleSession strip.
3. Add a strip action that loads memory using the current task/run/role context as the default query.
4. Open artifact, episode, and state details through the existing backend read routes without duplicating persistence logic.

## Verification

- Add component test coverage for memory search and artifact detail reads.
- Re-run the PM/Director/Chief Engineer dialogue regression slice.
- Re-run the existing RoleSession backend memory/router tests.
