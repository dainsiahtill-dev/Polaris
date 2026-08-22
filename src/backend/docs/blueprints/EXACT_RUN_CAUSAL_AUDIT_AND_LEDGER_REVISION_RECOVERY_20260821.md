# Exact-run causal audit and Run Ledger revision recovery

Status: Hardening — exact-run causal + repair-route audit live-proven; fresh L1-L12 march pending
Date: 2026-08-21  
Owners: `control_plane.run_ledger`, `audit.diagnosis`

## Problem

An exact Factory run can finish physical verification while remaining red in
the control plane. Live run `factory_ec5697b14a71` exposed one deterministic
case: a same-run QA retry wrote `workspace_validation` revision `4` after the
canonical FactStream already contained revision `7`. The writer derived the
next revision from a restarted local NDJSON projection rather than the
canonical FactStream. Two independent revision roots then made Run Ledger
integrity fail forever although 31 project tests and final QA revalidation
passed.

Manual correlation across provider, tool, verifier, TaskBoundary, TaskRuntime,
Run Ledger, QA, and Factory evidence is too slow for unattended development.

Live run `factory_d8966d9d011a` exposed three further diagnosis defects:

1. a non-terminal `running` projection was reported as
   `factory.pipeline.stage_failed` when no stage had failed;
2. Factory-scoped diagnosis missed PM/CE/Director journal events because role
   journals used role run ids while their nested payload carried the owning
   `factory_run_id`;
3. a delivery-depth failure was labelled a target-project defect even though
   the final Director request proved an impossible authority contract: QA
   required `prod_files>=7` and `test_files>=2`, but the repair request offered
   only `edit_file` and authorized three existing production files. Repeating
   that repair can never create the missing files.

Dynamic replay against the preserved PM plan and immutable CE portfolio found
the earlier authority defect: PM/CE authorized only three production source
paths and one test path while the carried delivery-depth contract required
seven production files and two test files. The Director repair surface was
therefore already infeasible before Director execution; the edit-only final
request was a downstream symptom, not the first cause.

## Architecture

```text
Factory gate producer
  -> control_plane.run_ledger public append
       -> read canonical execution.control_plane facts
       -> select canonical gate branch head
       -> allocate next revision
       -> explicitly resolve every orphan branch head, when present
       -> append immutable Fact + local projection
  -> Run Ledger projection
       -> unresolved fork: fail closed
       -> explicit complete fork resolution: keep history, select new head

Exact-run causal auditor
  -> provider request / response
  -> tool lifecycle / effect receipt
  -> verifier evidence
  -> TaskBoundary
  -> TaskRuntime
  -> Run Ledger
  -> QA verdict
  -> Factory terminal state
  -> stable diagnosis_id + one primary root_cause_code + ordered secondary candidates
  -> owner Cell + platform module attribution + evidence refs + executable next action
  -> evidence gap itself wins when the failed role's final provider request is unavailable
  -> role-preserving snapshot selection prevents a long Director tail from evicting PM/CE evidence
  -> non-terminal state remains RUNNING, never synthesized as failure
  -> Factory run correlation includes nested role-owned factory_run_id
  -> contract feasibility compares failed gate minimums, authorized scope,
     and final offered tool surface before blaming target code
  -> workspace verifier failure reads canonical runtime/qa evidence
  -> Director runtime coverage is discovery only
  -> read-only plan probe must produce a changed patch before a rule is executable
  -> failed verifier resolves to exactly one of:
       deterministic_repair_available
       repair_coverage_matched_but_unplannable
       repair_coverage_gap
  -> all three preserve PM/CE/completed artifacts and stay in the failed Director task

Bound HTTP API
  -> GET /v2/audit/runs/{run_id}/causal
  -> GET /v2/factory/runs/{run_id}/audit-bundle embeds the same causal report
  -> workspace comes only from backend instance binding
  -> current diagnosis remains separate from historical error counts
  -> terminal runtime.v2 events trigger the frontend to fetch the exact-run audit once
  -> audit-bundle reuses its already-read Run Ledger projection; no duplicate 78 MB scan
```

## Responsibilities

- `control_plane.run_ledger` owns revision allocation and branch resolution.
  Factory may declare gate identity and evidence but may not inspect a local
  ledger copy to invent canonical revision metadata.
- A normal fork remains an integrity failure. Recovery requires a new revision
  that continues one valid head and explicitly lists every discarded branch
  head by immutable content id.
