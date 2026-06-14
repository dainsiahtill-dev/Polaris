# ContextOS Full Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land ContextOS as a multi-consumer cognitive runtime layer with graph-aligned ownership, Director-visible QA critique, final request receipts, resolved actor capability profiles, diagnostics, replay gates, and dormant-module governance.

**Architecture:** ContextOS core stays in `kernelone.core`; `roles.kernel` and `orchestration.pm_dispatch` are consumers; durable runtime receipts and handoff stay in `factory.cognitive_runtime`. The rollout is test-first and split by owner Cell so each slice is independently verifiable.

**Tech Stack:** Python 3.12, pytest, ruff, mypy, Polaris Graph catalog/subgraphs, KernelOne ContextOS, runtime.task_market, factory.cognitive_runtime.

---

### Task 1: Graph And Governance Alignment

**Files:**
- Modify: `src/backend/docs/graph/catalog/cells.yaml`
- Modify: `src/backend/docs/graph/subgraphs/context_plane.yaml`
- Modify: `src/backend/polaris/cells/kernelone/core/cell.yaml`
- Modify: `src/backend/polaris/cells/context/engine/cell.yaml`
- Create: `docs/blueprints/CONTEXTOS_FULL_LANDING_BLUEPRINT_20260614.md`
- Create: `src/backend/docs/governance/templates/verification-cards/vc-20260614-contextos-full-landing.yaml`

- [ ] **Step 1: Update graph ownership**

Move ContextOS root-layer modules such as `polaris.kernelone.context.truth_log_service`, `working_state_manager`, `receipt_store`, and `projection_engine` into `kernelone.core`; remove ContextOS implementation modules from `context.engine.current_modules`; add `kernelone.core` as an explicit `context.engine` dependency in the Cell file.

- [ ] **Step 2: Add Blueprint and Verification Card**

Record the 2026-06-14 decision chain: ContextOS core is `kernelone.core`; receipts are `factory.cognitive_runtime`; `pm_dispatch` only requeues through `runtime.task_market`; final request receipts are emitted from the provider-bound request seam.

- [ ] **Step 3: Run graph sanity checks**

Run:

```bash
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python -m pytest -q src/backend/polaris/tests/architecture/test_graph_reality.py
```

Expected: graph tests pass or report only pre-existing unrelated graph gaps.

### Task 2: Integration QA Critique Bridge

**Files:**
- Modify: `src/backend/polaris/cells/runtime/task_market/internal/service.py`
- Modify: `src/backend/polaris/cells/runtime/task_market/tests/test_service.py`
- Modify: `src/backend/polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`
- Modify: `src/backend/polaris/tests/test_dispatch_pipeline_integration_qa.py`

- [ ] **Step 1: Write failing task_market test**

Assert `RequeueTaskCommandV1(metadata={"last_failure": ...})` writes `payload.last_failure`, so a later Director claim sees the critique without needing a worker lease.

- [ ] **Step 2: Write failing pm_dispatch test**

Assert failed project-level integration QA requeues the finished Director task to `pending_exec`, returns `director_critique_feedback`, and stores `last_failure.error_code == "INTEGRATION_QA_FAILED"`.

- [ ] **Step 3: Implement minimal bridge**

In `runtime.task_market.requeue_task`, copy `metadata.last_failure` into `item.payload.last_failure`. In `pm_dispatch`, call the task-market public `RequeueTaskCommandV1` path after failed integration QA and before the second result persist.

- [ ] **Step 4: Verify**

Run:

```bash
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python -m pytest -q \
  src/backend/polaris/cells/runtime/task_market/tests/test_service.py::test_requeue_task_can_teach_next_claim_without_worker_lease \
  src/backend/polaris/tests/test_dispatch_pipeline_integration_qa.py::test_run_post_dispatch_integration_qa_failure_requeues_director_with_critique
```

Expected: both tests pass.

### Task 3: Final Request Constraint Ledger

**Files:**
- Modify: `src/backend/polaris/kernelone/llm/engine/executor.py`
- Create or modify: `src/backend/polaris/tests/unit/kernelone/llm/engine/test_llm_engine_final_request_receipt.py`
- Modify as needed: `src/backend/polaris/kernelone/llm/engine/_executor_base.py`

- [ ] **Step 1: Write failing receipt test**

