# GR3B — Durable Managed Process Receipt Spine

Status: implementation_in_progress
Owner: `runtime.execution_broker` composed with `runtime.task_runtime` and
`control_plane.run_ledger`

## Problem

`runtime.execution_broker` currently owns only loop-local process handles:
launch, wait, terminate, and cancel.  It has no durable attempt lease,
directed-effect claim, process receipt, recovery state, or typed Run Ledger
projection.  A process can therefore be observed without becoming an
auditable TaskRuntime fact.  Conversely, Factory must not manufacture
TaskBoundary completion from a file-count heuristic.

## Authority boundaries

```text
TaskRuntime      : attempt identity, lease, directed-effect claim/receipt,
                   recovery and dead-letter are durable authority
ExecutionBroker  : OS process handle and bounded lifecycle only; never task outcome
RunLedger        : durable control-plane projection/envelope, never process owner
VerifierPolicy   : compiled verifier policy authority
VerifierExecution: executes only a hash-bound compiled policy (future GR4 consumer)
Factory          : workspace/run/stage lease and settlement consumer only
```

No component may replace another component's state with a convenience DTO.
In particular, a managed command may not carry caller-computed `passed`,
`completed`, `missing`, `failed`, or gate-policy verdicts.

## Command and composition

The public managed-process command binds only:

- a validated exact TaskRuntime attempt identity;
- an admitted parent/directed-effect correlation, intent/policy hashes, and
  directed-effect `expected_version` / `expected_seq` CAS preconditions;
- command/arguments/environment/log policy references needed to create a
  canonical process receipt.

It must never accept a caller-supplied claim grant or grant hash.  The exact
`DirectedEffectClaimGrantV1` and its hash are produced only by a successful
`claim_directed_effect` call.  A heartbeat may renew `lease_expires_at`; the
managed path must use the latest authority identity for receipt/recovery while
keeping the immutable claim-time grant CAS heads for effect receipt commit.

Bootstrap composes public Cell ports exactly once.  The broker must not import
Factory, TaskRuntime internal modules, TaskBoundary builders, or generic Run
Ledger event appenders.  TaskRuntime is queried/committed only through its
validated public API.  The full canonical receipt body is first persisted by
an idempotent, content-addressed `ManagedProcessReceiptStorePortV1` owned by
`audit.evidence`; only then may TaskRuntime receive its receipt ref/hash, and
only a committed receipt may enter a typed Run Ledger lifecycle projection.

## Flow

```text
managed command
  -> validate TaskRuntime attempt + lease + directed-effect CAS preconditions
  -> claim registered directed effect (returns exact grant)
  -> primitive broker launch with primitive timeout disabled
  -> bounded wait + periodic heartbeat
  -> explicit terminate on timeout / heartbeat loss
  -> content-address persist canonical process receipt
  -> TaskRuntime succeeded or failed effect-receipt commit using latest lease
     identity and claim-time grant CAS heads
  -> typed Run Ledger projection of committed receipt
```

Canonical receipt binds execution identity, grant, intent/policy, normalized
args/environment references, log hash, start/end timestamps, exit code, and
timeout/cancel flags.  A nonzero exit is *present failed evidence*, never
missing evidence.

## Partial-success and recovery rules

| Boundary | Required result |
| --- | --- |
| lease/grant/CAS invalid before spawn | fail closed; no child process |
| claim succeeds, launch fails | durable failed effect receipt |
| timeout or heartbeat loss | exactly one bounded terminate, then failed/recovery receipt |
| process observed, receipt commit fails | `RECOVERY_PENDING`; never respawn automatically |
| receipt committed, ledger projection fails | return typed projection-pending state; retry projection only |
| restart sees started effect | reconcile or dead-letter; do not launch a second process |

Broker does not settle the overall TaskRuntime attempt.  One attempt can own
multiple process effects, so completed effects survive a later sibling
failure, timeout, or settlement deadline.

## Explicitly excluded from GR3B