- `audit.diagnosis` owns causal audit reports. Reports are derived evidence,
  never a new execution source of truth and never a Bench success condition.
- Target project files remain read-only to diagnosis. Repair changes Polaris
  only.
- Explicit workspace is local storage-layout authority. Diagnosis must not
  call a backend layout endpoint first: that can deadlock the current request,
  inherit an HTTP proxy, or read another instance's runtime.
- Loopback backend hints are allowed only when no workspace is supplied. They
  must disable environment proxies and remain non-authoritative.

## Causal audit output

Each report must include exact `workspace`, `factory_run_id`, `project_id`, and:

1. link status for every fact-chain layer;
2. one primary `root_cause_code` and responsible Cell;
3. secondary contradictions without overriding primary cause;
4. immutable evidence refs or exact source paths;
5. `next_action` naming the narrow retry boundary;
6. explicit distinction between current blockers and historical errors.
7. non-terminal/terminal status, correlated role event count, immutable
   `context_snapshot_ref` list, final offered tools, and contract-feasibility
   evidence.
8. stable `diagnosis_id`, ordered `root_cause_candidates`, conditional
   `evidence_completeness`, `platform_residual_attribution`, and a structured
   `next_action` containing preservation rules and prohibited actions.
9. structured `repair_diagnosis`: residual diagnostics, affected paths,
   coverage counts, plan-probe status, executable source tools, and no-op
   covered source tools.

The formal YAML schema is a release gate. It accepts every runtime status and
evidence-link state emitted by the implementation, including `RUNNING`,
`contradictory`, and `not_applicable`. A schema/implementation mismatch is a
platform defect, not a UI concern.

## Immediate diagnosis invariant

When a current Factory run becomes failed or completed, the observer must query
that exact run; it must never infer from the newest workspace run or historical
error count. The report must be useful without reading prose logs:

- one stable 24-hex diagnosis id for identical evidence;
- one primary cause selected by authority order, with all downstream symptoms
  retained as candidates;
- one responsible Cell and one platform module id;
- one retry boundary that preserves all verified upstream work;
- explicit failed task ids, failed verifier modalities, suspected files, and
  prohibited actions;
- final-request evidence for each role expected to have executed. Missing or
  wrong-role evidence fails closed as `context.engine` rather than guessing a
  model/tool defect.

Diagnosis is read-only. It never edits a generated project and never becomes a
second Run Ledger or Factory success source.

## Audit-system self-health invariant

Immediate attribution is trustworthy only while the diagnosis dependencies are
green. Before a fresh project bench is allowed, the complete owner suites for
`audit.diagnosis` and `director.runtime` must collect and pass. A package split,
missing test fixture, stale monkeypatch target, schema drift, coverage/planner
disagreement, or diagnostic-normalization split is itself a P0 platform defect;
the system must stop before spending provider tokens on a target project.

The self-health gate enforces these properties:

1. one verifier failure remains one causal diagnostic island when its headline,
   file location, stack frame, or suggested fix span multiple lines;
2. `known_rule_matched` never implies executability: a plan probe must produce
   at least one non-no-op changed path;
3. overlapping rules must be mutually disambiguated by normalized diagnostic
   semantics, not unrelated source excerpts;
4. test-only package splitting must retain shared helpers, fixtures,
   parametrization, and monkeypatch the post-split symbol owner;
5. a red self-health gate blocks a new bench but does not invalidate preserved
   exact-run evidence or authorize PM/CE replay.

Closure evidence on 2026-08-21: the Director Runtime suite moved from
`673 passed, 64 failed, 1 error` to `741 passed` after restoring split-test
contracts and closing four production-semantic drifts. The authoritative log is
`/tmp/polaris-director-runtime-full-r4.log`.

## Repair-route audit invariant

`known_rule_matched=true` is not permission to retry. The exact-run auditor
must consume the public `query_director_repair_plan_probe` result and require at
least one changed patch. A coverage match with zero changed paths is classified
as `director.runtime.repair_coverage_matched_but_unplannable`; repeating the
same deterministic repair is prohibited. The next action is always
`same_director_task_repair_only`, followed by only the affected verifier.

Full workspace-quality evidence is read through the canonical storage root at
`runtime/qa/workspace-validation.json`. Run Ledger's effective-gate projection
is the bounded fallback. Historical gate revisions cannot override the latest
effective repair evidence. Diagnostic-owned source files may be read for a
read-only plan probe, but diagnosis never writes target-project files.

