# Factory R39: Director continuation fact boundary

Status: closed  
Bench: frozen after R39 pending pre-bench source qualification; no Provider or Bench attempt is authorized  
Scope: Factory Director claim selection plus TaskRuntime dependency satisfaction.

## Observed fact

Fresh isolated L1-04 R39 reached the real Director path after qualified PM and
Chief Engineer requests. Director wrote five files, emitted five durable effect
receipts, and produced Go code that compiled. The first PM task then failed its
materialization semantic-quality gate after making physical progress.

Factory correctly opened a second dispatch round, but selected the pending
Chief Engineer control task `CE-PORTFOLIO-factory_0fed651bd294` instead of a PM
task. The Director handoff gate then rejected that internal task with:

`Director run requires valid Chief Engineer blueprint/handoff evidence: missing Chief Engineer blueprint id`

R39 physical evidence also proves:

- the PM rows are `TASK-1`, `TASK-2`, and `TASK-3`;
- all three persisted CE blueprints are handoff-ready;
- the pending CE portfolio row carries trusted Factory/TaskRuntime provenance;
- `_director_dependency_schedule` already excludes that trusted CE row;
- `_read_claimable_director_task_ids` did not apply the same exclusion.

After excluding the CE control row, the authoritative replay returned no
claimable PM row. `TASK-2` and `TASK-3` were still blocked because TaskRuntime
treated `TASK-1=FAILED` as permanently dependency-unsatisfied. That conflicts
with the already-established Director result policy and Factory signal
`director.partial_failure_progress_continued`: a quality-failed task that
committed usable files is supposed to let a later, explicitly-scoped PM task
repair or extend those files.

R39 `TASK-1` has stronger facts than a workspace delta:

- TaskRuntime terminal status remains `FAILED`;
- four declared regular files are recorded by `adapter_result` and exist below
  the bound workspace;
- the DEO parent is `CLOSED_WITH_OUTCOME_PROOF`;
- `receipt_count=5`, `failed_receipt_count=0`, `dead_letter_count=0`, and
  `aborted_count=0`;
- the settlement proof is bound to the exact Director execution attempt.

## Root

Factory used two different task-domain projections over the same authoritative
TaskRuntime facts. Deadline/dependency admission excluded trusted CE internal
execution rows, while per-round Director claim selection considered every
pending/ready row. This let a control-plane execution fact escape into the PM
delivery queue.

The second split was between result projection and TaskRuntime: orchestration
locally inferred failed-but-materialized progress from raw file lists, while
the execution authority knew only `FAILED != COMPLETED`. This left TaskRuntime
unable to release the next PM task even though DEO proved committed capability.

Neither defect is missing CE blueprint generation. They must not be repaired by
fabricating an id, weakening strict handoff validation, marking a failed task
completed, or letting Factory mutate sibling task rows.

## Fix contract

- Reuse `_is_internal_chief_engineer_task_row` in Director claim selection.
- Exclude only rows whose Factory run, stage, role, external/source identity,
  and TaskRuntime materialization provenance all match the trusted CE contract.
- Keep arbitrary or forged unknown rows visible to fail-closed admission.
- Keep PM identity precedence and TaskRuntime as the sole execution fact source.
- Keep the materialized parent terminal `FAILED`; authorize only dependency
  satisfaction when TaskRuntime itself derives a hash-bound receipt from:
  exact Director attempt identity, closed all-success DEO proof, a write-tool
  adapter result, declared target paths, and real non-symlink workspace files.
- Store that receipt on the TaskRuntime row/fact, remove it on re-execution, and
  make idempotent settlement replay finish any missed dependency side effect.
- Make orchestration consume the TaskRuntime receipt; raw `new_files` /
  `modified_files` must no longer act as cross-Cell authority.
- Do not modify target-project code or restore any PM-to-Director bypass.

## Exit gates

- Regression proves trusted pending CE portfolio/schema-repair rows never enter
  a Director round while PM tasks remain claimable.
- Existing external-id compatibility and execution-owned-state tests pass.
- Regression proves a real DEO-settled failed Director task remains `FAILED`
  while its blocked child becomes pending; zero/failed/dead-lettered receipts,
  undeclared files, forged receipt hashes, and non-Director roles fail closed.
- Factory stage characterization, PM dispatch, TaskRuntime, Ruff, format, mypy,
  compileall, architecture, and broader Factory tests pass.
- Metadata remains `not_schedulable` until all gates close and pre-bench is
  requalified for exactly one fresh isolated R40 attempt.

## Closure evidence

- Focused TaskRuntime materialization settlement: `9 passed`.
- Workflow Director result projection: `8 passed`.
- Factory stage characterization: `256 passed`.
- TaskRuntime full Cell: `904 passed, 10 warnings`.
- WorkflowRuntime full: `215 passed`.
- Factory Pipeline full: `1232 passed, 2 warnings`.
- Architecture full: `1411 passed, 8 skipped`.
- Ruff, format, mypy, compileall, and `git diff --check`: pass.
- No Provider request, Bench attempt, or target-project edit was made while
  closing this bucket.
