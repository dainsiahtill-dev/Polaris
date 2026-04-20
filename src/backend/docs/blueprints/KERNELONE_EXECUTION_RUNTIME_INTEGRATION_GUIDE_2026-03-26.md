# KernelOne Execution Runtime Integration Guide (2026-03-26)

## 1. Goal

This guide defines the canonical integration surface for migrating all
thread/process/task execution paths to the KernelOne execution substrate.

Cells-only rollout pack:

1. `docs/blueprints/EXECUTION_BROKER_CELLS_ONLY_TASK_PACK_2026-03-27.md`

Core rule:

1. New code MUST use `polaris.kernelone.runtime` facade APIs.
2. No new direct `asyncio.create_task`, `threading.Thread`, or `subprocess.Popen`
   in business cells unless wrapped by the facade/runtime.
3. Business cells SHOULD route execution through
   `polaris.cells.runtime.execution_broker` instead of importing KernelOne
   runtime primitives directly.

---

## 2. Canonical Import Surface

Use only this import surface in migration PRs:

```python
from polaris.kernelone.runtime import (
    AsyncTaskSpec,
    BlockingIoSpec,
    ProcessSpec,
    ExecutionFacade,
    get_shared_execution_facade,
)
```

Cell-layer preferred import surface:

```python
from polaris.cells.runtime.execution_broker.public.contracts import (
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import (
    get_execution_broker_service,
)
```

When a module already runs inside an active event loop, prefer:

```python
facade = get_shared_execution_facade()
```

---

## 3. Facade APIs (Ready to Use)

### 3.1 Typed Specs

1. `AsyncTaskSpec`
2. `BlockingIoSpec`
3. `ProcessSpec`

Each spec carries `name`, `timeout_seconds`, and `metadata` for auditability.

### 3.2 Submit APIs

1. `submit_async_task(spec)`
2. `submit_blocking_io(spec)`
3. `submit_process(spec)`
4. `submit(spec)` (generic)
5. `submit_many(specs)` (batch)

### 3.3 Cell Broker APIs

1. `ExecutionBrokerService.launch_process(command)`
2. `ExecutionBrokerService.wait_process(handle_or_id, timeout_seconds=...)`
3. `ExecutionBrokerService.terminate_process(handle_or_id, timeout_seconds=...)`
4. `ExecutionBrokerService.get_process_status(query)`
5. `ExecutionBrokerService.list_active_processes()`

### 3.4 Lifecycle APIs

1. `wait_one(handle_or_id)`
2. `wait_many(handles_or_ids, timeout_per_item=..., overall_timeout=...)`
3. `cancel_many(handles_or_ids)`
4. `snapshot(handle_or_id)`
5. `list_runtime_snapshots(lane=..., status=...)`

### 3.5 One-shot APIs

1. `run_async_task(spec)`
2. `run_blocking_io(spec)`
3. `run_process(spec, collect_output=True)`

`run_process` returns `ProcessRunResult` with:

1. terminal `snapshot`
2. `stdout_lines`
3. `stderr_lines`

---

## 4. Migration Mapping

Use this mapping for replacing legacy execution code:

1. `asyncio.create_task(...)`
   Replace with `facade.submit_async_task(AsyncTaskSpec(...))`
2. `asyncio.to_thread(...)` or direct threadpool wrappers in cell/business code
   Replace with `facade.submit_blocking_io(BlockingIoSpec(...))`
3. `subprocess.Popen(...)` / `create_subprocess_exec(...)` in cell/business code
   Replace with `await facade.submit_process(ProcessSpec(...))` or `await facade.run_process(...)`
4. ad-hoc batch orchestration loops
   Replace with `submit_many(...)` + `wait_many(...)`

---

## 5. 10-Agent Parallel Integration Plan (Cells-Only)

Current rollout scope is strictly limited to `polaris/cells/**`.
Do not assign agents to `polaris/kernelone/**`, `polaris/infrastructure/**`,
or `polaris/delivery/**` in this phase.

Ownership is intentionally disjoint to avoid merge conflicts.

1. Agent-01: `polaris/cells/orchestration/pm_planning/**`
   Ensure process lifecycle paths are broker-routed.
2. Agent-02: `polaris/cells/orchestration/workflow_runtime/**`
   Remove residual direct process runner coupling in launcher/orchestrator entry.
3. Agent-03: `polaris/cells/roles/runtime/internal/worker_pool.py`
   Replace worker subprocess path with broker process submission.
4. Agent-04: `polaris/cells/director/execution/internal/tools/**`
   Replace direct `subprocess.run` tool execution path with broker-managed process run.
5. Agent-05: `polaris/cells/director/tasking/internal/worker_pool_service.py`
   Replace `run_in_executor` heavy blocking task execution with broker blocking lane.
6. Agent-06: `polaris/cells/llm/evaluation/internal/suites.py`
   Replace scattered `asyncio.to_thread` calls with broker `BlockingIoSpec`.
7. Agent-07: `polaris/cells/runtime/projection/internal/runtime_projection_service.py`
   Converge `to_thread` payload builders via broker blocking submissions.
8. Agent-08: `polaris/cells/archive/run_archive/internal/archive_hook.py`
   and `polaris/cells/archive/*/public/service.py`
   Replace ad-hoc thread/task kickoff with broker async submission.
9. Agent-09: `polaris/cells/factory/pipeline/internal/factory_store.py`
   and `polaris/cells/factory/pipeline/internal/factory_run_service.py`
   Move blocking file operations and loop tasks into broker lanes where applicable.
10. Agent-10: `polaris/cells/**/cell.yaml` and related `README.agent.md`
    Ensure `depends_on: runtime.execution_broker` and effect declarations align.

---

## 6. Integration Recipe

### 6.1 Process replacement (direct)

```python
facade = get_shared_execution_facade()
handle = await facade.submit_process(
    ProcessSpec(
        name="director-task",
        args=cmd,
        cwd=workspace_path,
        timeout_seconds=300.0,
        metadata={"task_id": task_id},
    )
)
status = await handle.wait(timeout=320.0)
```

### 6.2 One-shot process with captured logs

```python
facade = get_shared_execution_facade()
result = await facade.run_process(
    ProcessSpec(
        name="qa-smoke",
        args=cmd,
        timeout_seconds=120.0,
    ),
    collect_output=True,
)
if not result.ok:
    raise RuntimeError(result.snapshot.error)
```

### 6.3 Batch orchestration

```python
handles = await facade.submit_many(specs)
batch = await facade.wait_many(handles, overall_timeout=600.0)
if not batch.all_completed:
    await facade.cancel_many(batch.timed_out_execution_ids)
```

---

## 7. Quality Gate for Each Agent PR

Every migration PR MUST include:

1. no new direct execution primitive (`create_task`, `Thread`, `Popen`) in owned paths
2. at least one test validating timeout/cancel/status behavior
3. explicit UTF-8 handling retained for any text I/O touched
4. clear metadata tags in specs for observability

---

## 8. Current Shared Assets

1. Runtime core: `polaris/kernelone/runtime/execution_runtime.py`
2. Facade: `polaris/kernelone/runtime/execution_facade.py`
3. Runtime launcher integration example:
   `polaris/cells/orchestration/workflow_runtime/internal/process_launcher.py`
