# Director Tasks Debug Evidence Failure Isolation

Date: 2026-05-23

## Finding

`GET /v2/director/tasks` writes optional debug evidence from a `finally` block. The debug writer only caught `RuntimeError` and `ValueError`, so filesystem failures such as a locked log file could turn a successful task-list request into a 500.

## Root Cause

The debug-evidence helper treated diagnostics as best effort in comments but did not catch `OSError` or JSON serialization `TypeError`.

## Fix

- Expanded the debug writer exception boundary to include `OSError` and `TypeError`.
- Logged the failed evidence append at debug level instead of silently swallowing it.
- Added a regression test that forces the debug log write to fail and verifies the helper contains the failure.

## Verification

Targeted Python gates:

- `ruff check src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/cells/director/task_consumer/__init__.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/cells/director/task_consumer/__init__.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `mypy src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/cells/director/task_consumer/__init__.py`
- `.venv\\Scripts\\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -v`
