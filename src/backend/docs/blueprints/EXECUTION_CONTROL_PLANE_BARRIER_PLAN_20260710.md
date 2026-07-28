# Execution Control Plane Barrier Plan (2026-07-10)

## Scope

This plan closes the L1-01 r06-r11 control-plane gap without reopening TS repair,
WS2 read-model work, or legacy Director helpers.

## Blind-Spot Findings

1. Factory timeout cancellation can suspend TaskRuntime leases while a Director
   turn already decoded native tool calls and is entering dispatch. The resulting
   write failures surface as `session_not_active`.
2. A decoded tool batch with only failed tool results must never be projected as
   a successful completion. It is an execution-control-plane failure.
3. TaskBoundary entrypoint validation treats `package.json` build outputs such
   as `dist/main.js` as missing source targets. Build artifacts are verifier
   outputs, not source obligations.
4. Bench attribution needs to keep upstream task-boundary failures distinct from
   downstream QA/test symptoms. Missing downstream tests after an upstream
   compile/cancel failure are not evidence that the model cannot write tests.

## Design Invariants

- `decision_received -> tool_dispatch -> effect_receipt -> task_boundary` is the
  authoritative execution chain.
- Once a run has entered the tool-dispatch settlement window, Factory timeout is
  allowed to return a timeout projection, but must not immediately invalidate the
  TaskRuntime session that tools need to settle.
- Build output prefixes (`dist/`, `build/`, `out/`, `.next/`, `.nuxt/`,
  `coverage/`) are generated artifacts. Missing files in those prefixes must not
  trigger `missing_entrypoint_target` at task-boundary time.
- Bench failure taxonomy must prefer structured TaskBoundary and repair
  convergence facts over generic `integration_qa`, `delivery_depth`, or
  `runtime_environment:event_wait_timeout` symptoms.

## Implementation Plan

1. Gate generated package entrypoints in TaskBoundary.
2. Preserve/verify tool-batch all-failed fail-closed behavior.
3. Add Factory timeout barrier metadata and avoid session suspension when the
   timeout path is a tool-settlement barrier.
4. Strengthen bench taxonomy with structured `session_not_active`,
   `tool_dispatch_failed`, `repair_convergence`, and `task_boundary`
   attribution.
5. Validate with targeted unit tests, `py_compile`, Ruff, and a lightweight
   taxonomy smoke script where optional factory dependencies are unavailable.

## Implemented Decisions

1. TaskBoundary now treats generated package entrypoints under `dist/`,
   `build/`, `out/`, `.next/`, `.nuxt/`, and `coverage/` as verifier/build
   outputs rather than source-target obligations.
2. Director timeout settlement now checks the terminal run status first when a
   factory cancel signal is present. If the child run is still non-terminal and
   TaskRuntime reports active execution, the stage returns a barrier projection
   with `inflight_run_continues=true` and `cancel_signal_sent=false` instead of
   suspending the child lease.
3. Bench taxonomy now recognizes `session_not_active` and
   all-failed tool batches as `control_plane` failures before generic runtime or
   LLM-output classification.
4. Follow-up blind-spot scan found the Director binding fanout waiter still had
   a direct cancel-event path. It now follows the same ordering as timeout
   settlement: terminal run status first, active TaskRuntime barrier second,
   ordinary cancellation last.

## Verification Notes

- `PYTHONPATH=src/backend rtk pytest src/backend/polaris/cells/control_plane/run_ledger/tests/test_task_boundary.py -q`
  passed with 28 tests.
- `PYTHONPATH=src/backend rtk pytest src/backend/polaris/cells/roles/kernel/internal/transaction/tests/test_tool_batch_executor_metadata.py -q`
  passed with 11 tests.
- Ruff check, Ruff format check, `py_compile`, and `git diff --check` passed for
  the touched files.
- `src/backend/polaris/cells/factory/pipeline/tests/test_bench_gates.py` and
  `test_factory_stage_executor_characterization.py` are still collection-blocked
  in this local environment by missing optional `numpy` from the benchmark
  holographic import chain. That is an environment/dependency isolation debt,
  not an execution-control-plane code failure, and should be handled in a
  separate bucket.

## Follow-Up Blind-Spot Scan

The second scan explicitly checked for duplicate generated-entrypoint filters,
remaining `session_not_active` producers, and fanout/direct wait divergence.
Only fanout had a remaining bypass: it cancelled submitted Director runs before
checking terminal status or active TaskRuntime execution. This is now closed
without changing global factory cancellation semantics.

