# DEO-2C Deferred Repair Effects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move deterministic repair mutation authority out of synchronous adapter callbacks by exposing an immutable Director repair-effect plan and executing exactly one deferred repair round through the canonical roles.kernel directed-effect batch boundary.

**Architecture:** `director.runtime` remains sole repair planner and projects an immutable, hash-bound list of exact `write_file`, `edit_file`, and `delete_file` effects plus known rollback contingencies. `roles.adapters` may only return a typed deferred request for that projection. `roles.kernel` is the only consumer: after the active tool batch has fully returned, it validates the plan against `director.runtime`, converts the effects to synthetic `ToolInvocation` values, and submits one visible follow-up batch through the existing inventory/seal/admit/ready/claim/mutation-port path. No adapter callback may construct a physical executor or synchronously open another batch.

**Tech Stack:** Python 3.12, frozen dataclasses/Pydantic turn contracts, TaskRuntime DEO inventory, Director Runtime repair kernel, roles.kernel `ToolBatchRuntime`, pytest, Ruff, mypy.

---

## Scope lock

- DEO-2C owns immutable repair-effect projection, typed deferred request, exact plan revalidation, one-round kernel scheduling, and migration of the central runtime repair bridge.
- DEO-2D owns repository-wide removal of the remaining 38 raw repair mutation surfaces and the zero-unbound-mutation architecture proof.
- DEO-3 alone owns durable effect-receipt commit, recovery, parent close, later repair rounds, and terminal admission.
- Provider calls, Bench, target-project edits, receipt closure, hidden batches, `count_towards_batch_limit=False`, and adapter-owned TaskRuntime admission remain forbidden.

## File map

- `polaris/cells/director/runtime/public/contracts.py`: immutable public repair-effect and plan projection DTOs.
- `polaris/cells/director/runtime/public/service.py`: pure projection/hash/revalidation from the private repair plan.
- `polaris/cells/director/runtime/public/__init__.py`: public exports only.
- `polaris/cells/roles/kernel/public/directed_effect_contracts.py`: typed deferred request/result values; no physical executor.
- `polaris/cells/roles/kernel/internal/deferred_repair_effects.py`: validate request, re-plan, compare hash, synthesize one visible `ToolBatch`.
- `polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py`: consume deferred requests only after the active batch returns; execute one counted follow-up batch.
- `polaris/cells/roles/adapters/internal/director/runtime_repair_tool_adapter.py`: replace synchronous writer/editor/deleter callbacks with deferred-request projection.
- `polaris/cells/roles/adapters/internal/director/post_execution_repair_bridge.py`: consume central deferred bridge; no new executor construction.
- `polaris/cells/roles/adapters/internal/director/materialization_quality_callback_ports.py`: consume central deferred bridge; no new executor construction.
- `polaris/cells/director/runtime/tests/test_repair_kernel_contract.py`: Director projection TDD.
- `polaris/cells/roles/kernel/tests/test_deferred_repair_effects.py`: request validation, hash drift, one-round, synthetic invocation tests.
- `polaris/cells/roles/adapters/tests/test_director_repair_writers.py`: adapter zero-effect/deferred-request tests.
- `polaris/tests/architecture/test_deo_2c_deferred_repair_boundary.py`: dependency direction, no nested callback batch, no physical executor in central bridge.

### Task 1: Freeze DEO-2C governance and baseline

- [x] **Step 1: Record exact baseline inventory**

Run from `src/backend`:

```bash
rtk proxy rg -n 'executor_factory\s*=\s*DirectorToolExecutor|DirectorToolExecutor\(' \
  polaris/cells/roles/adapters/internal/director/{post_execution_repair_bridge.py,materialization_quality_callback_ports.py,quality_gate.py,execute_method_repair_bridge.py,execution.py}
```

Expected: frozen DEO-2 inventory remains 31 factory injections plus 7 direct constructions; any drift blocks implementation until the blueprint count is corrected.