## Contract-feasibility invariant

Before Director dispatch, the immutable Chief Engineer project completion
contract must contain enough distinct task-owned production and test artifact
obligations to satisfy the maximum `min_prod_files` / `min_test_files` carried
by the validated PM task contracts. An infeasible portfolio fails at the CE
boundary with `delivery_depth_completion_contract_infeasible`; it must not be
handed to Director and discovered hundreds of thousands of tokens later.

Director repair may start only when authorized scope plus offered tools can
physically satisfy the failed gate. A missing-file/depth failure requires both:

- at least one authorized not-yet-materialized target path of the required
  modality; and
- a create-capable tool such as `write_file`.

If either condition is absent, the auditor returns
`director.tasking.delivery_contract_scope_contradiction`, owner
`director.tasking`, retry boundary `same_contract_projection_only`, and
`pm_ce_restart_allowed=false`. It must not spend another provider call and
must not edit the generated project outside CE authority.

## Technical reasons

- FactStream is already the canonical ledger source. Moving revision allocation
  into its owner removes dual truth instead of adding another repair file.
- Explicit fork-head resolution preserves fail-closed behavior and immutable
  history. Silently choosing the newest event would hide real concurrent-write
  corruption.
- A deterministic auditor makes the fact chain executable as a diagnostic
  invariant instead of relying on UI error counts or human log interpretation.

## Verification

- Unit: sequential revisions supersede correctly.
- Unit: unresolved same-parent fork remains blocked.
- Unit: restarted independent chain is recoverable only through an explicit
  resolver revision listing every orphan head.
- Integration: Factory writer allocates from canonical facts even when local
  NDJSON is missing or stale.
- Live: retry only QA for `factory_ec5697b14a71`; no PM/CE/Director replay;
  Run Ledger, QA, Factory, independent tests, and entrypoint must agree.
- Live hardening: diagnose `factory_d8966d9d011a` from the same workspace after
  the instance stops; correlate role journals, expose final request refs/tools,
  and classify the scope/depth contradiction without another LLM call.
- Dynamic authority replay: feed the preserved L3-22 PM tasks and immutable CE
  portfolio to the CE feasibility projector; expect `actual=3/1`,
  `required=7/2`, and deficits `4/1`.

## Live closure evidence

- Before recovery: `CONTROL_PLANE_FAIL`, root cause
  `control_plane.run_ledger.gate_revision_fork_after_runtime_reentry`, retry
  boundary `same_run_quality_gate_only`, `pm_ce_restart_allowed=false`.
- Same-run retry: Factory advanced only `quality_gate`; PM, Chief Engineer, and
  Director remained completed.
- After recovery: run `factory_ec5697b14a71` is `completed`; quality gate score
  `100`; Run Ledger `integrity_ok=true` and `outcome_ok=true`; required command
  and QA evidence pass.
- Exact-run audit API returns `DELIVERY_VERIFIED`, no root cause, no evidence
  gap, while retaining `47` historical errors (`42` old failed gates and `5`
  old failed task boundaries) as non-authoritative history.
- Dynamic API debugging also found and closed a self-HTTP/proxy defect:
  explicit workspace resolution now avoids `/v2/runtime/storage/layout`;
  optional loopback hints use `requests.Session(trust_env=False)` and catch
  `RequestException`.
- Tests: causal classifier + HTTP contract + runtime-root authority = `11`
  passed; focused Run Ledger recovery suite = `8` passed; Ruff and Mypy clean
  for changed production files.
- L3-22 hardening: exact-run causal audit `11/11`; combined CE/audit/evidence
  suite `50/50`; Factory audit-bundle API `2/2`; changed-source Mypy and Ruff
  clean. Preserved-run replay reports production/test deficits `4/1` before
  Director dispatch. No generated project file was modified.
- The same preserved run now resolves to
  `chief_engineer.blueprint.delivery_depth_completion_contract_infeasible`,
  owner `chief_engineer.blueprint`, retry `same_ce_stage`,
  `pm_restart_allowed=false`, and `target_project_defect=false`. The downstream
  edit-only Director contradiction remains secondary evidence.
- FactoryRunMonitor no longer dereferences a nonexistent `auditBundle.bundle`;
  it renders current root cause, owner, and retry boundary separately from the
  explicitly non-authoritative historical error count. Frontend Vitest,
  TypeScript, and ESLint gates pass.
