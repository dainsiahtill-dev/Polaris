# Benchmark Sandbox Hardening Execution Plan (2026-04-08)

## Phase 0: Governance Prerequisites

1. Record structural assumptions and verification plan in Verification Card.
2. Record architecture-level decision in ADR.

Deliverables:

- `docs/governance/templates/verification-cards/vc-20260408-benchmark-sandbox-hardening.yaml`
- `docs/governance/decisions/adr-0069-benchmark-sandbox-materialization-governance.md`

## Phase 1: Sandbox Materialization Hardening

1. Add shared sandbox-key builder (`case_id + sha1`).
2. Add shared copy ignore function (git/cache/pyc).
3. Apply to `benchmark_loader.materialize_case_workspace`.
4. Apply same strategy to `tool_calling_matrix.materialize_case_workspace`.
5. Keep no-fixture case behavior unchanged.

## Phase 2: Runtime Boundary Convergence

1. Move tool-calling matrix sandbox root to runtime path (`runtime/llm_evaluations/<run_id>/sandboxes/*`).
2. Keep report artifacts in same runtime lineage.
3. Ensure paths remain under `resolve_runtime_path()` contract.

## Phase 3: Reliability & Type Fixes

1. Fix optional `model` flow in `agentic_benchmark` by normalizing to non-empty string before observation collection.
2. Fix runner suite fallback accounting (`total_cases` and `passed_cases` when no detailed cases).
3. Remove unused import in context adapter.

## Phase 4: Test Convergence

1. Update stale role-filter expectation in `tests/test_llm_agentic_benchmark.py`.
2. Update loader tests to assert runtime-resolved sandbox paths instead of hardcoded `.polaris`.
3. Add loader tests for ignored cache/git artifacts and stable short sandbox key shape.
4. Add matrix test to verify sandbox path boundary.
5. Keep existing regression coverage green.

## Phase 5: Quality Gates

1. `ruff check` (targeted paths, with `--fix` where applicable)
2. `ruff format` (targeted paths)
3. `mypy` (touched implementation modules)
4. `pytest` (touched benchmark/runner test sets)

Done criteria:

- All phases complete with passing gate outputs.

