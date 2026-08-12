# First Fresh Isolated Unattended Completion Proof

- Status: **Verified for one project; platform-wide closure not yet proven**
- Date: 2026-08-12
- Project: `L1-01`
- Factory run: `factory_a05700ed9ffd`
- Source revision containing the fixes: `0c5a62dfd`
- Wall clock: `1265.5s`
- Result: `chain=clean`, Factory `completed`, canonical execution
  `completed_verified`, authoritative QA PASS

## Why This Record Exists

Polaris spent months repeatedly improving local gates, retry policies, repair
rules, and summaries without producing one complete project. The failure was
not one large missing feature. Three independent control-plane defects broke
different links of the same physical fact chain, so fixing only the currently
visible error merely exposed the next one.

This document freezes the lessons from the first fresh isolated project that
actually completed. Future Agents must use these lessons to avoid returning to
blind full-chain reruns, model blame, or bench-summary patching.

## Deep Defects That Had To Be Closed

### 1. Physical verifier evidence was projected as QA-role invocation

A deterministic physical verifier report was a valid verdict artifact, but it
was not a physical QA LLM role run. Projecting it as `llm_invoked=true` or
letting the bench infer invocation from report existence split verdict truth
from role-execution truth.

Required invariant:

- Physical verifier reports project `qa_invoked=false` and `llm_invoked=false`.
- QA invocation authority prefers explicit `qa_invoked`; legacy
  `llm_invoked` is compatibility-only.
- Report existence cannot manufacture a role invocation.

### 2. A provider timeout was incorrectly used as the whole Director transaction watchdog

Director can receive a provider response, dispatch write/edit tools, run local
repair, and settle effect receipts after the narrow provider-response window.
Using provider timeout plus a small grace period as the enclosing transaction
watchdog cancelled valid work while tools or settlement were still active. A
repair child context also lost the enclosing Factory/director deadline, making
the cancellation deterministic on longer repairs.

Required invariant:

- Provider response timeout bounds provider waiting only.
- Transaction watchdog derives from enclosing request/task budget and is
  capped by the Factory Director deadline.
- Child repair contexts preserve Factory/director deadline, source, and
  request timeout.
- An active tool/effect/settlement barrier must finish or emit explicit
  terminal evidence; it must never be silently discarded by a narrow provider
  timeout.

### 3. CE removed an unexecutable entrypoint obligation but retained a dangling verifier edge

CE correctly dropped an entrypoint obligation that could not execute, but a
build verifier still referenced its deleted obligation id. Portfolio validation
then failed before Director, even though the underlying portfolio was
otherwise executable.

Required invariant:

- Obligation removal is graph-atomic: remove the obligation and only edges
  that cover that exact removed id.
- Genuine unknown obligation ids remain fail-closed.
- Contract normalization must never create a dangling verifier reference.

## What Finally Proved The Architecture

The r44 run exercised one coherent chain:

```text
final provider request
-> provider response
-> tool lifecycle receipt
-> tool dispatch
-> effect receipt
-> TaskBoundary verdict
-> TaskRuntime observable projection
-> Run Ledger projection
-> QA verdict
-> Factory/bench report
```

Observed project evidence:

- 18 generated code files; 13 source files.
- 12 production TypeScript files; 689 production lines.
- 1 test file; 16 assertions.
- `npm run build`: exit `0`.
- `npm run test`: exit `0`.
- Playwright entrypoint: HTTP `200`, no console/resource errors, nonblank
  `640x400` Canvas 2D frame.
- Run Ledger: no missing or failed required evidence modalities; no failed
  control-plane events.
- Task 3 hit TS6133, stayed on the same Director task, repaired the file, reran
  the affected verifier, and completed without restarting PM or CE.

## Mandatory Debug Order After Any Future Failure

Do not start by adding a repair regex, loosening QA, increasing a random
timeout, or rerunning the whole chain. Inspect the failed edge in this order:

1. Final provider request: role, PM contract, CE blueprint/handoff, target
   files, failure feedback, tools, response format, and token budget.
2. Provider response: real content/tool calls versus parser projection.
3. Tool lifecycle: normalization, authorization, dispatch, result, and real
   file fingerprint mutation.
4. Effect receipt and settlement: every physical write/edit/command must be
   durably settled before transaction completion.
5. TaskBoundary and TaskRuntime: execution history and delivery authority must
   agree without erasing valid settled artifacts.
6. Run Ledger and QA: distinguish missing evidence from present-but-failed
   evidence; distinguish deterministic verifier from QA-role invocation.
7. Factory/bench report: it is the last projection and a measurement surface,
   never the place to repair upstream truth.

Each failed run must emit one primary broken edge and one owner module. No new
bench run is allowed until the owner fix has focused tests and the platform
cascade is green.

## Practices That Wasted Months

