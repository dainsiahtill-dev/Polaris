# LLM Evaluation Cell

## Purpose

Execute readiness/evaluation suites and maintain test-index truth for provider
qualification. The cell also owns deterministic role benchmarks
(`agentic_benchmark`, `tool_calling_matrix`) and their sandboxed artifacts.

## Kind

`capability`

## Public Inputs

- `RunLlmEvaluationCommandV1`
- `QueryLlmEvaluationIndexV1`

## Public Outputs

- `LlmEvaluationResultV1`
- `LlmEvaluationCompletedEventV1`

## Depends On

- `llm.provider_runtime`
- `llm.provider_config`
- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `workspace/.polaris/llm_test_index.evaluation.json`
- `workspace/.polaris/runtime/llm_evaluations/*`

## Effects Allowed

- `fs.read:workspace/.polaris/**`
- `fs.write:workspace/.polaris/llm_test_index.evaluation.json`
- `fs.write:workspace/.polaris/runtime/llm_evaluations/*`
- `network.http_outbound:llm/*`

## Invariants

- evaluation reports must be indexable and reproducible
- index writes are explicit and auditable
- contract boundary is stable even if suite internals evolve
- role benchmarks must execute inside fixture sandboxes, not the primary repo
- judge outcomes must be deterministic and reproducible from trace + fixture state

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/runner.py`
- `internal/agentic_benchmark.py`
- `internal/tool_calling_matrix.py`
- `internal/deterministic_judge.py`
- `internal/benchmark_loader.py`
- `internal/readiness_tests.py`
- `internal/index.py`

## Verification

- `tests/test_llm_agentic_benchmark.py`
- `tests/test_llm_tool_calling_matrix.py`
- `tests/test_llm_test_index_reconcile.py`
- `tests/test_llm_qualification_validators.py`
- `tests/test_llm_connectivity_suite_ollama.py`
