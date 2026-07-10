# Polaris Execution Control Plane Reconstruction Prompt

This prompt is for a base-architecture Agent tasked with proving that Polaris
execution has converged. Its acceptance bar is intentionally higher than
passing another wave of unit tests.

## Objective

Reconstruct the Polaris execution control plane so a fresh isolated project can
survive multiple Director turns, tool batches, cancel/deadline boundaries,
TaskBoundary evaluation, repair/environment preparation, QA, and finally reach
stable `COMPLETED_VERIFIED`.

The goal is not to make one bench summary look better. The goal is to make the
execution fact chain authoritative and auditable.

## Required Reading

Before editing, read:

- `AGENTS.md`
- `CLAUDE.md`
- `src/backend/AGENTS.md`
- `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`
- `src/backend/docs/governance/POLARIS_SKEPTICAL_ARCHITECTURE_REVIEW.md`
- `memory/MEMORY.md` entries after the 2026-07-09 bench reset

Use codegraph before changing implementation code. Do not start from text
search and local patches.

## Bench-Derived Root Cause

Recent failed runs show that the main problem is not TypeScript, JavaScript, or
model coding skill in isolation. The deeper failure is split execution
authority across:

```text
final provider request
-> provider response
-> tool lifecycle receipt
-> tool dispatch
-> effect receipt
-> Execution Ledger commit
-> TaskBoundary verdict
-> TaskRuntime observable projection
-> Run Ledger projection
-> QA verdict
-> Factory/bench report
```

Provider route and fallback evidence improved only after fixing backup binding
selection, stale provider/model propagation, request snapshot preservation, and
transaction error metadata projection. That history shows Polaris needs one
immutable LLM-call evidence object, not several mutable projections.

Later runs showed tool dispatch and effect receipts can work, but
deadline/cancel/session state, TaskBoundary classification, downstream
dependency unlock, QA projection, and bench taxonomy can still diverge.
Failures such as `session_not_active`, `run_not_found`,
`director_no_materialized_changes`, `dependency_not_unlocked`, and missing tests
must first be treated as execution-control or task-boundary evidence until
proven to be ordinary implementation defects.

Repair and environment preparation are downstream mechanisms. They can repair
diagnostics after artifacts exist. They cannot solve missing materialization,
dropped tool dispatch, cancelled active sessions, missing owner tasks, or
TaskBoard/Run Ledger split-brain.

## Reconstruction Target

Build an atomic Execution Control Plane where:

1. Provider response and tool calls are normalized into lifecycle evidence.
2. Tool dispatch, tool result, and effect receipts are committed before a task
   can be considered complete.
3. TaskBoundary verdicts are derived from committed effects and verifier
   evidence.
4. TaskRuntime observable rows, Run Ledger, QA, UI, and Factory/bench report are
   read-only projections from the same execution facts.
5. Cancel and timeout behavior cannot suspend an active Director session while
   a tool-dispatch barrier still needs settlement.

## Hard Requirements

- Execution Ledger / `task_runtime.execution` fact stream is the execution
  authority.
- `TaskBoard`, Director status, UI status, QA status, and Factory report are
  projections.
- Raw TaskBoard rows, old session JSON, prompt text, message summaries, and
  regex-derived state are not authoritative.
- If `native_tool_calls_count > 0` and `dispatched_tool_calls_count == 0`, fail
  closed as `tool_dispatch_dropped`.
- If a write tool succeeds without an effect receipt, fail closed.
- A completed task must have effect receipt evidence, verifier evidence when
  required, and a TaskBoundary verdict.
- `run_not_found`, cancel, timeout, and deadline exhaustion must preserve active
  Director sessions unless an explicit cancellation barrier proves it is safe to
  stop them.
- QA verdicts must expose `failure_class` and `responsible_layer`.
- Factory/bench taxonomy must not report execution-control or task-boundary
  failures as ordinary implementation defects.

## Repair Boundary

Do not route orchestration failures into deterministic repair.

Repair is allowed for typed diagnostics on existing artifacts:

- syntax errors
- import/export mismatches with contract evidence
- manifest script defects
- lint/test/build diagnostics

Repair is not allowed for:

- target files never created
- tool calls not dispatched
- missing effect receipts
- active session suspended during settlement
- dependency not unlocked
- entrypoint not yet owned/materialized
- CE interface contract missing or contradictory

Those failures must return to execution control, TaskBoundary, or CE/PM contract
ownership.

## Forbidden Changes

- Do not add repair branches to `execute_method.py`.
- Do not add new legacy deterministic repair regex helpers.
- Do not modify Factory/bench gates to manufacture a pass.
- Do not modify generated target project code.
- Do not use raw TaskBoard/session files as the final status source.
- Do not treat `coverage_matched` as `covered_plannable`; use plan probe.
- Do not put dependency install commands inside repair rules.
- Do not accept unit-test waves as proof that the architecture is reliable.

## Required Validation

The final acceptance test is a fresh isolated project run. It must provide:

- requested project id
- canonical project id
- instance id
- workspace
- backend port
- frontend port
- factory run id
- PM contract ref/hash
- CE blueprint ref/hash
- final provider request / context snapshot refs for each Director task
- provider response evidence
- tool lifecycle receipt evidence
- effect receipt evidence for each write
- TaskBoundary verdict for each task
- TaskRuntime observable projection
- Run Ledger projection
- QA verdict with `failure_class` and `responsible_layer`
- Factory/bench report
- explicit legacy fallback/projection-mismatch status

The run must cover:

- multiple Director turns
- at least one multi-tool batch
- at least one verifier, repair, or environment-prep boundary
- cancel/deadline behavior that does not incorrectly kill an active settlement
  barrier
- final stable `COMPLETED_VERIFIED`

## Machine-Readable Final Report

Before claiming completion, fill a verification card from:

- `src/backend/docs/governance/templates/verification-cards/execution-control-plane-reconstruction-card.template.yaml`

Validate that card against:

- `src/backend/docs/governance/schemas/execution-control-plane-reconstruction-card.schema.yaml`
- `src/backend/docs/governance/ci/scripts/check_execution_control_reconstruction_card.py --workspace . --card <filled-card.yaml> --json`

Produce a report using:

- `src/backend/docs/governance/templates/skeptical-architecture-review-report.template.yaml`
- `src/backend/docs/governance/schemas/skeptical-architecture-review-report.schema.yaml`

The final report may set `architecture_reliable: true` only when all of these
are true:

- fresh isolated run reached `COMPLETED_VERIFIED`
- proof level is `system_oracle`
- every fact-chain node is `present`
- no red flag is `"true"`
- no projection mismatch remains
- no legacy fallback is needed for success

If any item is missing, the correct verdict is `unproven`, not pass.