- Factory pipeline implementation and Bench gates;
- TaskRuntime internal ownership changes;
- verifier policy/execution redesign (GR4);
- Factory TaskBoundary file-count completed shortcut removal (GR3C follow-up);
- Provider calls, target-project edits, and real Bench execution.

## Verification card requirements

1. Unbound adapter, invalid/expired/mismatched attempt, grant drift, and CAS
   drift fail before spawn.
2. Timeout and heartbeat loss each terminate exactly once and produce a
   failed/recovery receipt.
3. Success commits TaskRuntime evidence then typed Run Ledger projection.
4. Failure evidence is present/failed, not missing.
5. Receipt commit failure cannot replay a process; ledger failure cannot replay
   a committed process.
6. Restart reconciliation does not duplicate launch.
7. Static architecture rejects imports of Factory, TaskBoundary completion,
   and generic Run Ledger append from the broker path; graph remains acyclic.
8. Settlement closes/release-allows a failed-evidence attempt while retaining
   `passed=false`.

## Delivery sequence

1. **B1 — ports and binding only.** Add consumer-owned managed-process port
   contracts plus bootstrap-only same-object-idempotent / conflicting-rebind
   fail-closed binding.  No process launch, receipt write, TaskRuntime commit,
   or caller migration is permitted.
2. **B2 — canonical receipt owner.** Extend `audit.evidence` through its
   public boundary with idempotent content-addressed process-receipt append and
   read-by-hash.  The broker must not gain a private store.
3. **B3 — typed Run Ledger projection.** Add one managed-process lifecycle
   append command/service.  Direct generic and tool-lifecycle appends are
   structurally rejected from the managed path.
4. **B4 — one managed execution orchestrator.** Reuse TaskRuntime public
   authority/claim/commit/recovery and the B2/B3 ports; retain primitive
   `launch_process` behavior and migrate no existing callers in this bucket.
5. **B5 — recovery and lifecycle proof.** Add duplicate-launch, termination
   once, projection-pending, and restart-reconciliation tests.  Only then may
   GR3B be independently reviewed; GR3C/GR4 remain separate.

## B1 acceptance — 2026-08-02

B1 is accepted as a partial stage only.  `runtime.execution_broker` now has a
consumer-owned, typed managed-process receipt-store port and bootstrap-only,
single-assignment binding.  The port contains no process, receipt persistence,
TaskRuntime, or Run Ledger side effect.  Same-object bind is idempotent;
conflicting bind and unbound lookup fail closed.  Generated descriptor and
Cell verification metadata are synchronized, and the architecture fence blocks
Factory, all TaskRuntime, TaskBoundary, Run Ledger, Role Kernel/adapters,
Director, and DEO imports from B1.

Independent evidence: `/tmp/polaris-subagent-gr3b-b1-r2.json` is `CLEAR` for
B1; 33 focused execution-broker tests, mypy, Ruff, formatting, and scoped
diff checks passed.  B2 remains responsible for recomputing the canonical
receipt hash and for idempotent persistence through `audit.evidence`; it must
never trust a caller-provided hash without verifying the typed receipt body.

## B2 acceptance — 2026-08-03 (independent audit CLEAR)

B2 is accepted as a **partial stage only**.  `audit.evidence` is the canonical
owner of managed-process receipt bodies:

| Invariant | Status |
| --- | --- |
| Owner recomputes UTF-8 sorted-key JSON SHA-256; does not trust caller hash | **PASS** — `claimed_receipt_hash` mismatch → `EvidenceAuditError`, no append |
| Append-only JSONL at `runtime/evidence/managed_process_receipts.jsonl` | **PASS** |
| Same canonical body idempotent (`already_present=true`, single line) | **PASS** |
| Distinct bodies → distinct hashes | **PASS** |
| Cross-workspace isolation on read-by-hash | **PASS** |
| Malformed / tampered stored line fail-closed | **PASS** |
| Public surface only (`persist_managed_process_receipt` / `read_managed_process_receipt`) | **PASS** |
| No process launch, TaskRuntime commit, Run Ledger write, Bench, or main-port use | **PASS** (B3–B5 / GR7 still open) |

