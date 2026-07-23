# Factory R44B — Director First-turn Tool Contract Blueprint

Status: `R44B_CLOSED`

## Problem

Fresh isolated Bench R43 proved that the first Director materialization call
was intentionally pinned to an exact `write_file` Provider schema.  The final
prompt nevertheless required verification in the same batch and retained a
broader role-capability list.  The physical request therefore asked the model
to use tools that did not exist in that request.

## Scope

One bucket only: make the Director decision prompt describe the exact physical
tool schema without weakening first-write forcing.

Required invariants:

1. The first missing-target materialization request remains exact
   `write_file`; no read/exploration tool is added.
2. Current-turn tool names are projected from the actual Provider tool
   definitions and explicitly override broader role-capability prose.
3. Auto-derived verification intent is not represented as a same-batch action
   when no verification tool is physically exposed.  Verification remains
   mandatory in a later governed continuation/quality phase.
4. Explicit contract-required tools still fail closed; this change cannot
   silently defer an explicit same-batch contract.
5. Non-forced requests that expose verification tools retain same-batch
   verification guidance.
6. No target-project edits, tool-surface widening, deterministic business-code
   fallback, or bypass of effect receipts / TaskRuntime / QA.

## TDD proof

- RED: exact write-only mutation+verification prompt currently invents an
  "available verification step" and lacks current physical schema truth.
- GREEN: focused task-contract and decision-message tests.
- Gate: Roles Kernel suite, Ruff, format, mypy, compileall, scoped diff audit.

## Deferred bucket

- R44C: Go CLI artifact-profile classification.
- Bench remains `not_schedulable` until R44C and pre-bench gates close.

## R44B closure evidence

- RED: three focused assertions reproduced the invented same-batch
  verification and missing physical-schema projection; a fourth reproduced
  the MATERIALIZE sanitizer reintroducing the same contradiction.
- Focused task-contract and decision-message tests: `20 passed`.
- Transaction internal suite: `370 passed`.
- Roles Kernel suite: `4168 passed`, two pre-existing warnings.
- Ruff, format, mypy, compileall, YAML parse, and scoped diff check: pass.
