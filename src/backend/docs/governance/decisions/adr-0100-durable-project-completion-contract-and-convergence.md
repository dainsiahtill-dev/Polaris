# ADR-0100: Durable Project Completion Contract and Convergence Ownership

- 状态: Accepted for implementation
- 日期: 2026-08-08
- 关联: ADR-0096, ADR-0097, ADR-0099, ADR-0093

## 背景

Polaris already has PM, Chief Engineer, Director, QA, TaskRuntime, TaskBoundary,
Run Ledger and verifier capabilities.  However, project completion is still
derived in multiple places, delivery obligations are mostly free text, and the
Factory HTTP path owns a bounded in-memory rework loop.  This permits false
convergence, cannot create work for newly discovered residuals, and loses
progress across a process restart.

Repeated benchmark failures show that more language-specific repair branches do
not solve this ownership problem.  The platform needs one typed definition of
done, one owner-bound completion verdict, and one durable same-project
convergence protocol.

## 决策

### 1. `runtime.projection` becomes the sole completion verdict owner

ADR-0099's pure unbound reducer remains supported.  A new owner-bound adapter
reads all required facts through public owner ports and is the only production
path permitted to emit `completed_verified=True`.

Factory, QA, HTTP routes, Bench and external supervisors may expose or consume
that verdict.  They may not reconstruct a parallel `COMPLETED_VERIFIED` rule.

### 2. Chief Engineer owns the typed project completion contract

Chief Engineer extends its blueprint handoff with a canonical,
versioned `ProjectCompletionContractV1`.  Director consumes the contract only
through public CE output and binds its hash into `TaskExecutionContractV1` and
`ExecutionEnvelopeV1`.  No adapter may infer a weaker contract from filenames
after dispatch.

### 3. VerificationGuard owns complete contract diagnostics

VerificationGuard evaluates the typed contract against owner evidence and emits
all stable residual diagnostics.  It does not schedule, repair, or decide final
completion.  Missing and failed evidence are separate diagnostics.

### 4. `orchestration.workflow_runtime` owns the durable convergence cursor

The same-project completion loop is a workflow, not an HTTP-router retry.  The
workflow runtime persists only cursor/transition/idempotency state and re-reads
TaskRuntime, TaskBoundary, effect receipts, Run Ledger and ProjectOutcome from
their owners.  It publishes one dependency-ready residual through existing
TaskMarket/TaskRuntime authority, waits for settlement, then revalidates.

### 5. External Supervisor remains project-level policy only

An external process may select a project, start an isolated instance, apply
budget/backoff policy and advance L1-L12.  It is never a platform fact source
and cannot own same-project residuals or Run Ledger success.

### 6. Model ceiling is evidence-structured

A model ceiling requires valid final request/tool/context evidence, available
execution authority and budget, exhausted bounded attempts on the same
diagnostic/contract hash, and absence of platform/provider/environment/repair
blockers.  Error-message substring matching is not authoritative.

Bench/mapping code may construct only `ModelCeilingCandidateV1` locators.
`orchestration.workflow_runtime` reads the ContextOS final-request snapshot and
queries roles.kernel, runtime.execution_broker and director.runtime through a
bootstrap-bound owner port. Generic `audit.evidence` receipts are not authority.
It rechecks workspace/project/run/contract/diagnostic identity, request/context
identity, exhausted attempt ordinals, artifact continuity and verifier semantic
stability, then internally seals `ModelCeilingQualificationV1` /
`ModelCeilingTerminalResultV1`. Missing direct owner APIs park as
`CONTROL_PLANE_BLOCKED`; convergence must owner-revalidate every received result.
Direct construction, `dataclasses.replace`, copy and pickle fail closed.

## 被拒绝的方案

1. **Keep extending Factory rework loops** — process-local, cannot create typed
   residual work, and duplicates workflow ownership.
2. **Make Bench/Supervisor the completion authority** — violates internal-test
   boundaries and creates a second truth.
3. **Treat disk existence as completion** — ignores current-run provenance and
   verifier failure.
4. **Add more sample-specific deterministic repairs** — expands rule surface
   without fixing contract or convergence.
5. **Collapse delivery, chain and task state into one status** — erases honest
   partial success and causes control-plane false failures.

## 后果

### 正面

- One non-forgeable completion verdict.
- Crash-safe, idempotent same-project progress.
- New residuals become schedulable work instead of manual log analysis.
- Stable modules can be sealed independently.
- Bound-model limitations can be distinguished from platform failures.

### 负面

- Cross-Cell public contracts and bootstrap composition must change together.
- Existing callers of parallel completion helpers require migration.
- Completion contracts add strictness and may expose previously hidden invalid
  plans.

## 验收

- Characterization tests prove the historical false-convergence cases fail.
- Owner-bound outcome is the only API able to emit completed verified.
- Completion-contract hash is unchanged CE -> Director contract -> envelope ->
  receipt.
- Verification returns all stable diagnostics in deterministic order.
- Workflow restart does not repeat committed effects and resumes the same
  residual/contract hash.
- Structured model ceiling rejects missing-context, provider, environment and
  control-plane failures.
- Focused ruff, mypy and pytest pass; graph/catalog/descriptor gates pass.
- One fresh isolated project reaches authoritative completion, followed by the
  multi-language N-batch seal.