Independent re-verification (this audit):

- `pytest …/test_managed_process_receipts.py` → 6 passed
- `pytest …/audit/evidence/tests/` → 45 passed
- mypy + ruff on public contracts/service → clean

**Not in B2 scope (do not treat as sealed GR3B):**

1. **No bootstrap adapter** yet maps broker `ManagedProcessReceiptStorePortV1`
   (`AppendManagedProcessReceiptCommandV1` with required `receipt_hash`) onto
   evidence `PersistManagedProcessReceiptCommandV1` (optional claim + owner
   recompute).  That adapter is **B4** composition work; B1 fake store remains
   port-shape only.
2. **B3** typed Run Ledger managed-process projection is not started.
3. **B4/B5** orchestrator, terminate-once, projection-pending, restart recovery
   are not started.
4. Process-local lock only; multi-process CAS of the JSONL store is not claimed.

**Verdict:** `CLEAR` for GR3B-**B2** only.  GR3B as a whole remains
`implementation_in_progress`.  Unattended / Bench `COMPLETED_VERIFIED` is
**not** claimed.

## B3 acceptance — 2026-08-03 (partial stage only)

B3 lands a **typed managed-process lifecycle projection** on Run Ledger:

| Invariant | Status |
| --- | --- |
| Public entry `project_managed_process_lifecycle` | **PASS** |
| Requires committed `audit.evidence` receipt identity (hash lookup) | **PASS** |
| Missing/unknown receipt fail-closed | **PASS** |
| Nonzero exit → `evidence_presence=present_failed`, `missing_evidence=false` | **PASS** |
| Generic `append_run_ledger_event` of `managed_process_lifecycle` rejected | **PASS** |
| `append_tool_call_lifecycle_event` substitute / stage smuggle rejected | **PASS** |
| No process launch, TaskRuntime claim, Factory/Bench, main ports | **PASS** (B3 scope) |

Implementation:

- `control_plane/run_ledger/public/managed_process_lifecycle.py`
- Gate in `append_run_ledger_event` / `append_tool_call_lifecycle_event`
- Tests: `tests/test_managed_process_lifecycle.py`

**Not in B3:** B4 orchestrator (claim/launch/wait/commit), B5 recovery proofs,
full GR3B seal, GR4 verifier policy, Supervisor, Bench `COMPLETED_VERIFIED`.

**Verdict:** implemented and unit-auditable for **B3 only**.  Next: B4.

## B4/B5 acceptance — 2026-08-05 (partial stage; formal L1 probe exercised)

Public orchestrator: `run_managed_process` /
`RunManagedProcessCommandV1` in
`runtime.execution_broker.public.managed_process_execution`.

| Invariant | Status |
| --- | --- |
| Authority fail-closed **before** spawn (token/lease) | **PASS** (unit) |
| Spawn → content-addressed evidence receipt → typed ledger project | **PASS** (unit + real `echo`) |
| Nonzero exit / timeout → `present_failed`, never missing | **PASS** |
| Duplicate launch refused via durable effect journal | **PASS** |
| Timeout terminate at most once | **PASS** |
| Ledger projection-pending does not re-spawn | **PASS** |
| Full TaskRuntime DEO claim/commit migrate all callers | **NOT** in this stage |
| L1–L12 COMPLETED_VERIFIED / N-batch seal | **NOT** claimed |

Formal L1-01 isolated probe (2026-08-05): ran
`run_factory_bench.py --project-ids L1-01 --launcher-instance-mode isolated`
under goal scratch; produced `factory_audits.json` with residual
`primary_module_id` (M03) and `DELIVERY_FAILED` — **honest red**, not forged
green.  Critical-path unit gates green; product four-pillars still fail on
depth/real_run — platform residual work remains outside this partial seal.