- Audit hardening now passes exact classifier/service `20/20`, CE/Director
  evidence producers `38/38`, final-request evidence `70/70`, HTTP audit
  contracts `6/6`, and frontend auto-fetch `2/2`; changed audit sources pass
  Ruff and Mypy.
- Preserved L3-22 replay returns stable diagnosis id
  `acff27430c37172354e30d99`, complete required evidence, primary CE authority
  infeasibility, and three ordered downstream candidates. The next action keeps
  PM, forbids PM restart, and requests only `same_ce_stage`.
- Dynamic performance replay found the audit-bundle's duplicate Run Ledger
  projection. One 78 MB canonical scan took `1.937s`; reusing that projection
  reduced subsequent causal classification to `0.588s` with an identical
  diagnosis id/root cause. The remaining single canonical scan is an indexed
  FactStream hardening target, not a reason to duplicate reads.

## Fresh L1-01 causal validation

Fresh isolated run `factory_dc3f1803031e` proved the audit must distinguish
physical effects from a control-plane parser failure:

- Director issued 13 native `write_file` effects; all 13 authoritative effect
  receipts were present and the tool lifecycle was green.
- All static project gates passed. The terminal red state came from TASK-3
  TaskBoundary treating the inline package script expression
  `import('node:fs').then(fs=>fs.copyFileSync('index.html','dist/index.html'))`
  as a local entrypoint path.
- Exact-run diagnosis now returns stable id `ad7a889b0652dd38b02fd98e`, root
  `control_plane.run_ledger.task_boundary_missing_entrypoint_target`, module
  `M06_director_multi_task`, failed task `TASK-3`, and retry boundary
  `same_run_task_boundary_reproject_only`. It explicitly preserves PM, Chief
  Engineer, and all completed Director artifacts.
- The generic entrypoint parser now accepts only a safe relative-path grammar;
  JavaScript/shell expressions, quotes, operators, parentheses, variables, and
  redirections cannot become entrypoint targets. Reprojecting the preserved
  TASK-3 evidence changes the verdict from `MISSING_ENTRYPOINT_TARGET` to
  `PASSED / completed_verified` without modifying the generated project.
- M06 module gate passes. Recent large-file splitting had also left stale
  module pytest paths, a dangling decorator, and a dropped shared test helper;
  the registry now checks every executable pytest target exists, and the M02
  gate again executes 192 tests successfully.
- Full cascade reached M05 after M01-M04 passed, then exposed two independent
  existing workspace-admission failures. They remain a separate M05 blocker;
  they do not invalidate the exact-run audit or M06 parser closure.

Testing commands must use the Polaris pyenv 3.12.3 interpreter. The system
Python lacks the `nats` dependency and creates a false collection failure.

## L1-01 repair-route dynamic proof

The preserved `factory_dc3f1803031e` workspace exposed three TypeScript
diagnostics after TaskBoundary recovery. Public Director repair execution
fixed both TS4104 readonly-array assignments with two policy-gated `edit_file`
effects and receipt `repair_receipt_95cf6505de3d19552ede4b81`; `npm run check`
revalidation reduced errors from three to one without replaying PM, CE, or
completed Director tasks.

The remaining TS2322 (`Timeout` not assignable to `number`) dynamically proves
why coverage and planning must be separate. Coverage matched three broad
TS2322 rules, but all three produced zero changed paths. The public exact-run
query now returns:

- stable diagnosis id: `ab1cfb0fa5632a9eb54a1daa`;
- root: `director.runtime.repair_coverage_matched_but_unplannable`;
- owner: `director.runtime`;
- boundary: `same_director_task_repair_only`;
- affected file: `src/render/gardenCanvas.ts`;
- PM/CE restart: forbidden;
- completed Director artifacts: preserved;
- evidence completeness: `true` with command, QA, and repair proof present.

Audit classifier/service gates pass `26/26`; changed Python sources pass Ruff
and Mypy; FactoryRunMonitor renders repair route, affected file, residual
diagnostic, and whether an executable changed patch exists; focused frontend
Vitest passes `2/2`.

## L1-01 repair closure and effect-port atomicity