Observed 2026-07-20: exact file counts remain `24 + 8 + 3 + 2 + 1 = 38`;
factory injections `21 + 8 + 2 = 31`, leaving 7 direct constructions.

- [x] **Step 2: Lock scheduling**

Update the DEO-2 blueprint and gap ledger so DEO-2C is the sole active/schedulable bucket; DEO-2D/3/4/pre-bench/Bench remain `not_schedulable`.

- [x] **Step 3: Add the open verification card**

Create `src/backend/docs/governance/templates/verification-cards/vc-20260720-deo-2c-deferred-repair-effects.yaml` with structural classification, assumptions, pre-mortem, zero Provider/Bench evidence, and closure gates.

### Task 2: Project an immutable execution-grade repair plan

- [x] **Step 1: Write failing public-contract tests**

Add tests proving:

```python
assert effect.tool_name in {"write_file", "edit_file", "delete_file"}
assert effect.arguments_hash == hash_directed_effect_arguments(effect.arguments)
assert plan.effect_count == len(plan.effects)
assert plan.plan_hash == hash_director_repair_effect_plan(plan)
assert plan.round_number == 1
```

Also prove mutable mappings/lists, caller-supplied hashes, duplicate call ids, non-canonical paths, unsupported tools, and round 2 are rejected.

- [x] **Step 2: Verify RED**

Run:

```bash
rtk python -m pytest polaris/cells/director/runtime/tests/test_repair_kernel_contract.py -q
```

Expected: failure because `DirectorRepairEffectV1` and `DirectorRepairEffectPlanV1` do not exist.

- [x] **Step 3: Implement minimal contracts and pure projection**

Add frozen, slotted DTOs with exact canonical fields:

```python
@dataclass(frozen=True, slots=True)
class DirectorRepairEffectV1:
    call_id: str
    operation_id: str
    tool_name: Literal["write_file", "edit_file", "delete_file"]
    arguments: DirectedEffectImmutableItemsV1
    arguments_hash: str
    contingency_kind: Literal["forward", "rollback"]
    activates_after_call_id: str | None = None

@dataclass(frozen=True, slots=True)
class DirectorRepairEffectPlanV1:
    plan_id: str
    source_tool: str
    round_number: Literal[1]
    effects: tuple[DirectorRepairEffectV1, ...]
    effect_count: int
    plan_hash: str
```

`plan_director_repair` must project these from the private `RepairPlan` before private classes leave `director.runtime`. Hash all fields in canonical order. Do not execute a writer/editor/deleter.

- [x] **Step 4: Verify GREEN and public exports**

Run the focused contract, public export, Ruff, mypy, and compileall gates.

### Task 3: Define the typed deferred request and pure kernel synthesis

- [x] **Step 1: Write failing kernel tests**

Cover exact-type/canonical reconstruction, workspace/task/attempt binding, plan hash drift, re-plan mismatch, duplicate request consumption, rollback contingency inclusion, and round 2 rejection with zero mutation-port calls.

- [x] **Step 2: Verify RED**

Run:

```bash
rtk python -m pytest polaris/cells/roles/kernel/tests/test_deferred_repair_effects.py -q
```

Expected: import failure for the new request/synthesis boundary.

- [x] **Step 3: Implement minimal request and synthesis service**

Add:

```python
@dataclass(frozen=True, slots=True)
class DeferredDirectorRepairRequestV1:
    request_id: str
    workspace: str
    task_id: str
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    plan: DirectorRepairEffectPlanV1
```

The internal synthesis service must re-run `plan_director_repair`, require exact `plan_id`/`plan_hash`/effect equality, and convert each effect to one synthetic `ToolInvocation`. Synthetic call ids are server-derived from request id, plan hash, ordinal, operation id, and contingency kind. No model argument or adapter metadata may choose them.

- [x] **Step 4: Verify GREEN**

Run focused kernel tests plus the existing DEO contract/lifecycle/dispatch tests.

### Task 4: Replace the central synchronous repair bridge

- [x] **Step 1: Write failing adapter tests**

