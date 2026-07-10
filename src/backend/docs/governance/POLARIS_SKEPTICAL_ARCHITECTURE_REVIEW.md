# Polaris Skeptical Architecture Review

This checklist keeps a standing skeptical voice during base-architecture work.
It is not a replacement for execution ledgers, QA verdicts, or bench evidence.
It is a review lens for deciding whether the architecture is proven or merely
locally improved.

## Core Question

The architecture is not proven until a fresh isolated project reaches stable
`COMPLETED_VERIFIED` through one coherent fact chain:

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

If any link is missing, contradictory, or reconstructed from a legacy file, the
run is not proof of architectural convergence.

## Evidence Bar

For each failed or completed task, reviewers must locate evidence for:

- Final provider request snapshot and context audit.
- Native or text-tool lifecycle receipt.
- Dispatch count and effect receipt count.
- TaskBoundary verdict and failure class.
- TaskRuntime observable row.
- Run Ledger projection.
- QA verdict envelope and responsible layer.
- Factory/bench identity: requested project, canonical project, instance,
  workspace, backend port, frontend port, and run id.

Summary gates and unit tests are supporting evidence only. They cannot prove the
execution architecture by themselves.

Use `templates/skeptical-architecture-review-report.template.yaml` when a base
or bench agent reports a reliability claim. The report must remain machine
readable enough to show which fact-chain links are proven, missing, or
contradictory. The target schema is
`schemas/skeptical-architecture-review-report.schema.yaml`.

The file `audits/skeptical-architecture-review-current-unproven-20260710.yaml`
is a schema-valid example of an honest `unproven` verdict. It demonstrates that
a valid report can still deny architecture reliability when the system-oracle
evidence is absent.

Use `POLARIS_EXECUTION_CONTROL_PLANE_RECONSTRUCTION_PROMPT.md` when handing the
next base-architecture rewrite to an implementation Agent. It translates the
bench failure evidence into reconstruction scope, forbidden legacy paths, and
the fresh isolated `COMPLETED_VERIFIED` acceptance bar.

## Wave-Level Verification vs Architecture Proof

Component waves may use local contract, parity, recovery, and concurrency tests
as exit conditions. That is acceptable for reducing risk inside a migration
wave.

Those wave exits do not prove the architecture reliable. Any claim that the
execution architecture has converged must still be proven by a fresh isolated
project reaching stable `COMPLETED_VERIFIED` with the complete evidence chain.
If a blueprint says a Factory Bench run is not required for an individual wave,
reviewers must treat that as a local wave boundary only, not as permission to
skip the final system oracle.

## Red Flags

Treat these as architecture failures until disproven:

- `coverage_pass=true` but the request cannot dispatch the required write path.
- Native tools removed by retry or text fallback without parser/dispatch/effect
  evidence.
- `test_files=0`, missing entrypoints, or missing scripts while an upstream
  owner task is failed, blocked, pending, or timed out.
- QA/bench reports `IMPLEMENTATION_DEFECT` while TaskBoundary or execution
  control evidence points to dependency, materialization, dispatch, session, or
  cancel/deadline failure.
- TaskRuntime row, Director status, Run Ledger, and QA verdict disagree on the
  same task.
- Any final status depends on raw TaskBoard JSON, session JSON, message text, or
  prompt regex instead of Execution Ledger / TaskRuntime observable projection.
- Factory starts a new LLM turn without enough downstream QA budget, or cancels
  while an active tool-dispatch barrier still needs settlement.
- A fix improves a bench summary gate but does not strengthen the fact chain
  above.

## Disproof-First Review Prompts

Ask these before accepting any base change:

1. Which exact fresh isolated run proves this change?
2. Which evidence ref proves the final provider request had the right role,
   contract, targets, tools, and token budget?
3. If the provider produced tool calls, where is the lifecycle receipt proving
   normalization, dispatch, result, and effect receipt?
4. If the provider fell back to text-tool mode, where is the parser and dispatch
   evidence?
5. If a task failed or timed out, why did downstream tasks and QA classify it at
   the correct layer instead of as an implementation defect?
6. Did project-level build/test/depth wait for declared target-owner tasks to
   finish?
7. Are all visible statuses projections from the same execution facts?
8. What would falsify the claim that this architecture is now reliable?

## Acceptance

Do not accept a base rewrite as complete unless all of the following hold:

- At least one fresh isolated project reaches stable `COMPLETED_VERIFIED`.
- The evidence chain is complete for every Director task in the run.
- Cancel/deadline behavior is either not triggered or explicitly projected with
  barrier evidence.
- QA failure classes match upstream evidence when any task is incomplete.
- No legacy deterministic repair, direct-write bench workaround, or raw
  TaskBoard/session status path is needed for the pass.
