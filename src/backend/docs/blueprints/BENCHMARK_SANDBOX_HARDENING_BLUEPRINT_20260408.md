# Benchmark Sandbox Hardening Blueprint (2026-04-08)

## 1. Background

`llm.evaluation` benchmark code has four structural issues:

1. Sandbox materialization copies fixture internals (`.git`, `__pycache__`, cache files), causing nondeterminism and Windows long-path failures.
2. Tool-calling matrix sandbox root is written under workspace root instead of `runtime/llm_evaluations/*`, violating cell state owner boundaries.
3. Sandbox path uses raw `case_id`, which can be too long and unstable across fixture evolution.
4. Runner failure fallback can report `total_cases=0` when suite execution fails before returning case details.

## 2. Scope

In scope:

- `polaris/cells/llm/evaluation/internal/benchmark_loader.py`
- `polaris/cells/llm/evaluation/internal/tool_calling_matrix.py`
- `polaris/cells/llm/evaluation/internal/agentic_benchmark.py`
- `polaris/cells/llm/evaluation/internal/runner.py`
- `polaris/kernelone/benchmark/adapters/context_adapter.py`
- Related tests under `tests/` and `polaris/cells/llm/evaluation/tests/`

Out of scope:

- New benchmark suites
- Judge scoring model redesign
- Cross-cell contract reshaping

## 3. Architecture Decisions

### D1. Deterministic sandbox key

Sandbox directory switches from raw `case_id` to:

`<case_id_prefix>-<hash>`

- Prefix keeps observability.
- Hash keeps path short and collision-resistant.

### D2. Fixture copy hygiene

`copytree()` will ignore:

- `.git`
- `__pycache__`
- `.pytest_cache`
- `.mypy_cache`
- `*.pyc`, `*.pyo`

This keeps fixtures deterministic and prevents path-depth explosions.

### D3. Runtime path convergence

Both suites write sandbox and report artifacts under:

`resolve_runtime_path(workspace, "runtime/llm_evaluations/<run_id>/...")`

No writes directly under workspace root.

### D4. Runner fallback correctness

When suite returns only `ok/error` (no case list), treat it as one aggregate case:

- `total_cases=1`
- `passed_cases=0|1` based on `ok`

### D5. Type/lint convergence

- Remove optional model type mismatch in agentic benchmark observation collection.
- Remove dead import in context adapter.

## 4. Verification Strategy

1. Unit tests for benchmark loader sandbox behavior and ignore rules.
2. Tool-calling matrix test to assert sandbox path under runtime area.
3. Runner regression test for failure fallback totals.
4. Existing benchmark suites regression (`agentic_benchmark`, `tool_calling_matrix`, loader tests).
5. Ruff + Mypy + Pytest quality gates.

## 5. Acceptance Criteria

1. Sandbox copy no longer includes cache/git artifacts.
2. Tool-calling matrix sandbox never writes under workspace root.
3. Benchmark tests pass with updated role fixture inventory.
4. Runner no longer reports `total_cases=0` for suite-level failures.
5. Targeted Ruff/Mypy/Pytest gates pass for touched modules.

