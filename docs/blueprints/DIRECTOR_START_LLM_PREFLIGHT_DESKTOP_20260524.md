# Director Start LLM Preflight Desktop 2026-05-24

## Problem

The desktop shell disables Director startup when the Director LLM runtime is not
ready, but the backend `/v2/director/start` lifecycle endpoint can still be
called directly. That leaves a bypass where the service can enter a running
state even though the UI-level runtime gate says Director is blocked.

## Scope

- Guard `/v2/director/start` with the existing `ensure_required_roles_ready`
  helper.
- Check only the Director LLM role readiness for lifecycle startup.
- Keep `/v2/director/run` execution diagnostics responsible for task and worker
  readiness.

## Verification

- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -q`
- `pytest src/backend/polaris/tests/unit/delivery/http/test_routers_v2.py -q`
- `ruff check src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py src/backend/polaris/tests/unit/delivery/http/test_routers_v2.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py src/backend/polaris/tests/unit/delivery/http/test_routers_v2.py`
- `mypy src/backend/polaris/delivery/http/v2/director.py`
