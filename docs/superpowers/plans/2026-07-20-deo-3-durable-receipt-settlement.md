# DEO-3 Durable Receipt Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every canonical physical effect durably receipt-bound and make TaskRuntime terminal settlement recoverably close the exact directed-effect parent before session terminalization.

**Architecture:** TaskRuntime owns schema-v3 child receipt/recovery facts and schema-v2 outcome-bound parent close facts over existing guarded single-target FactStream CAS. The Director mutation port commits the receipt before returning success; canonical settlement persists one pending intent, closes eligible children, guarded-closes the parent, then persists the terminal session. Run Ledger only projects these facts.

**Tech Stack:** Python 3.12, TaskRuntime, FactStream guarded append, roles.kernel directed-effect lifecycle, roles.adapters mutation port, Run Ledger projection, pytest, Ruff, mypy.

---

## Scope lock

- DEO-3 owns durable receipt commit, finite recovery, outcome-bound parent close,
  pending terminal intent, heartbeat/reclaim fence, and Run Ledger projection.
- DEO-4 owns legacy seam removal after this protocol is proven.
- Provider calls, Bench, target-project edits, cross-stream transactions, a new
  receipt database, and blind effect retry are forbidden.

## File map

- `polaris/cells/runtime/task_runtime/public/contracts.py`: typed receipt,
  recovery, dead-letter, settlement, and heartbeat contracts.
- `polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py`:
  strict schema-v3 reducer/writers and outcome-bound parent close.
- `polaris/cells/runtime/task_runtime/internal/execution_session.py`: canonical
  pending-terminal-intent helpers.
- `polaris/cells/runtime/task_runtime/internal/service.py`: terminal ordering,
  replay, heartbeat, and stale-reclaim fence.
- `polaris/cells/runtime/task_runtime/public/service.py` and public exports:
  non-terminal receipt/recovery services only.
- `polaris/cells/roles/adapters/internal/director/directed_effect_mutation_port.py`:
  physical receipt commit before success.
- `polaris/cells/control_plane/run_ledger/public/tool_lifecycle.py`: receipt
  outcome projection without authority.
- TaskRuntime, adapter, Run Ledger, concurrency, and architecture tests listed
  in Tasks 1-8.

### Task 1: Freeze contracts and prove RED

- [x] Add strict tests constructing `CommitDirectedEffectReceiptCommandV1`,
  `MarkDirectedEffectRecoveryPendingCommandV1`, and
  `DeadLetterDirectedEffectOperationCommandV1`. Assert noncanonical SHA-256,
  empty refs/reasons, wrong typed authority, and unsupported outcomes fail.
- [x] Run the selected public contract tests. Expected: import/attribute failure
  because DEO-3 contracts do not exist.
- [x] Add the minimal immutable contracts, operation result codes, settlement
  codes, heartbeat code, exports, and public wrapper type checks.
- [x] Re-run selected tests. Expected: PASS.

### Task 2: Add strict receipt/recovery operation transitions

- [x] Write reducer and service tests for:
  `EFFECT_STARTED -> RECEIPT_COMMITTED`,
  `EFFECT_STARTED -> RECOVERY_PENDING`,
  `RECOVERY_PENDING -> RECEIPT_COMMITTED`, and
  `RECOVERY_PENDING -> DEAD_LETTER`.
- [x] Verify RED: the repository has only `admit|claim|abort` writers.
- [x] Add schema-v3 exact descriptors and reducer compatibility. Preserve
  schema-v2 reads. Store receipt outcome/ref/hash/binding and recovery evidence
  in the internal aggregate; reject any semantic drift.
- [x] Add guarded repository methods using the existing three-attempt
  reprepare/reconcile loop. Exact replay returns `idempotent_replay`; it never
  issues a claim grant or invokes an effect.
- [x] Re-run operation, inventory, and guarded-fence tests. Expected: PASS.

### Task 3: Bind the canonical mutation port to receipt commit

- [x] Add adapter RED tests with a real TaskRuntime operation fixture. Assert
  physical success cannot return `executed` while the operation remains
  `EFFECT_STARTED`; physical/receipt ambiguity becomes `RECOVERY_PENDING`.
- [x] Verify RED against the current non-durable `effect_receipt` projection.
- [x] After physical execution, build the existing hash-bound physical receipt,
  call `commit_directed_effect_receipt`, and require the exact operation,
  `RECEIPT_COMMITTED` state, receipt hash, and binding hash before returning
  success. Embed detached TaskRuntime commit evidence in the tool result.
- [x] On post-effect failure, call `mark_directed_effect_recovery_pending`; do
  not execute the physical tool again. Return typed failure when durable receipt
  proof is absent.
- [x] Re-run adapter, roles.kernel lifecycle, and production-wiring tests.

### Task 4: Persist and fence pending terminal intent

