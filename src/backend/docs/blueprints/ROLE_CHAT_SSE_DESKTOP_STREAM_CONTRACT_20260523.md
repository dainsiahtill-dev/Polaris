# Role Chat SSE Desktop Stream Contract

Date: 2026-05-23
Classification: one_off
Owner: Codex

## Problem

PM, Chief Engineer, and Director desktop dialogue can fall back to the generic
role streaming endpoint:

```text
POST /v2/role/{role}/chat/stream
```

The unit suite already verifies stream errors and workspace resolution, but the
happy-path SSE tests for the generic stream were skipped as "special async
generator handling". Current `sse_event_generator` and `httpx.ASGITransport`
can consume deterministic fake streams, so those skips now hide a critical
desktop dialogue contract.

## Architecture

```text
AIDialoguePanel / useAIDialogue
  -> POST /v2/role/{pm|director|chief_engineer}/chat/stream
  -> role_chat.role_chat_stream
  -> llm.dialogue.public.generate_role_response_streaming
  -> sse_event_generator
  -> event: thinking_chunk | content_chunk | complete
```

## Scope

- Replace skipped generic role chat stream success tests with deterministic SSE
  body assertions.
- Cover PM plus the two requested desktop engineering roles:
  `director` and `chief_engineer`.
- Preserve the existing router behavior; this slice is a contract coverage fix.

## Non-Goals

- No provider runtime invocation.
- No changes to RoleSession streaming.
- No changes to frontend SSE parsing.

## Verification Plan

- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py -q`
- `npm run test -- useAIDialogue AIDialoguePanel RoleChatPanel llmService`
- `npm run typecheck`
- `npm run lint`
- `npm run test -- PMPage ChiefEngineerPage DirectorPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace useAIDialogue AIDialoguePanel RoleChatPanel llmService`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_role_router.py -q`
