# RoleSession Desktop Snapshot Export Blueprint

Date: 2026-05-23

## Scope

Expose the existing RoleSession snapshot export route from the shared desktop dialogue panel used by PM, Chief Engineer, and Director.

## Existing Backend Contract

- `POST /v2/roles/sessions/{session_id}/actions/export`
- Body:
  - `include_messages: boolean`
  - `format: "json" | "markdown"`
- Response:
  - `{ ok: true, export: ... }`

## Frontend Plan

1. Extend `useAIDialogue` with snapshot export state and an explicit export action.
2. Add a compact RoleSession snapshot panel with JSON and Markdown modes.
3. Keep workflow export separate from snapshot export:
   - snapshot export reads current RoleSession contents for inspection.
   - workflow export sends evidence into PM/Director/Factory flow.

## Verification

- Add component test coverage for JSON and Markdown snapshot export.
- Re-run focused dialogue tests.
- Re-run TypeScript, build, and existing RoleSession backend router tests.