Use an executor spy that raises on construction. Prove a plannable repair returns one exact deferred request, does not call `DirectorToolExecutor`, does not write/edit/delete, preserves source tool/plan hash/allowed paths, and rejects convergence `max_rounds > 1` with `deo_multi_round_repair_requires_receipt_close`.

- [x] **Step 2: Verify RED**

Run:

```bash
rtk python -m pytest polaris/cells/roles/adapters/tests/test_director_repair_writers.py -q
```

Expected: executor spy is constructed by current synchronous bridge.

- [x] **Step 3: Implement minimal deferred adapter projection**

Change `run_runtime_repair_with_director_tools` to plan and return a typed deferred result. Remove `executor_factory` from this central seam. Keep current failure projections for unsupported/unplannable rules. Do not claim repair success before the deferred batch and revalidation complete.

- [x] **Step 4: Verify GREEN and affected bridge suites**

Run repair writer, adapter bridge, materialization callback, and post-execution schedule suites.

### Task 5: Execute one visible follow-up batch at the kernel boundary

- [x] **Step 1: Write failing integration tests**

Prove the active batch returns before deferred synthesis, original DEO fence is released, the follow-up has a distinct batch id, every effect enters the sealed inventory, each mutation consumes its own grant, rollback contingencies are separately admitted, unused rollback members are aborted, and no second repair round executes.

- [x] **Step 2: Verify RED**

Run the new deferred tests plus `test_tool_batch_runtime.py` selected cases. Expected: no deferred follow-up consumer exists.

- [x] **Step 3: Implement minimal counted follow-up execution**

Extract deferred requests from authoritative successful results only after `ToolBatchRuntime.execute_batch` returns. Validate and synthesize exactly one follow-up `ToolBatch`, increment the visible batch count, run normal `_prepare_directed_effect_dispatch`, then normal `ToolBatchRuntime`. Do not recurse into `execute_tool_batch`; use a bounded loop of maximum one deferred round. Record both receipts; do not claim DEO-3 closure.

- [x] **Step 4: Verify GREEN and denial controls**

Run focused transaction/runtime tests including missing authority, forged hash, wrong workspace, second round, and executor zero-effect spies.

### Task 6: DEO-2C architecture, broad gates, and independent review

- [x] **Step 1: Add architecture fences**

Assert `director.runtime` imports no adapters/kernel internals; adapters call no TaskRuntime admission; the central repair bridge has no executor factory, physical executor construction, mutation call, or nested batch; only roles.kernel consumes `DeferredDirectorRepairRequestV1`; no request/grant/context is serializable into provider payloads.

- [x] **Step 2: Run complete DEO-2C proof ladder**

Run focused Director runtime, roles.kernel, roles.adapters, TaskRuntime, KernelOne tool execution, architecture, Ruff, format, mypy, compileall, catalog hard-fail, YAML/JSON parse, and `git diff --check` gates. Provider/Bench count must remain zero.

- [x] **Step 3: Independent specification and quality/security reviews**

Use two read-only JSON reviewers. Required verdict: `PASS/PASS`, no Critical/Important findings, explicit DEO-2/DEO-3 boundary confirmation, exact one-round confirmation, and zero adapter physical-effect path.

- [x] **Step 4: Close only DEO-2C**

Update blueprint, ledger, Cell metadata, graph catalog, verification card, and `memory/MEMORY.md` with fresh evidence. Schedule DEO-2D as the only next bucket; keep DEO-3/4/pre-bench/Bench `not_schedulable`.

## Self-review

- Spec coverage: blueprint sections 8, 11.6-11.10, 12, 13, and 14 each map to a task above.
- Placeholder scan: no `TBD`, `TODO`, “implement later”, or unqualified “write tests” remains.
- Type consistency: `DirectorRepairEffectPlanV1` is Director-owned; `DeferredDirectorRepairRequestV1` is kernel-owned; adapters only project it; kernel alone consumes it.
- Safety: no plan step weakens TaskRuntime, serializes a grant, hides a batch, performs a second round, or runs Bench.
