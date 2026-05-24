# Role Session Workflow Export Preflight Desktop 2026-05-24

## Problem

Role-session desktop panels can export a session bundle directly into PM,
Director, or Factory workflow runs through
`/v2/roles/sessions/{id}/actions/export-to-workflow`.
That route calls `OrchestrationCommandService.execute_pm_run` and
`execute_director_run` directly, or creates Factory runs, bypassing the role
readiness gates used by the desktop entry points.

## Scope

- Before creating a PM export run, require the PM runtime role to be LLM-ready.
- Before creating a Director export run, require the Director runtime role to be
  LLM-ready.
- Before creating a Factory export run, require the staged runtime roles
  PM/Chief Engineer/Director/QA to be LLM-ready.

## Verification

- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py -q`
- `ruff check src/backend/polaris/delivery/http/routers/role_session.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py --fix`
- `ruff format src/backend/polaris/delivery/http/routers/role_session.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py`
- `mypy src/backend/polaris/delivery/http/routers/role_session.py`
