# KernelOne Context OS + Cognitive Runtime Hardening Plan

- Status: Executed (H1-H6 landed)
- Date: 2026-03-27
- Scope: `polaris/kernelone/context/**`, `polaris/application/cognitive_runtime/**`, `polaris/cells/roles/**`

> This hardening plan extends the landed Phase 0-6 baseline.
> It does not replace graph truth or change ownership boundaries defined in `AGENTS.md`, `docs/graph/**`, and `docs/cognitive_runtime_architecture.md`.
> Follow-on attention/runtime work is tracked in
> `docs/KERNELONE_CONTEXT_OS_ATTENTION_RUNTIME_IMPROVEMENT_PLAN_2026-03-27.md`.
>
> 2026-04-16 起，ContextOS / Cognitive Runtime / Turn Engine 的当前目标态统一收口为
> `docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`。
> 2026-04-17 起，Phase 7 监控基线（TurnResult.metrics + stream complete.monitoring）同样仅在该 canonical 蓝图维护。
> 本文作为前置 hardening 记录保留，不再定义新的 target-state 变更。

---

## 1. Goal

Convert current architecture correctness from "document-level agreement" into "runtime-enforced invariants" under real multi-turn and multi-role pressure.

The priority is stability and auditability, not feature expansion.

---

## 2. Hard Boundaries

1. `roles.session` remains raw conversation truth owner.
2. `state_first_context_os` remains a derived projection snapshot only.
3. `Context OS` owns working-set assembly, not business truth ownership.
4. `Cognitive Runtime` owns authority and evidence (`lease/validate/receipt/handoff`), not context truth.
5. SQLite is current storage backend, not a permanent architecture promise.

---

## 3. Risk to Workstream Mapping

1. Multi-ledger truth split -> `H1 Invariant Enforcement`
2. Routing misclassification drift -> `H3 Routing Confidence + Reclassification`
3. "Theoretical reversibility" only -> `H3/H4 Provenance + Reopen/Restore`
4. Working state entropy -> `H1 Typed schema + supersedes conflict semantics`
5. Context OS / Cognitive Runtime responsibility overlap -> `H1/H2 authority lattice + turn envelope`
6. SQLite write lock pressure -> `H5 single-writer queue + WAL discipline`
7. Missing quality gate -> `H6 measurable eval + rollout gate`

---

## 4. Execution Phases

### H1: Runtime Invariant Enforcement

Outputs:

1. Explicit invariant contract module (`truth/projection/evidence boundary`).
2. Snapshot validator that rejects projection payloads violating ownership rules.
3. Tests that fail if `state_first_context_os` is treated as source-of-truth.

Target areas:

1. `polaris/kernelone/context/context_os/`
2. `polaris/kernelone/context/history_materialization.py`
3. `polaris/cells/roles/session/`

Acceptance:

1. Invariant violations are machine-detectable, not only documented.

### H2: Turn Envelope Transaction Semantics

Outputs:

1. `TurnEnvelope` model linking: `turn_id`, `projection_version`, `lease_id`, `validation_id`, `receipt_ids`.
2. Deterministic commit ordering for context projection, validation, and evidence persistence.
3. Recovery semantics for partial failure (write succeeded, receipt failed; receipt succeeded, projection failed).

Target areas:

1. `polaris/cells/roles/kernel/internal/turn_engine.py`
2. `polaris/application/cognitive_runtime/service.py`
3. `polaris/infrastructure/cognitive_runtime/sqlite_store.py`

Acceptance:

1. Every high-risk turn has a traceable envelope chain.

### H3: Routing Reliability + Reclassify/Reopen

Outputs:

1. Routing confidence score and fallback strategy: deterministic first, escalate on ambiguity.
2. Reclassification API for misrouted entries.
3. Reopen support for sealed episodes with provenance-preserving state transition.

Target areas:

1. `polaris/kernelone/context/context_os/runtime.py`
2. `polaris/kernelone/context/context_os/models.py`
3. `polaris/kernelone/context/context_os/domain_adapters/`

Acceptance:

1. Misroutes become correctable operations, not irreversible errors.

### H4: Cross-Role Handoff Rehydration

Outputs:

1. Typed `HandoffPack` contract with `run_card`, `open_loops`, `decision_log`, `artifact_refs`, `source_spans`.
2. Receiver-side rehydration flow that rebuilds local working state instead of replaying foreign narrative.
3. Coverage tests for A->B role handoff quality.

2026-04-16 统一说明：

- 这里的 `HandoffPack` 逻辑名已经收敛到现有 `ContextHandoffPack` contract。
- 后续 TransactionKernel / ExplorationWorkflowRuntime 重构不得在 `roles.kernel` 再创建第二套 handoff schema。
- 任何新增字段都应优先扩展 `polaris/domain/cognitive_runtime/models.py` 与 `factory.cognitive_runtime` 公开契约。

Target areas:

1. `polaris/application/cognitive_runtime/service.py`
2. `polaris/cells/factory/cognitive_runtime/public/service.py`
3. `polaris/cells/roles/runtime/public/service.py`

Acceptance:

1. Handoff quality is measurable and reproducible across roles.

### H5: SQLite Concurrency Hardening

Outputs:

1. Single-writer queue for receipt/handoff persistence.
2. WAL and retry strategy with bounded backoff and idempotent upsert semantics.
3. Storage interface boundary to allow future backend migration without contract break.

Target areas:

1. `polaris/infrastructure/cognitive_runtime/sqlite_store.py`
2. `polaris/kernelone/storage/**`

Acceptance:

1. No turn-loop stalls caused by sync write contention under stress tests.

### H6: Evaluation Gate and Rollout Criteria

Outputs:

1. Mandatory metrics: fact recovery, open-loop continuity, artifact restore precision, temporal update correctness, abstention, compaction regret.
2. Shadow-to-mainline promotion gate tied to metrics, not subjective confidence.
3. CI-friendly benchmark fixtures and threshold policy.

Target areas:

1. `polaris/kernelone/context/context_os/evaluation.py`
2. `polaris/kernelone/context/tests/`
3. `docs/governance/ci/`

Acceptance:

1. Runtime mode promotion (`shadow` -> stronger mode) requires gate pass.

---

## 5. Rollout Strategy

1. Implement H1-H3 first before new features.
2. Keep `POLARIS_COGNITIVE_RUNTIME_MODE=shadow` as default during hardening.
3. Promote only after H6 gate proves regression-safe behavior.

---

## 6. Definition of Done

1. Invariants are enforced by code and tests.
2. Turn envelopes provide end-to-end traceability.
3. Routing errors are recoverable (reclassify/reopen).
4. Handoff rehydration preserves role continuity.
5. SQLite path is stable under concurrency and remains replaceable.
6. Quality metrics are mandatory for rollout decisions.

---

## 7. Execution Status

1. `H1` completed: persisted projection invariants are enforced in code and tests.
2. `H2` completed: turn envelope now threads through validation / receipt / handoff.
3. `H3` completed: routing confidence, reclassify, and reopen are implemented.
4. `H4` completed: handoff packs now export typed state and support receiver-side rehydration.
5. `H5` completed: SQLite persistence now uses a single-writer queue with WAL discipline.
6. `H6` completed: Context OS metrics now support rollout-gate threshold evaluation.
7. Attention-runtime follow-up work is intentionally split into a new plan so that
   `H1-H6` remains a completed hardening baseline rather than becoming an open-ended catch-all backlog.
