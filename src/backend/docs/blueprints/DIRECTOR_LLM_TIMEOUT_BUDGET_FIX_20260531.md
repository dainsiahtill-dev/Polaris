# Director LLM Timeout Budget Fix - 2026-05-31

## Problem

During the real PM -> Director workflow validation, the first Director task
completed and wrote files, but the next Director task stayed in-flight for a
long time without producing a terminal result.

The runtime evidence showed that the Director worker was passing the task total
timeout budget into the per-call LLM timeout resolver. Because workflow tasks
can carry large budgets such as 3600 seconds, the per-call Director LLM timeout
was raised up to the runtime cap. This made a single Codex CLI invocation wait
far longer than the UI and workflow recovery model can tolerate.

## Root Cause

`WorkerExecutor._execute_code_generation()` used `task.timeout_seconds` as the
default value for `CodeGenerationEngine.resolve_llm_timeout()`.

That conflated two different budgets:

- Task total budget: how long the whole Director task may run.
- LLM call budget: how long one model invocation may run before the workflow
  receives a terminal timeout and can continue or report a recoverable failure.

## Fix

`WorkerExecutor` now resolves an independent per-call timeout hint:

- explicit PM contract metadata may set `llm_call_timeout_seconds`,
  `director_llm_timeout_seconds`, or `request_timeout_seconds`;
- otherwise the default per-call timeout is 180 seconds;
- task total timeout remains available only for the overall task deadline.

## Verification

Targeted tests verify:

- a task with `timeout_seconds=3600` still invokes the LLM with 180 seconds;
- explicit PM contract metadata can override the per-call timeout;
- existing worker executor behavior remains green.

Commands:

```powershell
.\.venv\Scripts\ruff.exe check src/backend/polaris/cells/director/tasking/internal/worker_executor.py src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py --fix
.\.venv\Scripts\ruff.exe format src/backend/polaris/cells/director/tasking/internal/worker_executor.py src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py
.\.venv\Scripts\mypy.exe src/backend/polaris/cells/director/tasking/internal/worker_executor.py src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py
.\.venv\Scripts\pytest.exe src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py -q
```
