# Factory R44C — Typed Artifact Profile Classification Blueprint

Status: `R44C_CLOSED`

## Problem

Fresh isolated Bench R43 projected `builtin.artifact.html5_canvas` into PM for
L1-04, a Go `cli_game` whose product is an ASCII terminal application.  The
artifact heuristic scans the entire request, sees shared acceptance text such
as `index.html` / canvas first-frame checks, and returns Canvas before it checks
the task's explicit CLI/terminal intent.  Typed `project_type` is not consumed
as artifact evidence.

## Scope

One bucket only: make typed artifact intent and specific CLI/terminal intent
outrank incidental cross-modality boilerplate.

Required invariants:

1. Explicit `artifact`, `artifact_type`, and `project_kind` retain priority.
2. Recognized typed `project_type` values map to canonical artifact classes;
   generic or unknown project types do not become invented artifact classes.
3. `cli_game`, terminal app/game, and command-line variants normalize to
   `cli`.
4. Explicit CLI/terminal text outranks incidental Canvas/Web acceptance text.
5. A real HTML5 Canvas/browser request still selects `html5_canvas`.
6. Inference reasons and selected profile IDs remain auditable.
7. No target-project edits or benchmark-specific string/sample branch.

## TDD proof

- RED: reproduce Go `cli_game` plus shared Canvas boilerplate selecting the
  wrong artifact.
- GREEN: typed and text-only CLI regressions, plus true Canvas preservation.
- Gate: prompt-profile tests, Roles Kernel suite, Ruff, format, mypy,
  compileall, scoped diff audit.

Bench remains `not_schedulable` until this card and the pre-bench gate close.

## R44C closure evidence

- RED: typed `cli_game` and text-only terminal intent both selected
  `html5_canvas` when shared Canvas acceptance text was present.
- Prompt-profile suite: `19 passed`, including typed CLI, text-only CLI,
  unknown project type, and true Canvas controls.
- Roles Kernel suite: `4171 passed`, two pre-existing warnings.
- Ruff, format, mypy, compileall, YAML parse, and scoped diff check: pass.
