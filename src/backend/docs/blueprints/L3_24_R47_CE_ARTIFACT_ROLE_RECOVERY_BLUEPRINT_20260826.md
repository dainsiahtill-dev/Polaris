# L3-24 r47 CE artifact-role recovery

Status: unit-validated; fresh isolated verification pending.

## Exact-run failure

- Run: `factory_34602430d453`
- Stage: `chief_engineer_review`
- Provider requests: three successful MiniMax-M3 calls; correct CE identity, PM contract, target files, forced strict submission tool, and 0.56% context-window use.
- Terminal schema residual: `project_completion_contract.obligations.artifacts.0.semantic_role='build'` was outside the canonical artifact-role enum.
- The Director was never reached. This is not the r46 candidate-guard defect.

## Root cause

`normalize_chief_engineer_portfolio_tool_arguments` already receives failed native tool arguments after strict transport rejection and already has a deterministic path classifier. It only invoked that classifier when `semantic_role` was absent. A present invalid provider alias therefore bypassed recovery and caused the later minimal projection to fail delivery-depth feasibility.

## Invariant and fix

1. Canonical roles are preserved byte-for-byte.
2. Missing or invalid roles may be replaced only when the existing artifact path/entrypoint classifier proves exactly one canonical role.
3. Ambiguous paths return the original payload with no repair code.
4. The source provider payload remains immutable; recovered payload and hashes remain auditable.

The change is generic CE structural recovery. It does not modify a generated project, relax the strict portfolio schema, or globally coerce arbitrary enum values.

## Evidence

- RED: exact `CMakeLists.txt` + `semantic_role='build'` regression failed before implementation.
- GREEN: 137 CE semantic-repair and Factory handoff tests.
- Ruff, Mypy, and `git diff --check`: pass.
- Fresh isolated L3-24 remains required before closing the defect.