- Repeatedly rerunning the same project before closing the exact fact-chain
  edge.
- Treating every new surface error as an unrelated defect instead of checking
  whether settled facts were lost between projections.
- Blaming model quality before auditing the final provider request, tool schema,
  write effect, and settlement path.
- Using unit-test success or `step resolved` as project-completion proof.
- Restarting PM/CE for ordinary Director or QA residuals.
- Loosening required physical build/test/entrypoint gates rather than fixing
  invocation and evidence semantics.
- Letting `/tmp` remain the only copy of decisive evidence.

## Evidence Archive

Durable local archive:

`~/.polaris/audit_archives/unattended-completion-20260812/r44/`

- `factory_audits.json` SHA-256
  `d8d97c5694ff689c29a36fc09fc392334fc03fecf4d043be801cb17f612e5a5e`
- `L1-01.chain.log` SHA-256
  `edda0be149a75f1c2cbef55d8558e6e5dc413edd897ee97ff0b09f84d9f873b2`
- `runner.log` SHA-256
  `0296bea707b9c007f65e0fbcbe0d18edafff1fce3d714206399cdd68ee74bd1e`

## Next Proof Boundary

One project proves the first complete system oracle, not universal readiness.
Next action is sequential `L1-02` on current `main`, fresh isolated instance,
full budgets, no target-project edits. If it fails, close the newly observed
general root cause at its owner Cell before rerunning. Platform-wide readiness
requires later N-batch evidence with no new general root cause.

## L1-02 r45: First Post-Proof General Root Cause

The first sequential L1-02 probe did not enter Director. It failed at Chief
Engineer completion-contract normalization even though the final provider
request was context-complete and the CE produced a coherent portfolio.

The exact authority split was:

- PM-owned source: `src/index.js`.
- PM-owned executable entrypoint command: `npm start`.
- CE semantic entrypoint suggestion: `node src/index.js --input <text>`.

The platform compared the two command strings literally, dropped the only
entrypoint row, then rejected the application because no required entrypoint
remained. This is a platform authority-normalization defect, not a Provider,
Director, or target-project failure.

Required invariant: CE may describe entrypoint semantics and PM-owned paths,
but executable commands always converge to committed PM authority. If the same
owner has exactly one PM entrypoint authority and the source path is within PM
scope, normalize to that command. Missing, ambiguous, or out-of-scope authority
must remain fail-closed.

Durable evidence is archived under
`~/.polaris/audit_archives/unattended-completion-20260812/r45/`.

The owner fix is intentionally narrower than “one authority means accept”. PM
entrypoint targets are transported explicitly per owner task, intersected with
that task's exact `target_files`, and paired only with that owner's unique PM
entrypoint command. CE artifact wording is not a second authority requirement:
a valid PM entrypoint remains valid when CE calls the artifact generic
`source`. The legacy `semantic_role=entrypoint` fallback remains only for owner
tasks without explicit PM entrypoint-target authority. The first focused
regression run caught and rejected a broader version that would have promoted
an optional web adapter into an executable entrypoint.

Pre-rerun proof:

- Chief Engineer public contracts: `175 passed`.
- Ruff and Mypy: clean.
- `M06_director_multi_task`: PASS.
- Platform module cascade: `9/9` PASS.

## L1-02 r46: ContextOS False-Error Projection

The fresh r46 run proved the CE authority-normalization fix: PM completed and
Chief Engineer generated `2/2` blueprints. Director then reached canonical
TaskRuntime `completed`; QA was still running when this note was written.

The live ContextOS UI nevertheless listed the successful PM and CE stage
events under `异常闭环`. Their durable Factory events were unambiguously green:

- `type=stage_completed`
- `result.status=success`
- no top-level error
- summary text included `error_code=none`

The frontend routed formal `event.factory:*` records through
`parseProcessStreamLine`, whose fallback severity heuristic checked
`/error|failed|exception|traceback|timeout/` before success words. Therefore
the literal field label `error_code=none` overrode the structured success
result and produced `LogEntry.level=error`.

Invariant: structured event outcome is authoritative over display text.
`result.status=success|completed|passed` or explicit `ok=true` must classify as
success; structured failed/error/cancelled/blocked or `ok=false` must classify
as error. Text matching is fallback only when structured outcome is absent.

Regression proof:

- exact runtime.v2 successful Factory envelope with `error_code=none` is not an
  error and does not increase ContextOS `errorCount`;
- failed Factory StageResult remains an error;
- `useRuntime.test.ts`: `37 passed`;
- ESLint and frontend TypeScript typecheck: clean.

## L1-02 r46: Dynamic Quality-Repair Breakpoint Audit

r46 did not reach `COMPLETED_VERIFIED`. It produced a runnable JavaScript
project with 11 files, 9 source files, passing syntax/build/package/entrypoint
checks, and 22/23 passing Node tests. The sole product residual was a concrete
TAP assertion in `tests/product.test.js`: `extractDreamKeywords` did not include
`火焰`.

