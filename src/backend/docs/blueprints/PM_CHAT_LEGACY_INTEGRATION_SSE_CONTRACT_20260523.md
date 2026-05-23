# PM Chat Legacy Integration SSE Contract

Date: 2026-05-23
Classification: pattern
Owner: Codex

## Problem

The PM desktop dialogue surface uses `/v2/pm/chat/stream` for role-specific
streaming. Unit coverage now verifies framed PM SSE chunks, but the legacy
integration router suite still skipped both stream success and empty-message
stream error checks. That left the assembled FastAPI router contract weaker
than the unit contract.

## Architecture

```text
PM desktop client
  -> POST /v2/pm/chat/stream
  -> FastAPI app with pm_chat router + auth override
  -> shared SSE response wrapper
  -> llm.dialogue public streaming service
```

## Scope

- Replace skipped PM integration SSE success coverage with deterministic
  thinking, content, and complete frame assertions.
- Replace skipped PM integration empty-message coverage with an SSE error frame
  assertion.
- Assert the assembled router passes workspace, role, message, and context to
  the LLM dialogue public service.

## Non-Goals

- No change to PM production router behavior.
- No frontend route migration.
- No provider runtime or real LLM invocation.
- No generic role stream contract change.

## Verification Plan

- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/tests/integration/delivery/routers/test_pm_chat_router.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/tests/integration/delivery/routers/test_pm_chat_router.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/tests/integration/delivery/routers/test_pm_chat_router.py`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/integration/delivery/routers/test_pm_chat_router.py -q`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/integration/delivery/routers/test_pm_chat_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_pm_chat_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py -q`
