# ContextOS Full Landing Blueprint

**Document ID**: BLUEPRINT-2026-0614-CONTEXTOS-FULL-LANDING  
**Date**: 2026-06-14  
**Status**: In progress  
**Scope**: `kernelone.core`, `runtime.task_market`, `orchestration.pm_dispatch`, `roles.kernel`, `factory.cognitive_runtime`, ContextOS diagnostics/replay governance.

---

## 1. Decision

ContextOS is not a weak-model workaround. It is Polaris's cognitive runtime control plane for bounded, authorized, auditable state projection across LLMs, tools, humans, and agents.

The 2026-06-14 landing adopts these ownership rules:

1. ContextOS core four-layer implementation belongs to `kernelone.core`.
2. `context.engine` remains a graph-constrained role-context facade and does not own ContextOS runtime internals.
3. `roles.kernel` consumes ContextOS through `RoleContextGateway`, `TransactionKernel`, and LLM request preparation seams.
4. `factory.cognitive_runtime` owns durable runtime receipts, handoff export/rehydration, and SQLite-backed receipt persistence.
5. `orchestration.pm_dispatch` may record receipts and requeue critique through public Cell contracts, but must not write ContextOS internals or task-market storage directly.

## 2. Target Architecture

```text
TruthLog / WorkingState / ReceiptStore / ProjectionEngine
        |
        v
kernelone.core: StateFirstContextOS
        |
        +--> context.engine facade
        |
        +--> roles.kernel RoleContextGateway
        |       |
        |       v
        |   TransactionKernel -> LLMCaller -> AIExecutor final request seam
        |                              |
        |                              v
        |                  contextos.final_request RuntimeReceipt
        |
        +--> factory.cognitive_runtime RuntimeReceipt / HandoffPack

orchestration.pm_dispatch -> runtime.task_market public requeue
        |
        v
Director retry sees payload.last_failure from integration QA
```

## 3. Immediate Landing Slices

### Slice A: Graph And Governance Alignment

Current graph assets still contain historical statements that `polaris/kernelone/context/*` is owned by `roles.kernel`. The graph must be corrected before wider implementation:

- Extend `kernelone.core` ownership to include ContextOS root-layer files such as `truth_log_service.py`, `working_state_manager.py`, `receipt_store.py`, and `projection_engine.py`.
- Remove ContextOS implementation modules from `context.engine.current_modules`.
- Update `context_plane.yaml` migration notes so `roles.kernel` is only a consumer of KernelOne context capabilities.
- Add ADR-0071 addendum: four-layer ContextOS is `kernelone.core`; durable receipt/handoff is `factory.cognitive_runtime`; `roles.kernel` cannot create a parallel handoff or ContextOS schema.

### Slice B: Integration QA Critique Bridge

Observed defect: task-stage QA feedback reaches Director via `payload.last_failure`, but project-level integration QA only persists JSON/events. Director structurally cannot see integration-level critique.

Landing:

- `runtime.task_market.requeue_task()` accepts supervisor metadata `last_failure` and copies it to `payload.last_failure`.
- `orchestration.pm_dispatch` requeues affected Director tasks through public `RequeueTaskCommandV1` after failed integration QA.
- The persisted integration QA payload includes `director_critique_feedback` with attempted, requeued, skipped, and error lists.

This avoids forging a worker lease for `FailTaskStageCommandV1` after a task has already reached terminal resolved state.

### Slice C: Final Request Constraint Ledger

Observed gap: `RoleContextGateway` and `LLMCaller` can report pre-provider context metadata, while `AIExecutor` owns the final prompt budget truth after provider/model resolution, tool schema overhead, chat-message compression, and output clamp.

Landing:

- Emit best-effort `contextos.final_request` `RuntimeReceipt` from the final provider-bound request seam.
- Receipt payload must include resolved provider/model/window, requested/final output tokens, payload overhead, allowed prompt tokens, compression status, chat message counts, and redacted evidence refs.
- No new durable store; use `factory.cognitive_runtime` public contract.

### Slice D: Resolved Actor Capability Profile

Observed split: `RoleProfile`, `llm_config`, `ModelCatalog`, `ContextGateway`, `LLMCaller`, and `AIExecutor` each resolve parts of provider/model/window/capability truth.

Landing:

- Add a frozen read model `ResolvedActorCapabilityProfile`.
- Generate it from `RoleProfile + ModelCatalog.ModelSpec + session/host overrides`.
- Use it as a read-through helper, not a new source of truth.

### Slice E: Diagnostics And Replay

Observed gap: `ProjectionReport` exists but runtime currently discards it; replay should use the canonical
`polaris.delivery.cli.tools.contextos_replay` entrypoint instead of a separate governance script.

Landing:

- Add read-only diagnostics sections for ContextOS, projection, receipts, replay, and dormant modules.
- Use the deterministic `polaris.delivery.cli.tools.contextos_replay` CLI for replay verification.
- Keep benchmark scoring out of pytest; use CLI gates for matrices.

## 4. Dormant Module Governance

The following modules must not be silently wired into the hot path:

- `adaptive_weights.py`
- `budget_optimizer.py`
- `multi_resolution_store.py`
- `predictive.py`
- `pipeline/enhanced_runner.py`

Each module must be classified as active, feature-flagged experimental, or retired. Unclassified modules can remain importable for tests, but cannot be claimed as production capability.

## 5. Verification

Minimum focused verification:

```bash
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python -m pytest -q \
  src/backend/polaris/cells/runtime/task_market/tests/test_service.py::test_requeue_task_can_teach_next_claim_without_worker_lease \
  src/backend/polaris/tests/test_dispatch_pipeline_integration_qa.py::test_run_post_dispatch_integration_qa_failure_requeues_director_with_critique
```

Full landing verification adds:

```bash
LC_ALL=C.UTF-8 ruff check src/backend/polaris src/backend/docs/governance/ci/scripts --fix
LC_ALL=C.UTF-8 ruff format src/backend/polaris src/backend/docs/governance/ci/scripts
LC_ALL=C.UTF-8 PYTHONPATH=src/backend mypy <touched-python-files>
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
LC_ALL=C.UTF-8 PYTHONPATH=src/backend python -m polaris.delivery.cli.tools.contextos_replay \
  --workspace . --messages-json '{"messages":[{"role":"user","content":"ContextOS replay gate","sequence":"0"}]}'
```

## 6. Open Risks

1. Some generated descriptor/context packs may become stale after graph ownership changes.
2. `ProjectionReport` persistence must avoid creating a second durable receipt store.
3. Final request receipts must redact prompt content; receipts should describe budget decisions, not copy prompts.
4. Diagnostics must remain side-effect-free.
