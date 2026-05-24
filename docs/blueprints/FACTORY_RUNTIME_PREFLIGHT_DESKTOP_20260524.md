# Factory Runtime Preflight Desktop 2026-05-24

## Problem

Factory desktop start could create and schedule a run before validating the LLM
runtime roles required by its stage graph. The run then appeared active while
the runtime panel reported blocked roles, which made PM readiness look stale or
contradictory.

## Scope

- Resolve the Factory stage list before run creation.
- Require every non-architect stage role in that graph to be LLM-ready.
- Include PM, Chief Engineer, Director, and QA when their stages are configured.
- Keep the check before `FactoryRunService.create_run` and before background
  scheduling.

## Verification

- `ruff check src/backend/polaris/delivery/http/routers/_shared.py src/backend/polaris/delivery/http/routers/factory.py src/backend/polaris/delivery/http/routers/role_session.py src/backend/polaris/tests/unit/delivery/http/routers/test_factory_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py --fix`
- `ruff format src/backend/polaris/delivery/http/routers/_shared.py src/backend/polaris/delivery/http/routers/factory.py src/backend/polaris/delivery/http/routers/role_session.py src/backend/polaris/tests/unit/delivery/http/routers/test_factory_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py`
- `mypy src/backend/polaris/delivery/http/routers/_shared.py src/backend/polaris/delivery/http/routers/factory.py src/backend/polaris/delivery/http/routers/role_session.py`
- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_factory_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py -q`
