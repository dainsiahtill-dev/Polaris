# L3-24 r56 CLI Behavior Causal Owner Blueprint

Date: 2026-08-27  
Owner: `factory.pipeline` target routing  
Scope: generic same-task behavioral repair target selection

## Exact-run evidence

- Factory run: `factory_133765f7fbc9`.
- Target workspace is evidence-only and was never modified by the main Agent.
- Final request snapshot: `90afcc124891a118550be456`.
- Director role, PM contract, CE blueprint, failure feedback, workspace evidence, and `edit_file` tool were present; final request used about 21.4k of a 1M context window.
- Exact provider call `891ba713ac96460cb0ef5fb06d41415d` emitted a native `edit_file` call. Policy allowed a real edit to `src/cipher.cpp`; before/after hashes and an authoritative effect receipt were committed.
- Current verifier failures were CLI-boundary failures: a valid decrypt call was rejected by a required `--index` option and `phase-list` was reported as an unknown command. Yet Factory forced `src/cipher.cpp` because the behavior frontier rotated all production siblings in CE order.

## Root cause

Behavioral repair correctly preserves same-task authority, but it lacks a narrow causal rule for executable boundary failures. Test-only diagnostics expand to the whole production frontier, then rotation chooses a domain source even when argv/command parsing evidence proves the CE-owned entrypoint is the mutation owner.

## Invariants

1. Generated Bench projects remain read-only.
2. PM/CE authority and JobToken write scope remain immutable.
3. Explicit candidate-rejection feedback keeps its existing target pin.
4. Only unambiguous CLI boundary diagnostics may narrow the frontier to CE-owned entrypoint candidates.
5. Domain behavior assertions without CLI boundary evidence keep existing bounded sibling rotation.
6. The repair still requires a real edit receipt and verifier progress; target selection alone cannot mark success.

## Implementation

- Classify narrow CLI boundary evidence such as unknown commands and required CLI options.
- Within the already-authorized CE owner, detect conventional entrypoint paths (`main`, `cli`, `app`, `server`, `index`, or `cmd`/`cli`/`bin` path segments).
- Pin repair to those entrypoint candidates before applying rotation; do not expand scope.
- Preserve candidate-rejection target pinning before this inference.

## Verification

- TDD RED reproduces r56: current code chooses `src/cipher.cpp` instead of `src/cli/main.cpp`.
- Focused target-routing tests: 3 passed.
- Workspace-quality regression file: 103 passed.
- Ruff and targeted mypy: passed.
- Exact r56 final-request diagnostic replay selects `src/cli/main.cpp`.
- Fresh isolated L3-24 Bench remains the end-to-end gate.
