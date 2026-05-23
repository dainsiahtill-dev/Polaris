# PM Chat Active Workspace Stream Contract

Date: 2026-05-23
Classification: pattern
Owner: Codex

## Problem

The PM-specific chat routes still resolve the workspace from
`settings.workspace` directly:

```text
POST /v2/pm/chat
POST /v2/pm/chat/stream
GET  /v2/pm/chat/status
```

Other desktop role surfaces already prefer `workspace_path` and fall back to
`workspace`. The PM-specific route therefore can generate or validate PM chat
against the Polaris repo root instead of the active desktop target workspace.

The stream success-path test for `/v2/pm/chat/stream` is also skipped, leaving
the PM-specific SSE contract weaker than the generic role stream contract.

## Architecture

```text
PM desktop / legacy PM chat clients
  -> /v2/pm/chat | /v2/pm/chat/stream | /v2/pm/chat/status
  -> pm_chat router
  -> active workspace resolver: workspace_path -> workspace
  -> llm.dialogue public service
```

## Scope

- Add a PM chat workspace resolver matching the desktop active-workspace
  contract.
- Use it for non-streaming chat, streaming chat, and status configuration
  lookup.
- Replace the skipped PM stream success test with framed SSE assertions.
- Add active-workspace assertions for PM chat and PM chat status.

## Non-Goals

- No frontend route migration.
- No provider runtime invocation.
- No change to generic `/v2/role/{role}/chat/stream`.

## Verification Plan

- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/tests/unit/delivery/http/routers/test_pm_chat_v2.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/tests/unit/delivery/http/routers/test_pm_chat_v2.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/tests/unit/delivery/http/routers/test_pm_chat_v2.py`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_pm_chat_v2.py -q`
- `npm run test -- PMPage PMWorkspace PMAIDialoguePanel useAIDialogue llmService`
- `npm run typecheck`
- `npm run lint`
- `npm run test -- PMPage ChiefEngineerPage DirectorPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace useAIDialogue AIDialoguePanel llmService`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_pm_chat_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py -q`