The TS2322 route is now closed generically. Coverage first proved the old
rules were false-positive matches. The new
`deterministic_typescript_timer_handle_repair` is executable only when the
diagnostic is the exact `Timeout`/`NodeJS.Timeout` to `number` mismatch, the
reported source line calls `globalThis.setTimeout`, and the source proves a
browser-owned timer boundary. The live plan probe returned
`covered_plannable` with one changed path, and `npm run build` passed after the
policy-gated Director edit.

That repair exposed a second residual rather than restarting the chain: direct
Node TypeScript-source verification imported local `.js` paths. Public
coverage selected `deterministic_typescript_local_js_import_repair` and owned
the repair to TASK-3. The first live repair attempt produced decisive
effect-port evidence: two edits for one file executed sequentially, edit one
mutated the file, edit two rejected the original `before_hash`, and the failed
receipt recorded no rollback although the disk contained a partial patch.

`TransactionalRepairExecutor` now preserves precise editor semantics while
enforcing patch-level rollback:

- every execution-only edit copy binds to the current physical file hash;
- immutable plan ids and operation ids stay unchanged;
- the first accepted effect immediately activates one full-file rollback;
- reject/exception-after-mutation registers rollback before failure;
- rollback uses the policy-gated writer and records the result.

Three atomicity regressions and the full precise-edit suite pass. TASK-3 was
then replayed alone through `run_director_repair`; receipt
`repair_receipt_7c91630ea1ee9b5b22f0665b` is authoritative and its `npm test`
revalidation exited `0`. Final independent gates passed: `npm run check`
reported `52 passed, 0 failed`, and `npm start` built and executed the CLI with
exit `0`. PM, Chief Engineer, and completed Director work were not replayed;
no generated project file was manually edited.

The terminal Factory run remains an immutable historical failure. The new
audit view therefore keeps its old Factory outcome separate from current
repair/verifier evidence instead of rewriting history. Fresh bench validation
must create a new exact-run verdict.

## Current platform validation blocker

The exact-run audit and its affected repair routes are locally green, but the
complete `director.runtime` suite is not. On HEAD `5d307b2df`, a fresh run of
all 738 tests produced `673 passed, 64 failed, 1 error`. Fifty-seven failure
occurrences are missing helpers or a missing fixture in recently split
contract tests; the remaining signatures include coverage-rule overlap,
changed item cardinality, and public planners returning `repair_not_planned`.

This red state is tracked by
`defect-20260821-director-runtime-test-split-regression.json`. It is an
independent platform-refactor blocker: the new atomicity tests pass `3/3`, the
precise-edit suite passes, and the new TypeScript repair tests pass, but no
fresh Bench may be reported as platform-validated until the full Director
Runtime suite exits zero. Repair must start with the split test imports and
fixtures, then audit semantic coverage drift separately; generated project
files and PM/CE stages are outside this repair boundary.

## L3-22 CE semantic-output repair boundary

Exact run `factory_a9812b43a06a` established a second pre-Director failure
class. The CE provider completed one native `submit_structured_role_output`
call and returned a schema-valid portfolio, but its immutable completion
contract authorized only three production artifacts against the validated L3
minimum of seven. The feasibility gate was correct; the orchestration path was
not convergent because its single bounded repair was reachable only for
transport/schema failures.

The repair design keeps the existing authority boundary:

```text
validated PM contracts + CE topology authority
                 |
                 v
primary CE structured candidate
                 |
       schema + semantic + feasibility validation
                 |
        repairable contract deficit only
                 |
                 v
one separately claimed/deadline-admitted CE reconstruction
                 |
                 v
unchanged full validation -> persist candidate -> Director
```

No gate is relaxed. No generated-project file is edited. PM is never replayed.
The repair call inherits the original prompt-profile identity, validated PM
contracts, target/scope authority, project-completion authority, and exact
validation errors. A second invalid payload exhausts the repair budget and
remains a CE-local failure. Verification is defined by
`vc-20260821-ce-semantic-output-repair.yaml` and exact same-run CE retry.

Live same-run revalidation then exposed and closed a separate deadline-owner
bug. `chief_engineer_review` was absent from the retry stages that require a
fresh full Factory epoch, so a stale 455-second lease admitted zero CE calls
after reserving 310 seconds for Director/QA/finalization. Adding CE to that
full-epoch set preserves all existing budgets rather than weakening them.
The next retry of exact run `factory_a9812b43a06a` preserved PM completion,
reopened only CE, projected `factory_run_deadline_source=same_run_retry_epoch`,
incremented the extension count to 2, and entered a real CE provider stream.
The L3 march therefore remains anchored at `L3-22`, the first recorded open
L3 project; completed earlier projects are not replayed.

