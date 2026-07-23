# Factory R45 — Fresh Isolated Acceptance Blueprint

Status: `R45_ACCEPTANCE_AUTHORIZED_ONCE`

## Purpose

R43 reached physical Director materialization and exposed three independent
platform contract defects.  R44A, R44B, and R44C close those defects:

1. Director admission now preserves a quality-gate start reserve while more
   materialization waves remain.
2. The first Director turn states the exact physical tool schema and defers
   verification when no verification tool is actually exposed.
3. Typed `project_type=cli_game` and explicit terminal intent outrank incidental
   Canvas/Web boilerplate.

The broad pre-bench proof is green on the same stable source snapshot.  Exactly
one fresh isolated L1-04 acceptance run is therefore authorized.

## One-shot scope

- Project: `L1-04`.
- Launcher mode: `isolated`.
- Bench session reporting: `off`.
- Main ports `49977/5173`: reserved; never reused by the Bench instance.
- Target-project edits from the Main Agent: forbidden.
- Provider calls: only those naturally emitted by this one Bench run.
- Source must remain stable from launch through result collection.
- After the attempt, authorization is consumed regardless of outcome.

## Required physical evidence

1. PM contract and Chief Engineer blueprint materialize.
2. Director physical requests expose the exact prompt/tool contract.
3. Every physical role request has a readable 24-hex ContextOS snapshot and
   passes identity, tools, tool choice, response format, token/window, and
   coverage audit.
4. Effects bind to receipts, TaskRuntime, Run Ledger, QA, and the Bench report.
5. Product code lands, environment/dependencies are prepared, at least one real
   build/test/lint gate runs, and one CLI/Web/API entrypoint actually executes.
6. Only a report proving the complete chain may claim `COMPLETED_VERIFIED`.

## Pre-bench proof

- Factory Pipeline: `1240 passed`.
- TaskRuntime: `904 passed`.
- Workflow Runtime: `215 passed`.
- KernelOne release: `ok=true`; `415 passed, 1 skipped`.
- Architecture: `1411 passed, 8 skipped`.
- R44A/R44B/R44C focused, Cell, static, YAML, and diff gates: pass.