Dynamic tracing followed the physical chain rather than restarting the bench:

`failed command -> bounded failure island -> repair diagnostics -> owner task ->
deterministic attempt -> LLM fallback -> tool effect -> TaskRuntime lease ->
settlement -> verifier rerun`.

It exposed five platform defects:

1. Tail-only command trimming could discard an early TAP failure while keeping
   only late passing rows and `# fail 1`.
2. The same failure was fed through `diagnostic_excerpt`, `stdout_tail`, and
   `stderr_tail`, multiplying one assertion into multiple repair inputs.
3. TAP output was normalized line-by-line, expanding one causal failure into
   roughly 100 generic diagnostics instead of one `verifier_test_failure`.
4. Deterministic source-tool/coverage evidence without any successful write
   suppressed the LLM repair fallback. Both repair rounds therefore made no
   mutation and ended as `two_consecutive_stagnant_repairs`.
5. The Factory quality-repair continuation reused a 300-second TaskRuntime
   claim without the heartbeat used by ordinary Director execution. Long repair
   work reached DEO after lease expiry and failed settlement.

The hardened contract is now:

- preserve the first failure island plus final verifier summary within the
  output budget;
- prefer `diagnostic_excerpt` as the sole repair diagnostic source;
- normalize each TAP `not ok` block to one structured diagnostic with location,
  assertion, expected, and actual values;
- evidence is not progress: only authoritative mutation suppresses same-task
  LLM edit fallback;
- map absolute test locations safely back into the workspace and then to the
  imported implementation owner;
- force `edit_file` for existing-code TAP assertion repair, including the text
  fallback path;
- heartbeat the exact repair execution attempt until immediately before
  settlement;
- keep full workspace-repair evidence in its artifact and publish only a
  bounded hash/ref summary through Run Ledger/runtime.v2/NATS.

Offline proof after the fixes:

- real r46 output normalizes to exactly one `verifier_test_failure` containing
  `火焰`;
- target projection ranks `src/dream.js` and selects the original source-owning
  Director task, never PM/CE and never an unrelated test-only task;
- repair final-request context includes structured failed-gate and workspace
  quality evidence, current UTF-8 contents, and an exact `edit_file` tool
  contract;
- focused Factory repair tests: `5 passed`;
- focused Director adapter tests: `4 passed`;
- Run Ledger/publisher tests: `129 passed`;
- Ruff and Mypy for the affected production modules: clean.

A fresh isolated rerun is still required. r46 itself remains a failed historical
run and must not be relabeled.

## Pre-bench closure after dynamic breakpoint audit

The previous repair-kernel and Factory baselines were driven to green before a
new Provider run was allowed. Dynamic tests exposed four deeper control-plane
defects that ordinary end-to-end logs had hidden:

1. A successful write-shaped tool row without a path and changed before/after
   hashes could settle a repair task. Mutation evidence now requires all three.
2. A committed physical receipt could still settle success after the repair
   lease heartbeat had failed. Heartbeat rejection/expiry now invalidates the
   completion authority and forces failed settlement.
3. Canonical deterministic repair briefly replaced an exact Director task with
   workspace-level repair metadata. The runtime schedule now consumes the
   original task payload and its owned target files.
4. After artifact-quality code was split into `_scan`, a hard-coded exact
   `__module__` comparison stopped recognizing the platform scanner. Typed
   issues disappeared, so downstream test ownership could not be deferred.
   Default-scanner detection now uses the stable package identity.

Additional convergence hardening:

- TAP diagnostics are bounded to 12 while preserving total count, truncated
  count, and full-source SHA-256 for audit.
- command head/failure/tail slices are disjoint, so one `not ok` is never fed
  twice into repair planning.
- `typescript.json_as_source` no longer fabricates placeholder tests; it only
  replaces a proven package manifest written into a TypeScript path.
- missing domain exports no longer synthesize classes/functions from usage.
  Unsafe cases remain `covered_unplannable` and route to the same Director task.

Pre-bench proof at this checkpoint:

- Factory stage characterization: `315 passed`;
- Director adapter: `458 passed`;
- repair kernel plus strict TypeScript repairs: `491 passed`;
- CE public contracts, Run Ledger, and JetStream publisher: `303 passed`;
- affected Ruff and Mypy gates: clean;
- `git diff --check`: clean.

These gates prove local contracts only. They authorize a fresh isolated r47;
they do not relabel r46 or prove L1-02/N-batch completion.

## L1-02 r47: Destructive Repair and Observer Backpressure

r47 materialized 11 files and passed JavaScript syntax, build, package,
entrypoint, and delivery-depth checks. Initial `npm test` reached 18/22 passing
tests. The run then regressed because a deterministic repair misclassified a
generic TAP `AssertionError` as a missing-export defect.