## R94 Regression Addendum (2026-07-27)

R94 exposed a second authority-ordering defect in the same barrier:

- Director orchestration returned the non-authoritative lifecycle failure
  `director_no_materialized_changes`.
- The matching TaskRuntime execution fact still reported an active session with
  a fresh heartbeat and an unexpired execution lease.
- `RunCompletionWaiter.wait` returned the lifecycle failure immediately, so the
  already-admitted 1795-second execution lease was discarded and Factory later
  observed only the five-second settlement reserve.

The lifecycle result is a hint; TaskRuntime remains the execution owner. The
minimal correction is therefore not a larger settlement constant or a new
Provider retry. While the same run has an active canonical TaskRuntime row,
`RunCompletionWaiter` must keep the original admitted execution deadline,
await a canonical terminal fact, and refuse a second Director dispatch. Only
after TaskRuntime becomes terminal may the fixed settlement projection window
apply. A lifecycle failure remains terminal when no matching active
TaskRuntime fact exists.

Required regression proof:

1. an early orchestration failure plus active TaskRuntime fact does not return
   early;
2. the waiter returns the later canonical TaskRuntime terminal result within
   the original execution lease;
3. a lifecycle failure without an active TaskRuntime fact remains fail-closed;
4. no timeout constant, target-project code, Provider retry, or TaskRuntime
   lease mutation is introduced;
5. an explicit Factory cancel retains its fixed settlement window, does not
   mutate the active TaskRuntime child, and cannot be overwritten by lifecycle
   failure handling or active progress.

## R113 CE Advisory Scope Addendum (2026-07-27)

R113 reached the physical DeepSeek transport twice with qualified final
Provider requests. Both HTTP responses were `200`, the CE identity, PM
contracts, target files, tool schema, forced result tool, token audit, and
failure feedback were present, but both submissions omitted only the top-level
`scope_for_apply` field. Stream-boundary JSON Schema validation rejected the
payload before Director projection.

`scope_for_apply` is non-authoritative advice. PM `target_files` and
`scope_paths` remain the sole apply authority, and the CE portfolio builder
already rejects every suggested path outside that authority. Treating omitted
scope advice as a fatal transport defect therefore discards a valid
construction plan without increasing safety.

The minimal correction is:

1. keep `scope_for_apply` in the Provider schema and prompt, but do not require
   the advisory field at the transport boundary;
2. keep `construction_plan` and `risk_flags` required and preserve all task-id,
   interface-contract, and type validation;
3. do not synthesize missing CE advice from PM data;
4. emit `chief_engineer.scope_advisory_omitted` with
   `pm_authority_preserved=true` and `scope_expansion_allowed=false`;
5. continue to reject a present non-array `scope_for_apply`.

Premortem: an over-broad relaxation could accept a hollow portfolio, hide model
drift, or expand authority. The retained required fields and semantic
validators prevent the first; the warning signal prevents the second; the
existing PM-authority intersection prevents the third.

Required proof:

1. a red/green stage regression using a valid CE portfolio with only
   `scope_for_apply` omitted;
2. schema assertions proving the property remains declared but optional;
3. rejection of a present invalid scope type;
4. focused Factory/CE tests, Ruff, format, mypy, and diff checks;
5. one fresh isolated R114 bench only after requalification.

## R114 Factory Snapshot Lock Addendum (2026-07-27)

R114 proved the R113 transport correction: PM and Chief Engineer completed,
three CE blueprints were projected, Director reached physical Provider calls,
and Rust source files landed. The run then failed inside Polaris persistence,
not inside the generated project:

- every query, heartbeat projection, stage commit, and quarantine terminalizer
  timed out acquiring the same `factory/<run_id>/run.json` lock;
- `FactoryStore.get_run` caught that `FileLockTimeoutError` as an `OSError` and
  projected the existing run as missing, producing false `RUN_NOT_FOUND`/404;
- Director work continued while the authoritative run snapshot became
  unobservable and uncommittable.

The lock inversion is structural. `_acquire_file_lock` acquires a
`threading.Lock` in the default executor, returns to the event loop while still
holding it, then the protected read/write is submitted as a second
`asyncio.to_thread` job to the same executor. Concurrent run queries can occupy
all executor workers waiting for that lock while the lock owner waits for its
queued I/O job. Increasing the five-second timeout only prolongs the
self-deadlock.

The minimal correction is:

