# Execution Broker Cells-Only Task Pack (2026-03-27)

## Scope

This task pack is strictly limited to:

- `polaris/cells/**`

Do not modify:

- `polaris/kernelone/**`
- `polaris/infrastructure/**`
- `polaris/delivery/**`
- non-cell roots (except test fixtures already inside `polaris/cells/**`)

---

## Canonical Cell-Level API

All agents use this surface:

```python
from polaris.cells.runtime.execution_broker.public.contracts import (
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import (
    get_execution_broker_service,
)
```

For async/blocking helpers:

```python
from polaris.cells.runtime.execution_broker.public.service import (
    get_execution_broker_service,
)
from polaris.kernelone.runtime import AsyncTaskSpec, BlockingIoSpec
```

---

## Hard Rules for All 10 Agents

1. No new direct `subprocess.Popen` / `subprocess.run` in owned paths.
2. No new direct `asyncio.to_thread` / `ThreadPoolExecutor` / `run_in_executor` in owned paths when broker lane can be used.
3. Preserve explicit UTF-8 text I/O.
4. Add metadata (`cell`, `workspace`, `task_id` when available) in broker command.
5. Add at least one test under the same cell subtree.

---

## 10-Agent Ownership (Cells-Only)

1. Agent-01 (already partly converged, finish cleanup)
   Path: `polaris/cells/orchestration/pm_planning/**`
   Goal: Ensure all process lifecycle calls go through `runtime.execution_broker`.
2. Agent-02 (already partly converged, finish cleanup)
   Path: `polaris/cells/orchestration/workflow_runtime/**`
   Goal: Remove residual direct process runner coupling in launcher/orchestrator entry.
3. Agent-03
   Path: `polaris/cells/roles/runtime/internal/worker_pool.py`
   Goal: Replace worker subprocess path with broker process submission.
4. Agent-04
   Path: `polaris/cells/director/execution/internal/tools/**`
   Goal: Replace direct `subprocess.run` tool execution path with broker-managed process run.
5. Agent-05
   Path: `polaris/cells/director/tasking/internal/worker_pool_service.py`
   Goal: Replace `run_in_executor` heavy blocking task execution with broker blocking lane.
6. Agent-06
   Path: `polaris/cells/llm/evaluation/internal/suites.py`
   Goal: Replace scattered `asyncio.to_thread` calls with broker `BlockingIoSpec`.
7. Agent-07
   Path: `polaris/cells/runtime/projection/internal/runtime_projection_service.py`
   Goal: Converge `to_thread` payload builders via broker blocking submissions.
8. Agent-08
   Path: `polaris/cells/archive/run_archive/internal/archive_hook.py` + `polaris/cells/archive/*/public/service.py`
   Goal: Replace ad-hoc thread/task kickoff with broker async submission.
9. Agent-09
   Path: `polaris/cells/factory/pipeline/internal/factory_store.py` + `factory_run_service.py`
   Goal: Move blocking file operations and loop tasks into broker lanes where applicable.
10. Agent-10 (cells governance sync)
    Path: `polaris/cells/**/cell.yaml` + related `README.agent.md` in touched cells
    Goal: Ensure `depends_on: runtime.execution_broker` and effect declarations are aligned.

---

## Per-Agent Acceptance Checklist

Each agent must deliver:

1. Code change only in owned `polaris/cells/**` paths.
2. `rg` proof in owned paths:
   - no new `subprocess.Popen` / `subprocess.run`
   - no new direct `ThreadPoolExecutor` / raw thread offload where broker is applicable
3. At least one passing test in owned cell subtree.
4. Explicit note of any intentionally retained primitive with reason.

---

## Merge Order

1. Agent-01 + Agent-02 (orchestration entry points)
2. Agent-03 + Agent-04 + Agent-05 (roles/director execution core)
3. Agent-06 + Agent-07 + Agent-09 (llm/runtime/factory blocking lanes)
4. Agent-08 (archive async kickoff harmonization)
5. Agent-10 (cells governance final sync)
