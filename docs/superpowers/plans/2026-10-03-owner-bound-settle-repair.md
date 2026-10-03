# Owner-bound settle repair implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. User approved
> direct execution, no new branches/automatic commits. One final independent review.

**Goal:** Remove synthetic settle write authority and retain original task assets
through physical repair, validation and settlement.

**Architecture:** Existing Factory owner claim and strict CE public handoff drive
existing Director tasking/DEO. Artifact registration precedes TaskRuntime close.

**Tech Stack:** Python, current Cell public APIs, KernelOne, pytest/Ruff/Mypy.

**Spec:** src/backend/docs/blueprints/POLARIS_OWNER_BOUND_SETTLE_REPAIR_20261003.md

## Global constraints

- Actual generated targets read-only for engineering; no new model configuration.
- Keep current PM/CE, successful assets and receipts; ordinary errors stay Director-local.
- No candidate-derived grants, synthetic CE, helper completion, fake receipts, QA waiver.
- Runtime only .polaris/runtime; main/control/Starwave ports untouched.
- All shell commands RTK; explicit UTF-8; public Cell boundaries.

## Review focus

- Cross-run/task handoff must reject before physical execution.
- Explicit scope denial/narrowing must not be replaced by inventory.
- A second wave must retain original owner identity, not mint another task.
- Failed asset registration must block successful settlement.
- Output generation proposal cannot create permission even with a plannable rule.

### Task1: Commit authority projection

Files: factory_materialization_impl.py, factory_stage_executor/_mixin_03.py,
factory_workspace_quality_impl.py; tests/test_settle_repair_owner_authority.py.
Interface: commit context adds repair_task: Mapping[str,Any]|None; same result shape.

- [ ] Write missing-owner/foreign-path RED tests against actual commit producer.
- [ ] Require strict same-run CE handoff and projection; preserve original token.
- [ ] Use public Director profile/strategy projection for admitted scope.
- [ ] Pass original repair_task from existing quality-repair caller.
- [ ] Run authority negatives/positive immutable projection, Ruff/Mypy.

### Task2: Stage caller and settlement

Files: factory_materialization_impl.py; tests/test_director_stage_materialization_settle.py
and tests/test_settle_repair_owner_authority.py.
Consumes Task1 context, existing canonical owner claim and artifact recorder.

- [ ] RED stage callback verifies original claim/planning and receipt-before-close.
- [ ] Replace helper claim with existing causal owner resolver + canonical claim.
- [ ] Forward repair_task to planner/commit; register original task artifacts before close.
- [ ] Keep residual/cancel/failed commit fail-closed; never mark task verified from files alone.
- [ ] Run affected Factory/quality/DEO suites and independent final review.

### Task3: Evidence and qualification

- [ ] Correct temporal scanner comparison in defects/progress/memory.
- [ ] Store exact local gate results, scope review and open obligations.
- [ ] Verify owned isolated instance current state before any restart/retry.
- [ ] Same-run proof first; only current qualified candidate fresh run can count autonomous.
- [ ] Preserve denominator and sequential120-project/N-batch criteria.

### Task2b: Automatic original-owner repair continuation

Evidence: fresh r06 failed at Director, after an original TASK-1 claim and one
committed owner effect. Read-only current-byte/historical-diagnostic planning
returns owned/foreign/owned candidates; the foreign candidate aborts the loop.

Files: factory_materialization_impl.py and narrowly required existing
factory_workspace_quality_impl.py helpers; new Factory component tests.

- [ ] TDD: known foreign candidate between two owned candidates must not suppress
  either legitimate owner effect; unknown/unsafe candidates still reject.
- [ ] Route by original same-run CE completion/projection ownership, never by
  planner-created permission. Preserve the original commit scope guard.
- [ ] Finish/settle the current attempt correctly, claim the original other task
  through TaskRuntime, and replan from current diagnostics/bytes. Never replay an
  old candidate under a changed owner or mutate a token/projection to authorize it.
- [ ] Respect dependency blocks, heartbeat rejection, cancellation/drain, command
  policy, no-progress breaker and existing deadline; retain every physical receipt.
- [ ] Required diagnostics/receipt/boundary failures stay failed. Earlier affected
  owners require truthful revalidation; no completed verdict from file existence.
- [ ] Verify real multi-owner/late-rejection behavior, then independent review,
  current release gates and a fresh isolated project; unit pass is not fresh proof.
