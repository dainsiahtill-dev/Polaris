# DEO-2A TaskRuntime Inventory and Claim Grant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make TaskRuntime durably seal one complete mutation inventory, prove
all sealed intents are admitted before any claim/abort, and issue a non-replayable
claim-bound grant for exactly one `EFFECT_STARTED` transition.

**Architecture:** TaskRuntime remains the only durable DEO writer. The parent
registry gains strict `parent_inventory_sealed` and `parent_inventory_ready`
facts, while the existing operation stream keeps one event-sourced operation per
effect. Admission must match a sealed member; claim and abort require the ready
proof. A fresh confirmed claim returns a frozen grant, but exact replay never
reissues one.

**Tech Stack:** Python 3.11+, frozen dataclasses, FactStream strict JSONL,
guarded CAS append, pytest, Ruff, mypy, Cell metadata governance.

---

## Scope and execution boundary

This is only DEO-2A. It does not modify `roles.kernel`, `director.runtime`,
`roles.adapters`, KernelOne receipts, Run Ledger, QA, target-project code, or any
Bench surface. It must not write `RECEIPT_COMMITTED`, recovery, parent-close, or
terminal-settlement facts.

The shared worktree already contains reviewed DEO-1C and governance edits.
Workers must not commit, reset, restore, or reformat unrelated files. Each task
ends with a diff checkpoint; the main agent owns final integration and any later
commit decision.

All backend Python/test/static commands below run with tool workdir
`/home/dains/Documents/polaris/src/backend`. Git commands may run from that
subdirectory because it belongs to the same repository.

## File structure

- Modify `src/backend/polaris/cells/runtime/task_runtime/public/contracts.py`
  for inventory input/member/result contracts, commands, query, and claim grant.
- Modify `src/backend/polaris/cells/runtime/task_runtime/public/service.py` for
  typed public seal/finalize/query entry points and claim result propagation.
- Modify `src/backend/polaris/cells/runtime/task_runtime/public/__init__.py` for
  the exact new public exports.
- Modify
  `src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py`
  for strict registry facts, guarded ready proof, inventory-bound admission,
  ready-bound claim/abort, and fresh-claim grant construction.
- Create
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py`
  for DEO-2A behavior and corrupt-stream cases.
- Modify
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py`
  only to route existing mutation fixtures through seal/admit/ready; existing
  semantic assertions remain unchanged.
- Modify
  `src/backend/polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py`
  for no-DEO-3 and public-writer call-shape fences.
- Modify
  `src/backend/polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_concurrency.py`
  for seal/finalize/claim CAS interleavings.
- Modify TaskRuntime `cell.yaml`, `README.agent.md`,
  `generated/context.pack.json`, and `src/backend/docs/graph/catalog/cells.yaml`
  only after code gates pass.

## Locked public names

The implementation uses these exact names throughout all tasks:

```python
DirectedEffectInventoryEffectTypeV1 = Literal["write", "async"]
DirectedEffectInventoryExecutionModeV1 = Literal["write_serial", "async_receipt"]
DirectedEffectInventoryContingencyKindV1 = Literal["forward", "rollback"]

DirectedEffectInventoryIntentV1
DirectedEffectInventoryMemberV1
DirectedEffectInventoryProjectionV1
DirectedEffectInventoryResultV1
DirectedEffectClaimGrantV1

SealDirectedEffectInventoryCommandV1
FinalizeDirectedEffectInventoryAdmissionCommandV1
GetDirectedEffectInventoryQueryV1

seal_directed_effect_inventory
finalize_directed_effect_inventory_admission
get_directed_effect_inventory
```

The schema constants are:

```python
DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1 = "task-runtime.directed-effect-inventory-intent/1"
DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1 = "task-runtime.directed-effect-inventory-member/1"
DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1 = "task-runtime.directed-effect-inventory-projection/1"
DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1 = "task-runtime.directed-effect-claim-grant/1"
```

### Task 1: Lock contract shape with failing tests

**Files:**

- Create:
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py`
- Test:
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py`

- [x] **Step 1: Add contract-construction tests**

Use the existing `_attempt`, `_admit_parent`, and explicit enrollment fixture
patterns from `test_directed_effect_operation.py`. Add local helpers with exact
mutation input records:

```python
def _intent(*, ordinal: int = 0, call_id: str = "call-1") -> DirectedEffectInventoryIntentV1:
    return DirectedEffectInventoryIntentV1(
        ordinal=ordinal,
        tool_call_id=call_id,
        normalized_tool_name="write_file",
        effect_type="write",
        execution_mode="write_serial",
        intended_effect_fingerprint="1" * 64,
        policy_verdict_hash="2" * 64,
        expected_receipt_binding_hash="3" * 64,
        contingency_kind=None,
    )
```

