# Factory R47 — Native Verifier Contract

Status: `prebench_verified`

Bench state: `schedulable_once_under_R48`

## Problem

R45 final provider requests were structurally valid, but the Go L1-04
verification task required both native Go tests and a Python
`tests/test_product.py` harness. The Python command was not requested by the
project; it came from deterministic PM synthesis.

This cross-language verifier injection:

- expands target files and final-request tokens;
- adds an unrelated runtime/toolchain dependency;
- increases Director materialization failure surface;
- duplicates evidence already provided by `go test ./...` and `go run .`;
- makes a Go project appear incomplete when its native deliverables exist.

The same pattern exists in other non-Python deterministic workspace contracts.

## Invariants

1. A language contract uses that language's native build/test/entrypoint gates
   by default.
2. Python verifier files/commands are included only when the directive
   explicitly requires Python or a Python-based external verifier.
3. Native tests remain real executable evidence; this change does not weaken
   artifact, build, test, or entrypoint requirements.
4. PM, CE, Director, QA, Run Ledger, and Bench continue consuming one task
   contract; no Bench-only bypass or target-project patch is introduced.

## Minimal implementation

- Remove implicit Python verifier targets and acceptance commands from the Go
  deterministic workspace contract.
- Keep `main_test.go`, `go test ./...`, `go run .`, and README evidence.
- Add regression tests proving Go contracts contain only native verifier
  requirements unless the directive explicitly asks for Python.
- Record the broader JavaScript/Rust/C++/Java occurrences as follow-up
  migration candidates; do not mix those languages into this bucket without
  their own evidence and tests.

## Proof ladder

1. Focused PM adapter contract tests.
2. Ruff and mypy for changed files.
3. Factory Pipeline suite.
4. Full pre-bench gates once R46 and R47 are both focused-green.
5. One fresh isolated Bench authorization on a stable source fingerprint.

## Exit criteria

The synthesized Go verification task contains `main_test.go` and `README.md`,
requires `go test ./...` and `go run .`, and contains no implicit
`tests/test_product.py` or Python unittest command.

## Pre-bench evidence

- PM adapter contract suite: `135 passed`.
- Factory Pipeline: `1240 passed`.
- TaskRuntime: `418 passed`.
- Workflow Runtime: `215 passed`.
- KernelOne release: `415 passed, 1 skipped`, `ok=true`.
- Architecture: `1411 passed, 8 skipped`.
- Ruff, source mypy, compileall, and `git diff --check`: pass.
- Stable source fingerprint: `aab54ea3611e77cd` (two consecutive reads).

The focused contract exit criteria are satisfied. Fresh isolated L1-04 remains
required to prove the generated final provider requests and materialized
project consume the native Go verifier contract end to end.