Dynamic file-event replay proved the exact mutation chain:

1. The missing-export planner enumerated every named import in test files after
   seeing `AssertionError`/`strictEqual`, despite no unresolved-export error.
2. Five already-exported domain functions were rewritten from test literals;
   several collapsed to `return "1.0.0";`.
3. The DEO executor persisted those real modifications with `patch=""`, so
   runtime events falsely reported `patch_unavailable_reason=no_content_change`.
4. A later LLM repair tried to repair the newly corrupted functions, causing an
   equal-count diagnostic swap and `two_consecutive_stagnant_repairs`.

The closed contract is monotonic:

- missing-export requires an explicit symbol and module/path diagnostic;
- already-exported symbols produce no operation;
- only an existing declaration, class method facade, or imported binding may
  be exported/re-exported;
- absent domain implementation remains covered-unplannable for the same owner
  Director task;
- DEO modify events calculate their patch from actual before/after UTF-8
  contents.

Offline replay used the frozen r47 initial 11 files and original TAP diagnostic.
The repaired planner returned `planned=false`, `patch_count=0`, and no changed
paths. Regression proof: repair kernel `470 passed`; combined Director/Factory/
HTTP targeted cascade had `760 passed` before four superseded invention tests
were converted to the monotonic contract; affected Ruff and Mypy gates are
clean.

r47 also exposed observer backpressure, not a Node syntax error. A runtime.v2
status frame exceeded 900,000 bytes while Factory GET requests took 19-70
seconds. The bench audit record measured `workspace_validation_repair_coverage`
at about 1.74 MiB, `chain` at about 443 KiB, and canonical projection at about
217 KiB. Factory status metadata now elides any oversized value into a bounded
summary with `json_bytes` and `durable_evidence=factory_run_audit_bundle`; full
evidence remains durable. Canonical attribution now reports an authoritative
failed QA verdict before its derived failed TaskRuntime helper, preventing
`task_runtime_not_completed` from hiding the real verifier failure.

Frozen r47 evidence and hashes:

`~/.polaris/audit_archives/unattended-completion-20260812/r47/`

## L1-02 r48: Second Isolated Project Completion

r48 closed L1-02 without restarting the whole chain. A Director-only retry
reused already verified artifacts, then a QA-only retry completed the project.
The last blocker was not product quality: an old QA FAIL from
`director-6a7ee46c866f` remained in raw Run Ledger history and poisoned the new
`completed_verified` TaskBoundary from `director-421e2fad985b`.

The closed invariant is delivery-epoch authority:

- QA verdict applies only to exact `(task_id, director_run_id)`.
- Run Ledger preserves all historical gates but projects only the current
  delivery epoch into `effective_gates`.
- Factory consumes `effective_gates`; raw `gates` are legacy fallback only.
- A Director retry invalidates stale QA applicability, not PM/CE authority.
- Recovery reruns only QA and affected physical verifiers.

Live result for `factory_0dcb1e13baa7`:

- Factory `status=completed`, `phase=completed`.
- Current QA PASS and TaskBoundary both bind `TASK-2` /
  `director-421e2fad985b`; `canonical_authorized=true`.
- `npm run build`: exit `0`.
- `npm test`: exit `0`, `22/22` Node tests passed.
- `npm run start`: exit `0`, real CLI report emitted.
- Platform acceptance: exit `0`, `16/16` tests passed.
- Delivery depth: `prod_files=7`, `prod_lines=274`, `test_files=2`, passed.
- Regression cascade: Run Ledger `129`, Factory `38`, QA contracts `43` —
  `210 passed` total; Ruff and Mypy clean.

KFS evidence-path lesson: logical `runtime/qa/*` files may live below the
resolved system runtime root, not the workspace's physical `runtime` path.
Absence at a guessed path is not evidence loss; resolve storage roots and list
the bounded evidence subtree before declaring a receipt/report missing.

Durable evidence:

`~/.polaris/audit_archives/unattended-completion-20260812/r48/`

- `factory-run.json` SHA-256
  `23017506823a0055dfeb670b9da92407f59ee77506ffb59f31c34f800ee102af`
- `run-ledger-director-421e2fad985b.json` SHA-256
  `60dfd632d85312317f67090eeaf825d4769a8108b573935a5ce6909fe7d1b7ce`
- `qa-report.json` SHA-256
  `5fcdc92b352afbf14142ecda09bec06eca2b1a75041545f05f481f76d37359e5`
- `workspace-validation.json` SHA-256
  `bccdab139ea879ef5afde61e74f12263a570d8881fc0c5edb800d7723b26e220`

This is the second sequential isolated project proof. It strengthens the
platform evidence but still does not prove L1-L12 or the required N-batch.