## L3-22 Go cross-file redeclaration repair gap

Exact run `factory_a9812b43a06a` advanced through PM, Chief Engineer, and
Director materialization, then reduced the physical `go test -count=0 ./...`
residual to one compiler island:

```text
models/seed.go:119:6: ParseNote redeclared in this block
models/model.go:169:6: other declaration of ParseNote
```

The same-run QA recovery correctly reclaimed only `TASK-2`, forced native
`edit_file`, committed a non-no-op effect receipt for `models/seed.go`, and
reran the same verifier. The residual did not close. Public repair coverage
proved the platform defect:

- the primary `redeclared in this block` diagnostic matched
  `deterministic_go_dedup_repair`;
- the companion `other declaration of ParseNote` diagnostic was classified as
  uncovered;
- plan probe therefore passed only one half of the compiler diagnostic group
  to the planner and returned `covered_unplannable`, `patch_count=0`;
- the existing Go dedup implementation only removed repeated generated
  `type|const|var` lines inside one file; it could not repair equivalent
  top-level function declarations across files despite advertising generic Go
  redeclaration coverage.

The repair remains inside `director.runtime` and preserves the public generic
Plan/Run boundary:

```text
paired Go compiler diagnostics
        |
        v
coverage: both halves -> deterministic_go_dedup_repair
        |
        v
planner: locate same top-level function in both package files
        |
        +-- bodies differ / pair incomplete -> no patch, fail closed
        |
        v
equivalent bodies -> remove compiler-primary duplicate only
        |
        v
PatchComposer -> policy-gated edit_file -> receipt -> go test revalidation
```

Assumption Register:

1. The two compiler lines form one indivisible diagnostic group. Verified by
   the exact `go test` transcript and shared symbol name.
2. Removing the compiler-primary declaration is safe only when the primary and
   companion are complete declarations in the same directory/package and have
   token-equivalent source after removing comments/insignificant whitespace.
   Functions, receiver methods, structs, single-line variables, and grouped
   const/var declarations use the same rule. Any semantic difference remains
   an LLM repair/blocker.
3. Coverage must include the companion line under the same source tool; merely
   broadening the primary match cannot make the planner plannable.
4. The repair must emit a precise text replacement, never write a whole file,
   and must not mutate generated projects outside Director policy execution.

Pre-mortem: the most likely wrong repair would delete a semantically different
function or a build-specific declaration. The planner therefore rejects
missing/ambiguous companions, different names or declaration kinds, different
directories/packages, build-tagged files, malformed/unbalanced declarations,
and non-equivalent bodies. Equivalent methods are permitted because the exact
receiver-bearing declaration is compared; different methods remain blocked.

Implementation evidence (2026-08-21): eight focused tests cover paired
coverage, public plan probing, functions/types/vars, grouped constants, method
diagnostic spelling, multi-pair compiler receipts, and fail-closed paths. The
focused Director Runtime suite reports `76 passed`; Ruff and Mypy are clean.
A no-write shadow replay against the exact L3-22 workspace produced two
non-overlapping patches in wave 1 and two in wave 2. The verifier residual
then narrowed to the intentionally non-equivalent `Engine.Step`, its missing
`applyPhysics` dependency, stale imports, and missing `Bubble.Validate`. Those
are separate semantic repairs and must not be hidden by declaration dedup.

Verification is defined by
`vc-20260821-go-cross-file-redeclaration-repair.yaml`. Live acceptance requires
same-run `TASK-2` repair only, a changed-hash `edit_file` receipt, and a new
`go test -count=0 ./...` result. PM and Chief Engineer calls must remain
unchanged.

## L3-22 package-local owner routing and plannable-repair unmask

The next same-run QA recovery exposed two factory-level defects. First, Go
package diagnostics used package-local paths such as `engine_test.go`, while
the canonical CE target was `engine/engine_test.go`. The repair loop neither
classified `*_test.go` as a test wrapper nor resolved the unique package-local
path. It also treated shared JobToken capability paths as unique ownership,
even though multiple tasks may legitimately receive write capability for a
manifest or entrypoint. The unique ownership SSoT is the CE
`task_completion_projection.owned_artifacts` projection.