1. execute lock acquisition, protected synchronous file operation, and release
   in one worker callback; never hold the per-file lock across an async await;
2. preserve one cross-loop lock identity per resolved file path and atomic
   replace semantics;
3. propagate `FileLockTimeoutError` as explicit contention; never convert it to
   a missing/corrupt run snapshot;
4. keep corrupt UTF-8/JSON handling fail-closed and distinct from contention;
5. do not change Provider, Director, target-project, settlement, or timeout
   policy.

Premortem: a partial fix could remove the deadlock but introduce concurrent
write races, cancellation leaks, or false 404s. Required proof:

1. a deterministic small-executor red/green regression reproducing the lock
   owner queued behind same-lock waiters;
2. cancellation proof that the worker completes/release occurs even if its
   awaiting coroutine is cancelled;
3. concurrent read/write integrity and atomic JSON proof;
4. explicit contention propagation from `get_run`;
5. focused Factory store/stage tests, full Factory pipeline, Ruff, format,
   mypy, diff checks, release gate, independent review, and only then one fresh
   isolated R115.

### R114 Closure Evidence (2026-07-27)

The final implementation keeps lock acquisition, protected synchronous I/O,
atomic replace, and lock release in one worker callback. Caller cancellation
waits for that worker to settle and keeps `CancelledError` authoritative; a
late worker failure is retained only as its cause. Lock contention now
propagates as `FileLockTimeoutError` and the Factory HTTP projection returns
`503 FACTORY_RUN_SNAPSHOT_BUSY` instead of false `404/RUN_NOT_FOUND`.

Final proof on the exact source authorized for R115:

- Factory store/router combined regressions: `77 passed, 4 deselected`;
- Factory stage persistence: `25 passed`;
- Factory run service: `95 passed`;
- complete Factory pipeline: `1266 passed, 2 warnings`;
- Ruff check, Ruff format, mypy, and `git diff --check`: pass;
- KernelOne release gate: `ok=true`, `415 passed, 1 skipped, 2 warnings`;
- independent quality/security re-review: `CLEAR`, no Critical, Important, or
  Minor findings;
- stable source fingerprint:
  `3f7783799617aa97 == 3f7783799617aa97`.

R114 is closed and requalified. Exactly one fresh isolated R115 L1-05 run is
authorized; no additional retry is implied by this closure.

## R115 PM Capability-Scope Addendum (2026-07-27)

R115 proved the R114 persistence correction and the physical request path:
PM and Chief Engineer completed, Director received a qualified final Provider
request containing the PM contract, CE blueprint, target files, and native
`write_file`; the model returned seven native writes. No effect was applied
because DEO correctly rejected the batch with `deo_path_scope_denied`.

The failure originated upstream in the PM contract:

- `target_files` retained seven Rust mutation targets;
- `_normalize_task_contract` compacted `scope_paths` to six;
- the seventh target, `src/models/recipe.rs`, therefore had no JobToken write
  authority;
- atomic DEO preflight correctly prevented every write rather than partially
  applying an invalid batch.

The first correction exempted task-local concrete targets from the compact
scope budget. Independent review then found a second path: quality autofix can
append targets after initial normalization. The final correction therefore
reconciles every post-autofix task at the PM contract exit using the same
exact-or-parent scope semantics as DEO. It appends only uncovered targets from
that task; it does not union project targets, widen DEO, or weaken atomic
fail-closed behavior.

### R115 Closure Evidence (2026-07-27)

Final proof:

- real R115 Rust synthesis plus real Card3D post-autofix regressions: `2 passed`;
- complete PM adapter suite: `137 passed`;
- complete Roles adapters suite: `1291 passed, 2 warnings`;
- directed-effect public authorization/policy and adapter wiring: `242 passed`;
- complete Factory pipeline: `1266 passed, 2 warnings`;
- Ruff check, Ruff format, mypy, compileall, JSON/YAML parse, and
  `git diff --check`: pass;
- KernelOne release gate: `ok=true`, `415 passed, 1 skipped, 2 warnings`;
- independent review: `CLEAR`, no Critical, Important, or Minor findings;
- stable source fingerprint:
  `761ac2eca355b946 == 761ac2eca355b946`.

All four physical final Provider requests from R115 were audited: PM `1/1`,
Chief Engineer `2/2`, and Director `1/1` passed role identity, required
reference, tool-surface, and final-request token/window checks; QA was not
admitted after Director failure.

