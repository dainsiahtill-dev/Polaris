# PM Planning Input Diagnostics Gate - 2026-05-25

## Problem

The PM desktop startup diagnostics could report the PM runtime as ready when LanceDB, LLM, and the workspace/docs directory were available, even if there was no usable requirements or plan input. In that state the PM surface looked startable, but the planning iteration could produce zero tasks and then cascade into Director and QA blocked states.

## Change

- `/v2/pm/diagnostics` now returns a `planning_input` section with `ok`, `status`, `source`, `path`, byte/character counts, checked paths, and error evidence.
- PM start controls now treat missing, empty, or unreadable planning input as hard startup blockers for `/v2/pm/start`, `/v2/pm/start_loop`, and `/v2/pm/run_once`.
- `/v2/pm/run` treats a non-empty request `directive` as inline planning input, so Workbench runs with an explicit directive are not blocked by absent requirement files.
- The PM desktop diagnostics modal shows a dedicated planning-input section with remediation steps and checked paths.
- The PM backend evidence strip includes `input=<status>` so false-ready states are visible without opening the modal.

## Planning Input Sources

The diagnostics check these sources in order:

1. `runtime/contracts/requirements.md`
2. `workspace/docs/product/requirements.md`
3. `workspace/docs/10_requirements.md`
4. `runtime/contracts/plan.md`
5. `workspace/docs/product/plan.md`

For workspace documents, diagnostics check both the KernelOne logical workspace artifact path and the physical workspace docs path.

## Verification

- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py -q`
- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `npm run test -- PMDiagnosticsPanel PMWorkspace pmService`
- `npm run typecheck`
- `npm run lint`
- `git diff --check`