Assert frozen behavior, exact `to_record()` keys, canonical SHA-256 validation,
effect/mode pairing, ordinal validation, and deep tuple detachment.

- [x] **Step 2: Add command boundary tests**

Construct `SealDirectedEffectInventoryCommandV1` with one to 64 ordered unique
intents. Assert rejection for zero members, 65 members, non-contiguous ordinals,
duplicate `tool_call_id`, read effects, write/async mode mismatch, caller-supplied
effect identity, wrong attempt type, wrong parent type, and every seal operation
head other than zero.

- [x] **Step 3: Run the test and prove RED**

Run:

```bash
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py
```

Expected: collection fails because the locked public contracts do not exist.

- [x] **Step 4: Record the file boundary**

Run:

```bash
rtk proxy git status --short
rtk proxy git diff --check
```

Expected: only the new test plus pre-existing shared changes; diff check passes.

### Task 2: Implement immutable inventory and grant contracts

**Files:**

- Modify: `src/backend/polaris/cells/runtime/task_runtime/public/contracts.py`
- Modify: `src/backend/polaris/cells/runtime/task_runtime/public/__init__.py`
- Test:
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py`

- [x] **Step 1: Add canonical hash and effect/mode validators**

Add a private validator that accepts only lowercase 64-character SHA-256 hex.
Apply it to the three semantic hashes, `inventory_hash`, and `grant_hash`. Map
`write -> write_serial` and `async -> async_receipt`; reject every other pair.

```python
def _directed_effect_sha256(name: str, value: str) -> str:
    normalized = _directed_effect_token(name, value)
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return normalized
```

- [x] **Step 2: Add input and durable member types**

`DirectedEffectInventoryIntentV1` contains the helper fields from Task 1 and no
`effect_id` or `operation_id`. `contingency_kind` is optional for ordinary
model-originated effects and explicitly `forward`/`rollback` only for a frozen
repair plan. `DirectedEffectInventoryMemberV1` contains the same fields plus
server-derived `effect_id` and `operation_id`. Both are frozen,
slotted, exact-record dataclasses. `from_record()` rejects missing, extra, or
wrongly typed fields before construction.

```python
@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryMemberV1:
    ordinal: int
    tool_call_id: str
    effect_id: str
    operation_id: str
    normalized_tool_name: str
    effect_type: DirectedEffectInventoryEffectTypeV1
    execution_mode: DirectedEffectInventoryExecutionModeV1
    intended_effect_fingerprint: str
    policy_verdict_hash: str
    expected_receipt_binding_hash: str
    contingency_kind: DirectedEffectInventoryContingencyKindV1 | None = None
    schema_version: str = DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1
```

- [x] **Step 3: Add seal/finalize/query commands**

All commands carry `workspace`, `task_id`, the full typed execution attempt, and
the durable parent binding. Seal additionally carries the immutable intent
tuple, registry `expected_registry_version`/`expected_registry_seq`, and
`expected_operation_head_seq`, which must be zero. Finalize additionally carries
the exact `inventory_hash`, registry `expected_registry_version`/
`expected_registry_seq`, and `expected_operation_head_seq`. Query has no
expected CAS fields. All validate
workspace/task identity against the attempt and binding.

```python
@dataclass(frozen=True, slots=True)
class SealDirectedEffectInventoryCommandV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    intents: tuple[DirectedEffectInventoryIntentV1, ...]
    expected_registry_version: int
    expected_registry_seq: int
    expected_operation_head_seq: int = 0
    actor: str = "roles.kernel"

@dataclass(frozen=True, slots=True)
class FinalizeDirectedEffectInventoryAdmissionCommandV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    inventory_hash: str
    expected_registry_version: int
    expected_registry_seq: int
    expected_operation_head_seq: int
    actor: str = "roles.kernel"