Inject a fake Cognitive Runtime service and assert `AIExecutor._execute_invoke` records a `contextos.final_request` receipt after provider/model/window/tool/chat budget resolution, including payload overhead, allowed prompt tokens, output clamp, chat message count, and compression status.

- [ ] **Step 2: Implement receipt helper**

Add a small private helper in `executor.py` that builds a redacted payload and best-effort records it through `factory.cognitive_runtime.public.RecordRuntimeReceiptCommandV1`. Do not create a new durable store.

- [ ] **Step 3: Verify**

Run:

```bash
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python -m pytest -q src/backend/polaris/tests/unit/kernelone/llm/engine/test_llm_engine_final_request_receipt.py
```

Expected: receipt test passes and existing prompt-budget tests still pass.

### Task 4: Resolved Actor Capability Profile

**Files:**
- Create: `src/backend/polaris/kernelone/llm/engine/actor_capability_profile.py`
- Create or modify: `src/backend/polaris/tests/unit/kernelone/llm/engine/test_actor_capability_profile.py`
- Modify: `src/backend/polaris/cells/roles/kernel/internal/llm_caller/caller.py`
- Modify: `src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py`

- [ ] **Step 1: Write failing profile tests**

Assert the resolver combines `RoleProfile`, `ModelCatalog.ModelSpec`, provider/model ids, role context policy, output limit, and tool/json/vision flags into a frozen read model without writing config state.

- [ ] **Step 2: Use the profile at request prep seams**

Replace ad hoc repeated provider/model/window/capability lookups in `LLMCaller` and `RoleContextGateway` with read-only use of the resolved profile where possible.

- [ ] **Step 3: Verify**

Run:

```bash
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python -m pytest -q \
  src/backend/polaris/tests/unit/kernelone/llm/engine/test_actor_capability_profile.py \
  src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py \
  src/backend/polaris/cells/roles/kernel/tests/test_llm_caller.py
```

Expected: profile tests pass and gateway/caller regressions pass.

### Task 5: Diagnostics And Replay Gate

**Files:**
- Modify: `src/backend/polaris/delivery/http/v2/runtime_diagnostics.py`
- Create: `src/backend/docs/governance/ci/scripts/run_contextos_replay.py`
- Modify: `src/backend/docs/governance/ci/scripts/run_contextos_governance_gate.py`
- Modify: `src/backend/docs/governance/ci/pipeline.template.yaml`
- Create or modify: `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_runtime_diagnostics_router.py`

- [ ] **Step 1: Add read-only diagnostics tests**

Assert `/v2/runtime/diagnostics` exposes `contextos`, `projection`, `receipts`, `replay`, and `dormant_modules` sections without writing files.

- [ ] **Step 2: Add replay CLI**

Create a deterministic governance CLI that runs existing ContextOS replay/projection tests and emits JSON with `status`, `tests`, `projection_report`, and `receipts` keys.

- [ ] **Step 3: Verify**

Run:

```bash
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python -m pytest -q \
  src/backend/polaris/kernelone/context/tests/test_contextos_replay_consistency.py \
  src/backend/polaris/kernelone/context/tests/test_context_os_projection_isolation.py \
  src/backend/polaris/tests/unit/delivery/http/routers/test_v2_runtime_diagnostics_router.py
```

Expected: all deterministic diagnostics/replay tests pass.

### Task 6: Final Quality Gates

**Files:** all touched files.

- [ ] **Step 1: Run ruff**

```bash
LC_ALL=C.UTF-8 ruff check src/backend/polaris src/backend/docs/governance/ci/scripts --fix
LC_ALL=C.UTF-8 ruff format src/backend/polaris src/backend/docs/governance/ci/scripts
```

- [ ] **Step 2: Run mypy on touched Python modules**

```bash
LC_ALL=C.UTF-8 PYTHONPATH=src/backend mypy \
  src/backend/polaris/cells/runtime/task_market/internal/service.py \
  src/backend/polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py \
  src/backend/polaris/kernelone/llm/engine/executor.py
```

- [ ] **Step 3: Run focused pytest suite**

```bash
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python -m pytest -q \
  src/backend/polaris/cells/runtime/task_market/tests/test_service.py \
  src/backend/polaris/tests/test_dispatch_pipeline_integration_qa.py \
  src/backend/polaris/tests/unit/kernelone/llm/engine \
  src/backend/polaris/kernelone/context/tests
```

Expected: focused suite passes, or failures are recorded with exact root cause and next action.