Second, two real Director `edit_file` effects changed `main.go` and reran
`go test`. The second change replaced a constant conversion with
`math.Round`, changing the residual to `undefined: math`. Error count stayed
equal and the generic Go diagnostic-code extractor reported no stable code,
so the convergence breaker stopped after two `equal_count_swap` rounds. The
post-verifier coverage plan nevertheless newly exposed
`deterministic_go_missing_stdlib_import_repair`. That is concrete executable
progress and must receive one bounded continuation.

The hardened flow is:

```text
verifier diagnostic path
        |
        v
unique workspace suffix resolution (ambiguity blocks)
        |
        v
CE completion owned_artifacts selects owner
        |
        v
Director edit + unequal hashes + verifier rerun
        |
        +-- resolved / fewer errors -> continue or pass
        |
        +-- equal count + new unseen plannable source_tool
        |          -> exactly one bounded continuation
        |
        +-- repeated tool / no new effect -> existing cycle breaker
```

The implementation keeps PM and Chief Engineer sealed. Focused tests cover Go
test classification, CE ownership preference over a shared JobToken,
package-local path resolution, and a three-round constant-conversion ->
missing-import -> verifier-pass sequence. The complete workspace-quality test
file reports `52 passed`; Ruff and targeted Mypy are clean. Live acceptance is
the same run `factory_a9812b43a06a`, resumed only at `quality_gate`.

## L3-22 Go verifier function-context projection

Same-run recovery moved L3-22 from eleven quality errors to two behavior
assertions. Production-owner routing was correct and every `engine/engine.go`
edit had unequal hashes, but verifier results remained unchanged. Dynamic
final-request audit proved the remaining issue was evidence granularity, not
missing role identity, tools, PM/CE authority, or retry context:

```text
go test assertion: engine_test.go:112
        |
        v
old projection: lines 108-116 only
        |
        +-- hidden: SeedCMajorChord, restitution=0, dt=0.02, 500 Step calls
        +-- nearby prose conflicts with executable `Velocity.Y > epsilon`
        |
        v
Director makes real edit, verifier remains 2 -> 2
```

The prompt projection now includes the complete enclosing Go
`Test`/`Benchmark`/`Fuzz`/`Example` function, bounded by the next verifier
declaration and the existing total character budget. It explicitly states
that executable setup, calls, and assertions outrank conflicting comments.
Verifier files remain read-only evidence and never expand the JobToken-derived
write scope.

Verification is defined by
`vc-20260821-go-verifier-function-context.yaml`. Live acceptance remains the
same `factory_a9812b43a06a` quality-gate boundary; PM and Chief Engineer must
not rerun.

## L3-22 physical mutation versus behavioral progress

The complete Go verifier projection closed the context defect, but the next
same-run retry still remained at two failures. Dynamic audit of final provider
request `44b4193705493fa4b2a224da`, role turn outcomes, effect receipts, file
hashes, and the verifier rerun proved a deeper distinction:

```text
valid forced edit_file + unequal before/after hash
        |
        v
only executable addition: b.Velocity.Y -= 0
        |
        v
program behavior unchanged; go test 2 -> 2
```

Physical mutation is necessary but insufficient for repair progress. The
progress projector now retains separate physical evidence while denying
mutation authority to a deliberately narrow set of provable semantic no-ops:
comment/whitespace-only changes, self-assignment, plus/minus zero, and
multiply/divide one. Ambiguous edits are never guessed; the real verifier
remains authoritative.

The Director prompt also states the general executable truth rule: for a
failure guard `if condition { fail }`, passing requires `condition` to become
false. Setup, calls, and assertions outrank comments. This is a generic repair
contract, not a generated-project patch.

Verification is defined by
`vc-20260821-director-semantic-noop-progress.yaml`. Live acceptance remains the
same L3-22 Factory run and the same QA/Director boundary; PM and Chief Engineer
must remain sealed.

The first live revalidation proved the no-op classifier and exposed the next
convergence defect. A comment-only edit no longer counted as progress. The
next behavior-changing edit removed both prior failures and made the main Go
package pass, but exposed two different engine tests. Because the count stayed
two, the Director-local loop stopped even though the prior failing-test set was
fully resolved.