```

- [x] **Step 4: Add projection, result, and grant**

The projection contains the exact parent/attempt identity, ordered member tuple,
inventory hash, sealed event/seq, registry and operation heads, ready event/seq,
admitted count, missing operation ids, and unexpected operation ids. Successful
seal/finalize/query codes require a projection. The grant embeds the complete
attempt, parent binding, operation identity, inventory member/hash, operation
version, claim event id/seq, both stream heads, and grant hash.

`DirectedEffectOperationResultV1` gains
`claim_grant: DirectedEffectClaimGrantV1 | None = None`. Only
`code == "effect_claimed"` may carry it and that success code must carry it.
`idempotent_replay` must never carry it.

- [x] **Step 5: Export every locked name**

Update both import and `__all__` lists in `public/__init__.py` and
`public/contracts.py`; do not export private hash/build helpers.

- [x] **Step 6: Run contract tests**

Run:

```bash
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py
rtk proxy python -m ruff check polaris/cells/runtime/task_runtime/public/contracts.py polaris/cells/runtime/task_runtime/public/__init__.py
```

Expected: contract-only tests pass; repository behavior tests added next remain
absent rather than skipped.

### Task 3: Persist and strictly reduce the sealed inventory

**Files:**

- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py`
- Modify: `src/backend/polaris/cells/runtime/task_runtime/public/service.py`
- Test:
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py`

- [x] **Step 1: Add failing seal tests**

Test explicit registry/operation enrollment, parent admission, one successful
seal, exact idempotent replay, changed-member conflict, stale registry CAS,
historical/closed parent rejection, and zero operation-stream writes. The
successful member must prove TaskRuntime—not the caller—derived `effect_id` and
`operation_id` from parent binding, call id, fingerprint, and member schema.

- [x] **Step 2: Add internal immutable registry records**

Add `_SealedDirectedEffectInventory` and `_ReadyDirectedEffectInventory`.
Extend `_ParentRegistry` with maps keyed by `binding_id`; preserve those maps in
every parent-admitted/closed reconstruction. Initial registry construction uses
empty maps.

Use exact event types:

```python
_PARENT_INVENTORY_SEALED_EVENT_TYPE = (
    "task_runtime.directed_effect_parent_registry.v1.parent_inventory_sealed"
)
_PARENT_INVENTORY_READY_EVENT_TYPE = (
    "task_runtime.directed_effect_parent_registry.v1.parent_inventory_ready"
)
```

- [x] **Step 3: Implement strict sealed-event parsing**

Extend `_apply_registry_event`. The sealed payload exact fields are schema,
stable registry identity, previous/version, parent sequence, binding id,
ordered member records, member count, inventory hash, actor, and timezone-aware
`recorded_at`. Validate registry identity, open binding, monotonic seq/version,
one seal per binding, member limit/order/uniqueness, server-derived identities,
and canonical inventory hash. Any drift is strict-stream corruption or an
existing typed identity conflict; no partial projection is returned.

- [x] **Step 4: Implement `seal_inventory`**

Add `DirectedEffectOperationRepository.seal_inventory(command)`. It validates
the complete attempt and durable open binding, derives members, checks exact
replay before CAS, then uses `append_if_guarded_snapshot` with registry target
and operation-stream guard at head zero. It rereads both streams strictly and
confirms event id/seq/hash. Changed semantics return
`inventory_seal_conflict`; the method never enrolls streams and never writes an
operation fact.

- [x] **Step 5: Add the public seal entry point**

```python
def seal_directed_effect_inventory(
    command: SealDirectedEffectInventoryCommandV1,
) -> DirectedEffectInventoryResultV1:
    if not isinstance(command, SealDirectedEffectInventoryCommandV1):
        raise TypeError("command must be SealDirectedEffectInventoryCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return _inventory_failure(failure) if failure is not None else repository.seal_inventory(command)
```

Expand `_directed_effect_authority_failure`'s union; `_inventory_failure`
copies only typed code/evidence and never manufactures a projection.

- [x] **Step 6: Run seal tests and diff checkpoint**

Run:

```bash
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py -k 'seal or contract'
rtk proxy git diff --check
```

Expected: all selected tests pass; no operation event is written by sealing.

### Task 4: Bind admission and ready proof to the sealed set

**Files:**

- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py`
- Modify: `src/backend/polaris/cells/runtime/task_runtime/public/service.py`
- Test:
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py`
- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py`

- [x] **Step 1: Add failing admission/ready tests**

Cover admission before seal, seal against a non-empty operation stream, unknown
call/effect, semantic-hash mismatch, partial admission, unexpected admission,
duplicate call identity, wrong state, stale operation head, exact finalize
replay, and successful ready proof. Each failure asserts no registry append and
no claim/abort append.

- [x] **Step 2: Enforce sealed membership on `admit`**

In `_mutate`, after strict parent reconstruction and before replay/append,
require the open binding's sealed inventory for `kind == "admit"`. Locate by
exact `(tool_call_id, effect_id, operation_id)` and compare all three semantic
hashes. Missing seal/member returns `inventory_not_sealed` or
`inventory_member_not_found`; mismatch returns `inventory_member_conflict`.

- [x] **Step 3: Implement guarded readiness finalization**

`finalize_inventory` reads a guarded snapshot with parent registry as target
and operation stream as guard. Strictly reduce the registry and the entire
operation stream. Require exactly one `INTENT_COMMITTED` admission per sealed
member, no extra operation, current operation head equal to the command's
expected head, and member count no greater than 64. Append one
`parent_inventory_ready` event with inventory hash, ordered operation ids,
admission-set hash, and guarded operation head. Reread both streams and confirm
the exact fact before returning `inventory_ready`.

- [x] **Step 4: Strictly parse ready facts**

The ready reducer validates monotonic registry seq/version, existing seal,
matching open binding/inventory hash/member order, canonical admission-set hash,
non-negative operation head, one ready fact per binding, and exact payload
shape. Structural or semantic drift fails closed.

- [x] **Step 5: Implement query and public wrappers**

`get_inventory` strictly reads both streams and returns the sealed members,
ready state, admitted count, missing ids, unexpected ids, and both current
heads. Add public `finalize_directed_effect_inventory_admission` and
`get_directed_effect_inventory` with exact command/query type checks.

- [x] **Step 6: Update existing operation fixtures**

Change only the shared setup path used by admit/claim/abort tests: enroll,
admit parent, seal all test operations, admit every member using the returned
server-derived effect ids, finalize ready, then run the original transition.
Tests specifically exercising pre-seal/pre-ready denial use local setup and do
not bypass the new enforcement.

- [x] **Step 7: Run focused transition tests**

Run:

```bash
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py
```

Expected: all tests pass; DEO-1 semantic drift, replay, corruption, bounded
stream, and readiness diagnostics remain green.

### Task 5: Issue a fresh-claim-only grant and enforce ready claim/abort

**Files:**

- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py`
- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/public/contracts.py`
- Test:
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py`
- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py`
- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_concurrency.py`

- [x] **Step 1: Add failing grant tests**

Assert claim and abort fail before ready. After ready, assert a fresh claim
returns one grant matching attempt, binding, member, operation, inventory hash,
semantic hashes, version, event id/seq, registry head, and operation head.
Assert grant and nested records are frozen/detached. Repeat the exact claim and
assert `idempotent_replay` with `claim_grant is None` and no append.

Run real thread and process exact-claim races. Each race must persist one claim
fact and return exactly one `effect_claimed` result with a grant plus one typed
`idempotent_replay` without a grant. The append ownership key uses a call-local
nonce that remains stable for one invocation's bounded retries and ambiguous
reconciliation; it is not part of the durable event payload or public grant.

- [x] **Step 2: Validate the durable ready prefix on every claim/abort**

For `kind in {"claim", "abort"}`, require a ready fact. Strictly reduce the
operation prefix ending at the ready fact's recorded head and prove it is the
exact all-`INTENT_COMMITTED` sealed set. Then reduce the full stream for current
state. A forged ready payload, corrupted prefix, changed inventory, or missing
member fails before append.

- [x] **Step 3: Build the grant only after confirmed fresh claim**

Construct the canonical hash over the grant record excluding `grant_hash`.
Call this builder only from the confirmed mutation result for
`kind == "claim"` and `code == "effect_claimed"`. Same-call ambiguous append
reconciliation may return the grant after exact confirmation. `_replay_result`
never calls the builder.

Tighten `DirectedEffectOperationResultV1`: every `effect_claimed` result must
carry exactly one `DirectedEffectClaimGrantV1`, and no other result code may
carry one. The Task 2 staging comment/optional allowance is removed here.

```python
if kind == "claim":
    grant = self._claim_grant(
        command=cast(ClaimDirectedEffectCommandV1, command),
        projection=projection,
        transition=transition,
        inventory=ready_inventory,
        member=inventory_member,
    )
else:
    grant = None
```

- [x] **Step 4: Run grant and corruption tests**

Run:

```bash
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py -k 'claim or abort or ready or corrupt or replay'
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_concurrency.py
```

Expected: all selected tests pass and executor code is not imported or called.

### Task 6: Add architecture fences and prove no DEO-3 widening

**Files:**

- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py`
- Test:
  `src/backend/polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py`
- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_concurrency.py`

- [ ] **Step 1: Add call/import fences**

Assert only TaskRuntime public service calls `seal_inventory` and
`finalize_inventory`; no roles, adapters, KernelOne, Run Ledger, QA, or Bench
imports exist in TaskRuntime production files. Assert the repository does not
enroll streams implicitly and the new facts use only FactStream public guarded
or strict APIs.

- [ ] **Step 2: Add state-transition fences**

AST/source assertions reject new writers for `RECEIPT_COMMITTED`,
`RECOVERY_PENDING`, `CLOSED_BY_PARENT`, `DEAD_LETTER`, parent close, terminal
settlement, receipt persistence, or readiness enforcement outside the two new
inventory facts and existing admit/claim/abort transitions.

- [ ] **Step 3: Run the fence suite**

Run:

```bash
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py
```

Expected: all fences pass with no exemption list.

- [ ] **Step 4: Run real CAS interleavings**

Add and run real-thread tests for two exact/different seal attempts, the final
admission racing readiness finalize, and claim racing abort. Valid outcomes are
one canonical seal, finalize retry or typed head drift with no false ready fact,
and exactly one claim-or-abort transition. A fresh claim grant may exist only on
the winning in-flight claim call.

Run:

```bash
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_concurrency.py
```

Expected: all concurrency tests pass with one durable winner per transition.

### Task 7: Run 2A gates and synchronize Cell metadata

**Files:**

- Modify: `src/backend/polaris/cells/runtime/task_runtime/cell.yaml`
- Modify: `src/backend/polaris/cells/runtime/task_runtime/README.agent.md`
- Modify:
  `src/backend/polaris/cells/runtime/task_runtime/generated/context.pack.json`
- Modify: `src/backend/docs/graph/catalog/cells.yaml`
- Modify:
  `src/backend/docs/blueprints/DIRECTED_EFFECT_OPERATION_DEO2_BLUEPRINT_20260716.md`

- [ ] **Step 1: Run focused and full TaskRuntime tests**

Run:

```bash
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_inventory.py polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py
rtk proxy python -m pytest -q polaris/cells/runtime/task_runtime
```

Expected: zero failures. Record exact pass counts; do not reuse earlier counts.

- [ ] **Step 2: Run static gates**

Run:

```bash
rtk proxy python -m ruff check polaris/cells/runtime/task_runtime/public/contracts.py polaris/cells/runtime/task_runtime/public/service.py polaris/cells/runtime/task_runtime/public/__init__.py polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py
rtk proxy python -m ruff format --check polaris/cells/runtime/task_runtime/public/contracts.py polaris/cells/runtime/task_runtime/public/service.py polaris/cells/runtime/task_runtime/public/__init__.py polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py
rtk proxy python -m mypy polaris/cells/runtime/task_runtime/public/contracts.py polaris/cells/runtime/task_runtime/public/service.py polaris/cells/runtime/task_runtime/public/__init__.py polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py
rtk proxy python -m compileall -q polaris/cells/runtime/task_runtime/public polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py
rtk proxy git diff --check
```

Expected: every command exits zero.

- [ ] **Step 3: Synchronize metadata after green code**

Add the exact new contracts/services/facts and `events.fact_stream`-only
dependency to TaskRuntime metadata. Record that claim grant replay is forbidden,
readiness now gates claim/abort only, DEO-3 remains absent, DEO-2B is next, and
Bench remains `not_schedulable`. Preserve UTF-8 and valid JSON/YAML.

- [ ] **Step 4: Validate metadata and governance**

Run:

```bash
rtk proxy python -m json.tool polaris/cells/runtime/task_runtime/generated/context.pack.json
rtk proxy python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace ../.. --mode hard-fail
rtk proxy git diff --check
rtk proxy git status --short
```

Expected: valid JSON, governance exit zero with no new issues/mismatches, and no
files outside the authorized list plus pre-existing shared changes.

## 2A exit evidence

DEO-2A can close only when all of these are current:

- sealed inventory is durable, immutable, bounded, and server-identifies every
  effect/operation;
- admission outside the sealed set is impossible;
- ready fact proves the exact all-intent set under an operation-head guard;
- claim and abort fail before ready;
- a fresh confirmed claim returns exactly one frozen grant;
- exact claim replay returns no grant and performs no append;
- strict corruption, crash-reconciliation, concurrency, and replay tests pass;
- no DEO-3 state writer, external Cell dependency, target-project change, or
  Bench run exists;
- independent specification and code-quality reviews both clear the diff.

After closure, only DEO-2B becomes schedulable. DEO-2C/2D, DEO-3/4, pre-bench,
and Bench remain `not_schedulable`.