R115 is closed. It does **not** authorize R116 yet: the Rust contract/context
secondary findings already captured by R115 must be audited and closed one
bucket at a time before a fresh isolated run can be scheduled.

## R115 Deterministic-Check Section Secondary Closure (2026-07-27)

The first R115 secondary bucket is closed. The PM deterministic synthesizer
previously scanned the complete requirement and treated verifier names inside
conditional prose or Markdown examples as authoritative checks. In L1-05 this
leaked `html` from Web-only acceptance prose into a Rust CLI contract, then
faithfully propagated that malformed contract through the audited PM, Chief
Engineer, and Director final Provider requests.

The correction scopes declarations to the authoritative
`Deterministic Checks` / `确定性检查` Markdown section when present. It accepts
explicit list or independent exact-token declarations, preserves nested
subsections and the legacy no-section fallback, and excludes fenced/indented
examples plus list, blockquote, and paragraph continuations. It does not
hard-code a Rust exception and still accepts an explicitly declared `html`
check.

Closure proof on one stable source fingerprint:

- parser-focused regression matrix: `20 passed`;
- complete PM adapter suite: `156 passed`;
- complete Roles adapters suite: `1310 passed, 2 warnings`;
- complete Factory pipeline: `1266 passed, 2 warnings`;
- Ruff check/format, mypy, compileall, JSON parse, and targeted
  `git diff --check`: pass;
- KernelOne release gate: `ok=true`, `415 passed, 1 skipped, 2 warnings`;
- independent bounded CommonMark review: `CLEAR` after eleven review rounds;
- unchanged source hashes before and after the broad gates:
  `66eb0b56e0befab5`, `c85e51b6759955da`, `f3b34b7e4c06fb43`.

Machine evidence is
`scratchpad/r115-rust-contract-context/defect-record-r115-deterministic-check-section.json`.
R116 remains unauthorized while the separate Rust `tests/test_product.py`
validation-harness and ContextOS `all_ok=false` / `isolated=false` observation
is audited as its own bucket.

## R115 ContextOS Control-Plane Isolation Secondary Closure (2026-07-27)

The second R115 secondary bucket is closed. The exact Director final Provider
request exposed a complete capability and execution-attempt authority object in
its prompt context. ContextOS correctly reported `all_ok=false` and
`control_plane.isolated=false`; the defect was in request construction, not in
the audit.

The correction makes one canonical control-plane prompt taxonomy authoritative
for both prevention and audit. Context override projection now creates a
bounded recursive prompt-only copy, removes raw JobToken and attempt authority
from nested mappings, serialized strings, spelling variants, and generic
authorization envelopes, and preserves only prompt-safe references, hashes,
scope paths, commands, PM contract, CE blueprint, target files, and failure
feedback. The original override and its authority objects remain untouched for
ToolGateway and DEO. Opaque objects, hostile builtin subclasses, recursive
graphs, excessive depth/width, non-string mapping keys, and late signatures
fail closed without weakening the independent ContextOS audit.

Closure proof on the frozen source:

- focused ContextOS and gateway adversarial matrix: `74 passed`;
- gateway, transaction, and ToolGateway integration: `244 passed`;
- complete Roles Kernel suite: `2791 passed, 1 warning`;
- Ruff check/format, mypy, compileall, JSON parse, and scoped
  `git diff --check`: pass;
- exact R115 CE-blueprint replay: `message_chars=6522`, PM/CE task evidence and
  prompt-safe capability refs preserved, `ok=true`,
  `control_plane.isolated=true`, no metadata-key or content hits;
- independent quality/security review: `CLEAR`, no Critical, Important, or
  Minor findings;
- Provider calls and Bench runs after the fix: `0`.

Machine evidence is
`scratchpad/r115-rust-contract-context/defect-record-r115-context-os-capability-leak.json`.
R116 remains unauthorized while the separate Rust `tests/test_product.py`
validation harness and the unrelated KernelOne audit contracts/validators
baseline drift remain open.

## R115 KernelOne Audit Contract Baseline Secondary Closure (2026-07-27)

The third R115 secondary bucket is closed. The eight red tests were one
contract split, not eight unrelated runtime failures. Seven expectations came
from an older duplicate suite that still synthesized missing persisted audit
fields and anonymous run identities. Commit `700c09cc` had intentionally made
the persisted signed envelope complete and fail-closed, while the canonical
unit suite already froze that contract. The remaining production inconsistency
was `normalize_event_type("")`, which silently mislabeled missing identity as
`task_start`.

