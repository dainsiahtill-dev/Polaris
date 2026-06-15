# Polaris 生产稳定性硬化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the highest-value production stability risks found in the 2026-06-15 deep audit: prompt budget correctness, effect-sink authorization, reliable facts, graph truth, and bounded runtime growth.

**Architecture:** Treat ContextOS budget enforcement, runtime execution sinks, and durable fact streams as fail-closed production invariants. Keep public contracts backward-compatible where possible, but enforce stricter checks at the sink layer.

**Tech Stack:** Python 3.12, pytest, mypy, ruff, SQLite-backed TaskMarket, KernelOne ContextOS/LLM runtime, NATS JetStream.

---

### Task 0: Governance Baseline

**Files:**
- Create: `src/backend/docs/governance/templates/verification-cards/vc-20260615-production-stability-hardening.yaml`
- Create: `src/backend/docs/governance/decisions/adr-0094-runtime-effect-sinks-and-reliable-facts.md`

- [ ] Write the verification card and ADR before changing runtime behavior.
- [ ] Run the focused baseline tests for ContextOS chunks, LLM executor, TaskMarket, ExecutionBroker, JetStream consumer, and graph gates.

### Task 1: ContextOS / LLM Budget Hardening

**Files:**
- Modify: `src/backend/polaris/kernelone/context/chunks/budget.py`
- Modify: `src/backend/polaris/kernelone/llm/engine/_executor_base.py`
- Modify: `src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py`
- Test: `src/backend/polaris/kernelone/context/tests/test_chunks.py`
- Test: `src/backend/polaris/kernelone/llm/engine/tests/test_executor.py`
- Test: `src/backend/polaris/kernelone/llm/engine/stream/tests/test_executor.py`
- Test: `src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_fallback.py`

- [ ] Add failing tests for pinned `SYSTEM` / `CURRENT_TURN` admission and pinned-over-budget failure.
- [ ] Add failing tests for negative headroom raising a budget error instead of clamping to 256.
- [ ] Add failing gateway tests for giant system prompts and state-first/no-snapshot compression.
- [ ] Implement pinned chunk admission and fail-closed output budget checks.
- [ ] Verify focused tests pass.

### Task 2: Execution / File Sink Authorization

**Files:**
- Modify: `src/backend/polaris/cells/runtime/execution_broker/public/contracts.py`
- Modify: `src/backend/polaris/cells/runtime/execution_broker/internal/service.py`
- Modify: `src/backend/polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py`
- Modify: `src/backend/polaris/kernelone/llm/toolkit/executor/handlers/command.py`
- Modify: `src/backend/polaris/kernelone/tool_execution/constants.py`
- Test: `src/backend/polaris/cells/runtime/execution_broker/tests/test_service.py`

- [ ] Add failing broker tests for destructive git commands, absolute log paths, and dangerous env overrides.
- [ ] Add failing tool-executor tests for shell input redirection outside workspace and KFS-routed writes.
- [ ] Enforce command shape, workspace guard, env filtering, and log path checks at broker/tool sinks.
- [ ] Verify focused sink and permission tests pass.

### Task 3: TaskMarket / Fact Stream Reliability

**Files:**
- Modify: `src/backend/polaris/cells/events/fact_stream/public/contracts.py`
- Modify: `src/backend/polaris/cells/events/fact_stream/public/service.py`
- Modify: `src/backend/polaris/cells/runtime/task_market/internal/service.py`
- Modify: `src/backend/polaris/cells/runtime/task_market/internal/store.py`
- Modify: `src/backend/polaris/cells/runtime/task_market/internal/store_sqlite.py`
- Test: `src/backend/polaris/cells/runtime/task_market/tests/test_service.py`
- Test: `src/backend/polaris/cells/runtime/task_market/tests/test_store_sqlite.py`

- [ ] Add failing tests for lifecycle receipt not written before failed CAS.
- [ ] Add failing tests for idempotent fact append by outbox id.
- [ ] Add failing tests for active/terminal duplicate publish not clearing lease or rolling back status.
- [ ] Move lifecycle receipts to deterministic outbox-after-CAS relay and enforce idempotency.
- [ ] Verify TaskMarket and fact stream tests pass.

### Task 4: JetStream / Runtime Diagnostics Reliability

**Files:**
- Modify: `src/backend/polaris/infrastructure/messaging/nats/ws_consumer_manager.py`
- Modify: `src/backend/polaris/delivery/http/v2/runtime_diagnostics.py`
- Test: `src/backend/polaris/tests/unit/infrastructure/messaging/nats/test_ws_consumer_manager.py`
- Test: `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_runtime_diagnostics_router.py`

- [ ] Add failing tests proving queue-full does not ACK dropped messages.
- [ ] Add failing tests proving disconnected JetStream is not reported healthy.
- [ ] NAK or leave unacked queue-full messages and surface resync-required state.
- [ ] Verify focused JetStream and diagnostics tests pass.

### Task 5: Graph Truth / Release Gate Convergence

**Files:**
- Modify: `src/backend/polaris/kernelone/context/chunks/assembler.py`
- Modify: `src/backend/polaris/tests/architecture/test_kernelone_release_gates.py`
- Modify: `src/backend/polaris/tests/architecture/test_graph_reality.py`
- Modify: `src/backend/docs/governance/ci/scripts/run_kernelone_release_gate.py`
- Modify: `src/backend/docs/governance/ci/pipeline.template.yaml`

- [ ] Add failing tests for KernelOne reverse imports and catalog verification target existence.
- [ ] Remove KernelOne dependency on `roles.kernel.internal.metrics`.
- [ ] Add context pack freshness and verification-target collect to release gates.
- [ ] Verify graph and release gate commands pass.

### Task 6: Bounded Runtime Growth

**Files:**
- Modify: `src/backend/polaris/kernelone/context/truth_log_service.py`
- Modify: `src/backend/polaris/kernelone/context/context_os/runtime/engine.py`
- Modify: `src/backend/polaris/kernelone/llm/engine/stream/executor.py`

- [ ] Add failing bounded-pending TruthLog indexing tests.
- [ ] Add stream complete tests proving huge output is represented by hash/metadata, not duplicated full text.
- [ ] Add cleanup tests for sync ContextOS context-manager resources.
- [ ] Implement bounded queues / resource cleanup / stream size caps.
- [ ] Verify focused runtime-growth tests pass.

### Final Verification

- [ ] `ruff check . --fix`
- [ ] `ruff format .`
- [ ] `PYTHONUTF8=1 LC_ALL=C.UTF-8 PYTHONPATH=src/backend mypy <touched-python-files>`
- [ ] Focused pytest suites from all tasks.
- [ ] `PYTHONUTF8=1 LC_ALL=C.UTF-8 PYTHONPATH=src/backend python src/backend/docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`
- [ ] `PYTHONUTF8=1 LC_ALL=C.UTF-8 PYTHONPATH=src/backend python src/backend/docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace src/backend --mode fail-on-new`
