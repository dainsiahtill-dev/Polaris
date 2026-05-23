# HTTP Active Workspace Canonical Resolver

Date: 2026-05-23

## Finding

PM, Chief Engineer, Director, RoleChat, and RoleSession HTTP surfaces have
grown several local workspace resolver implementations. Some paths prefer the
active Electron `settings.workspace_path`, while others stringify any object
or read LLM readiness evidence from `settings` directly.

## Root Cause

The delivery layer lacked one canonical active-workspace helper. Tests that use
`MagicMock` can expose this because an unset `workspace_path` attribute may be
stringified as a mock object instead of falling back to `settings.workspace`.
The same split also lets LLM config load from the active workspace while
readiness test indexes are loaded from a stale workspace.

## Fix

- Harden `polaris.delivery.http.workspace.active_workspace_value()` so it
  ignores mock placeholders, prefers `workspace_path`, supports real
  `os.PathLike` values, and avoids stringifying arbitrary objects.
- Route PM chat, PM management, RoleChat, and RoleSession through the shared
  helper.
- Read shared role-readiness test indexes from the resolved active workspace,
  matching the config and cache-root workspace.
- Keep the runtime projection cell self-contained while aligning its mock
  placeholder guard with the shared resolver behavior.

## Verification

Targeted Python gates:

- `.venv\\Scripts\\python.exe -m ruff check src/backend/polaris/delivery/http/workspace.py src/backend/polaris/delivery/http/routers/_shared.py src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/delivery/http/routers/role_session.py src/backend/polaris/cells/runtime/projection/internal/llm_status.py src/backend/polaris/tests/unit/delivery/http/test_workspace.py src/backend/polaris/tests/test_llm_phase0_regression.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py --fix`
- `.venv\\Scripts\\python.exe -m ruff format src/backend/polaris/delivery/http/workspace.py src/backend/polaris/delivery/http/routers/_shared.py src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/delivery/http/routers/role_session.py src/backend/polaris/cells/runtime/projection/internal/llm_status.py src/backend/polaris/tests/unit/delivery/http/test_workspace.py src/backend/polaris/tests/test_llm_phase0_regression.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py`
- `.venv\\Scripts\\python.exe -m mypy src/backend/polaris/delivery/http/workspace.py src/backend/polaris/delivery/http/routers/_shared.py src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/delivery/http/routers/role_session.py src/backend/polaris/cells/runtime/projection/internal/llm_status.py src/backend/polaris/tests/unit/delivery/http/test_workspace.py src/backend/polaris/tests/test_llm_phase0_regression.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py`
- `.venv\\Scripts\\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/test_workspace.py src/backend/polaris/tests/test_llm_phase0_regression.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py src/backend/polaris/tests/unit/delivery/http/routers/test_pm_chat_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_role_session_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -q`