The correction preserves complete persisted-envelope validation and strict
`run_id` validation, updates the stale duplicate expectations, and makes an
empty event type fail closed. Legal enum/string inputs and `None` as the
query-layer “no event-type filter” sentinel remain unchanged.

Closure proof:

- focused contract and validator suites: `123 passed`;
- complete KernelOne audit scope: `597 passed`;
- wider independent audit scope: `818 passed`;
- KernelOne release gate: `ok=true`, `415 passed, 1 skipped, 2 warnings`;
- Ruff check/format, mypy, JSON parse, and scoped `git diff --check`: pass;
- independent contract review: `CLEAR`, no Critical, Important, or Minor
  findings;
- Provider calls and Bench runs after the fix: `0`.

Machine evidence is
`scratchpad/r115-rust-contract-context/defect-record-prebench-kernelone-audit-baseline.json`.
R116 remains unauthorized only while the Rust native-validation contract awaits
its final independent review and governance synchronization.

## R115 Rust Native-Validation Contract Secondary Closure (2026-07-27)

The final R115 secondary bucket is closed. The original production PM contract
declared a Python verifier for a Rust project, while Factory and Bench reported
`cargo check` or generic `rustc` compilation as test execution. The first native
correction also ran generated binaries too close to the target workspace and
trusted zero-test or forgeable output.

The correction establishes one platform contract:

- PM declares `tests/product.rs`, `cargo test --quiet`, and read-only model /
  engine context while preserving test/README-only mutation scope;
- Factory and internal Bench gates consume the same bubblewrap-isolated,
  disposable workspace copy; the target workspace is never mounted;
- Cargo success requires at least one paired standard-libtest stdout result;
  zero tests, sandbox absence, compile failure, and contract drift fail closed;
- Cargo configuration that can replace rustc, rustdoc, linker, runner, flags,
  loader, target, or executable path is rejected;
- custom harness targets are rejected across the recursive Cargo workspace
  closure: root package, explicit members, and in-root path dependencies;
- non-Cargo `rustc` fallback writes to a temporary output directory and remains
  compile-only evidence.

Closure proof on the final current source:

- Rust native-validation focused matrix: `18 passed`;
- complete internal Bench gate suite: `210 passed`;
- PM Rust contract focus: `1 passed`;
- complete PM adapter suite: `156 passed`;
- complete Roles adapters suite: `1310 passed, 2 warnings`;
- complete Factory pipeline: `1289 passed, 2 warnings`;
- KernelOne release gate: `ok=true`, `415 passed, 1 skipped, 2 warnings`;
- Ruff check/format, mypy, compileall, JSON parse, and scoped
  `git diff --check`: pass;
- final independent specification review after four remediation rounds:
  `CLEAR`, no Critical, Important, or Minor findings;
- Provider calls and Bench runs after the fix: `0`.

Machine evidence is
`scratchpad/r115-rust-contract-context/defect-record-r115-rust-native-validation.json`.
All recorded R115 secondary buckets are now closed.

## R115 Final Synchronized Non-Provider Preflight (2026-07-27)

The exact current production source was captured twice at backend fingerprint
`46562cb0fd37568f` on HEAD
`e11a86db623a0babcd713e3d32b97341b6cad1b8`; both captures were identical.
The shared worktree is intentionally not rewritten or reset, so this source
fingerprint—not worktree cleanliness—is the executable source identity for the
next isolated run.

Final preflight proof:

- complete Factory pipeline: `1289 passed, 2 warnings`;
- complete KernelOne audit scope: `597 passed`;
- KernelOne release gate: `ok=true`, 51 suite paths,
  `415 passed, 1 skipped, 2 warnings`;
- all four R115 machine-readable defects: `CLOSED_REQUALIFIED`;
- Ruff check/format, mypy, compileall, JSON/YAML parse, and scoped
  `git diff --check`: pass;
- no active `factory_bench` process; the reserved main instance owns
  `49977/5173` and is not used by Bench;
- Provider calls and Bench runs during this preflight: `0`.

The final non-Provider preflight is closed. Exactly one fresh isolated R116 run
for `L1-05` is authorized on the exact source fingerprint above, with
`--launcher-instance-mode isolated`, `--bench-session-reporting off`, a
5400-second per-project lease, and a 6000-second outer watchdog. Any source
fingerprint drift before launch invalidates this authorization and requires a
new preflight capture.