The local progress evidence now recognizes an unseen verifier
`forward_unmask` only when all of these hold: a responsible non-no-op write,
identifiable failing tests on both sides, disjoint old/new test identities,
the new set is no larger, and the full new diagnostic signature was not seen
earlier in this loop. A repeated A -> B -> A signature cannot renew the budget;
the existing five-attempt hard cap remains authoritative.

## L3-22 transport failure isolation and named-test contract conflict

The next exact same-run recovery exposed two distinct stop classes that must
not share one retry counter:

1. A `Request timeout (300.0s)` occurred after the forced Director request.
   This is Provider transport failure, not evidence that Director inspected the
   verifier and produced a semantic no-op. The quality loop now grants at most
   one same-owner transport retry. It does not increment semantic stagnation or
   non-progress counters; a second timeout stops as
   `quality_repair_provider_timeout_exhausted`. Adapter summaries may expose
   this failure only through their human-readable `error` field (for example
   `TransactionKernel execution failed: Request timeout (300.0s)` or
   `director_quality_repair_2_llm_timeout`); Factory normalizes those returned
   summaries before classifying the round, rather than trusting `error_code`
   alone.
2. Successful `edit_file` effects alternated the Go failures between gravity,
   floor/end-to-end, and restitution conventions. One bounded regression
   synthesis request carries the current named-test residual plus every
   displaced named-test guard. If that request does not strictly reduce the
   frozen test-identity union, the loop stops as
   `named_test_semantic_contract_conflict_candidate` and routes to a same-CE
   contract-feasibility review. PM replay remains forbidden.
3. The non-oscillating sibling is equally bounded: after two real mutations
   keep the exact same named tests red, Factory grants one causal-reanalysis
   request. If its residual named-test set is not a strict subset of the set it
   received, it emits the same conflict candidate with reason
   `bounded_causal_reanalysis_did_not_reduce_named_test_set`. It must not fall
   through to the generic non-progress fuse.

The stable identity source is real runner output (`--- FAIL: TestName` for Go),
not arbitrary prose containing a `Test*` token. This avoids inventing test
identities from compiler messages or comments. Physical writes, Provider
timeouts, and semantic contract feasibility now have separate evidence and
separate budgets.

Exact live replay `factory_a9812b43a06a` on 2026-08-22 preserved completed PM,
Chief Engineer, and Director stages and retried only QA. Three authorized
`engine/engine.go` edits passed syntax validation but did not reduce
`TestStepAppliesGravity` plus `TestStepWithRestitutionBounces`. One Provider
response took 292.9 seconds and returned HTTP 200 with no usable tool call;
the mutation-contract retry then produced an edit. This proves the remaining
red state is neither a missing tool schema nor a physical no-effect. The
pre-fix live artifact still used `three_nonprogress_repairs_without_verified_progress`,
which directly motivated the symmetric causal-reanalysis conflict projection.
After backend reload, the final same-run QA-only replay closed in exactly three
stagnant rounds with `named_test_semantic_contract_conflict_candidate`, reason
`bounded_causal_reanalysis_did_not_reduce_named_test_set`, owner `TASK-1`, and
the identical two-test input/residual set. The machine projection explicitly
forbids PM/CE restart and routes to `same_ce_stage_contract_feasibility_review`.

## L3-22 pre-freeze prevention: CE shared behavior authority

The same-run conflict detector correctly stopped Director repair, but detection
alone still spent source/test generation and repair tokens. The prevention
owner is `chief_engineer.blueprint`: new Factory CE requests now require an
independent `shared_behavior_contract`, and every task plan binds the exact
invariants it implements or verifies.

The contract is intentionally separate from the advisory-only project
interface declarations. It has its own hash/ref, owner and consumer task ids,
covered completion obligations, and concrete given/when/then examples. Before
any immutable portfolio is persisted, a pure feasibility gate checks exact
task/reference closure and rejects cross-task source/test ownership without a
shared invariant. The exact error remains machine-readable as
`blueprint_portfolio_behavior_contract_infeasible` through the Factory stage;
it is no longer collapsed into generic portfolio generation failure.

Preserved L3-22 portfolio evidence was replayed offline against the new gate.
With the historical empty behavior contract, the gate rejected TASK-1,
TASK-2, and TASK-3 because the required test owner lacked a shared production
behavior invariant. No generated-project file was modified. Targeted evidence:
`208 passed` CE contract/feasibility tests, `55 passed` Factory CE tests, Ruff
clean, and Mypy clean for eight source files. A fresh isolated Bench is still
required before claiming live completion.
