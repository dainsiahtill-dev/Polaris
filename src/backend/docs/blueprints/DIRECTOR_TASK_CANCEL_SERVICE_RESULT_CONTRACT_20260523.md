# Director Task Cancel Service Result Contract

Date: 2026-05-23

## Scope

Keep the Electron Director cancel control aligned with the actual Director execution service response contract.

## Current Evidence

- Desktop calls `pmService.cancelDirectorTask(taskId)`, which posts to `POST /v2/director/tasks/{task_id}/cancel`.
- The route in `src/backend/polaris/delivery/http/v2/director.py` treated any truthy `service.cancel_task()` result as success.
- The real `polaris.cells.director.execution.service.DirectorService.cancel_task()` returns a payload dict:
  - `{ "ok": true, "task_id": "..." }` when accepted.
  - `{ "ok": false, "error": "...", "task_id": "..." }` when the task is missing or not cancellable.

## Fix Design

Normalize the service result at the HTTP boundary:

1. Preserve boolean compatibility for existing tests and any thin mock implementations.
2. Treat dict payloads as success only when `ok` or `cancelled` is explicitly true, or when the returned status is a cancelled token.
3. Return the service success payload to the desktop, with `ok` and `task_id` populated.
4. Convert explicit service failure payloads to HTTP 400 with the service error detail.

## Boundaries

- Target cell: `director.execution`.
- Delivery boundary: `polaris.delivery.http.v2.director`.
- No new state owner, storage path, graph truth, or Director execution backend is introduced.
- This fixes response interpretation only; it does not add workflow-level cancellation for projected PM workflow rows.

## Verification Plan

- Add router regression tests for dict success and dict failure payloads.
- Run focused gates:
  - `ruff check src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py --fix`
  - `ruff format src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
  - `mypy src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
  - `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -v`
  - focused frontend Director cancel tests.