- [x] Add execution-session RED tests for canonical intent hashing, exact replay,
  different outcome/summary/metadata conflicts, and UTF-8 summary hashing.
- [x] Add `pending_terminal_intent` helpers that persist only detached canonical
  hashes and the requested outcome. Preserve the record after terminalization.
- [x] Add heartbeat RED test and implement
  `terminal_fence_pending` refusal before lease renewal.
- [x] Add stale-reclaim RED test and reject a pending terminal intent before any
  suspension write or projection.

### Task 5: Close children and parent with guarded CAS

- [x] Add RED tests with sealed/ready inventories covering all child states.
  Settlement must block unresolved states, allow `ABORTED`, close successful or
  failed receipts for compatible outcomes, and allow `DEAD_LETTER` only for
  `failed|suspended`.
- [x] Add `RECEIPT_COMMITTED -> CLOSED_BY_PARENT` schema-v3 transitions binding
  terminal-intent hash and outcome. Each append targets the operation stream and
  guards the OPEN registry head.
- [x] Add schema-v2 parent close payload with intent/outcome, exact guarded
  operation head, receipt summary hash, and counts. Append to the registry while
  guarding the operation head; strictly re-read before success.
- [x] Keep schema-v1 historical closes readable but classify them as
  `CLOSED_WITHOUT_OUTCOME_PROOF`.
- [x] Re-run parent registry/readiness/inventory suites.

### Task 6: Make canonical settlement crash-recoverable

- [x] Add deterministic crash seams and RED tests after intent write, after one
  child close, after parent close, and after terminal session write.
- [x] Replace the old read-only pre-barrier inside
  `_settle_execution_attempt_locked` with: persist/replay intent, close/replay
  DEO parent, then mark/write terminal session. No TaskBoard/Run Ledger write
  occurs while session locks are held.
- [x] Verify identical command replay finishes without duplicate child, parent,
  session, or effect facts. Different terminal command fails closed.
- [x] Add thread/process races for receipt commit versus child close, child close
  versus parent close, heartbeat versus intent, and reclaim versus settlement.

### Task 7: Project receipt outcomes to Run Ledger

- [x] Add RED tests proving no receipt is missing evidence, failed receipt is
  present-but-failed evidence, and successful receipt is present-successful.
- [x] Extend receipt extraction to prefer the TaskRuntime durable commit record
  nested in the physical effect receipt. Recovery/dead-letter remains blocking.
- [x] Verify settlement barrier, tool lifecycle, public Run Ledger, QA verdict,
  and Factory run-ledger compatibility tests. Run Ledger must perform no
  TaskRuntime write or parent close.

### Task 8: Architecture, broad gates, and closure

- [x] Add AST fences proving only TaskRuntime writes DEO receipt/recovery/close
  facts; parent close has no public standalone command; Run Ledger imports no
  TaskRuntime internal module; mutation port uses only TaskRuntime public API.
- [x] Run full TaskRuntime, roles.kernel, roles.adapters, Director Runtime,
  Run Ledger, QA, KernelOne guarded FS/FactStream, and architecture suites.
- [x] Run Ruff check/format, mypy, compileall, YAML/catalog hard-fail, public
  import smoke, and `git diff --check`.
- [x] Obtain independent specification and quality/security review with zero
  Critical/Important findings.
- [x] Synchronize blueprint, gap ledger, Cell metadata, graph catalog,
  verification card, and `memory/MEMORY.md`; close only DEO-3 and schedule only
  DEO-4. Provider/Bench remain zero and `not_schedulable`.

## Self-review

- Every design requirement maps to Tasks 1-8.
- No placeholder, cross-stream transaction, second SSoT, or blind retry exists.
- Public surface adds only non-terminal receipt/recovery operations.
- Terminal authority remains one TaskRuntime settlement command.
- Receipt parsing is shared by Run Ledger and tool lifecycle and rejects
  malformed hashes, padded schema/id/tool values, wrong flag/outcome types,
  non-canonical versions, extra fields, and non-finite values fail closed.
- The six legal `ABORTED|DEAD_LETTER` settlement/outcome combinations are
  frozen; illegal combinations remain blocked.
- Independent specification and quality/security reviews are `CLEAR/CLEAR`
  with zero Critical and zero Important findings.
- Three recovery-path C901 reports (11/11/13) remain non-blocking Minor debt;
  they do not duplicate authority or widen the durable writer set.
- Final evidence includes TaskRuntime internal/public `409 + 486`, Run Ledger
  `311`, and repository architecture `1402 passed, 8 skipped`; the architecture
  rerun includes the refactor-aware cooperative session-file-lock dominance
  fence. Ruff, format, strict mypy over nine source files, compileall, YAML,
  public import smoke, catalog hard-fail (`0/0`), and scoped diff check pass.
- DEO-3 is closed. DEO-4 is the only active bucket; pre-bench, Provider, and
  Bench remain `not_schedulable` with zero calls/runs/effects claimed here.
