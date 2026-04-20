# ADR-0064: Tool Calling Matrix Strict Tool Identity and Coverage Gap Reporting

Status: Accepted  
Date: 2026-03-27

## Context

`tool_calling_matrix` is used as deterministic gate for Polaris tool-calling quality.
A deep audit found two risks:

1. Tool argument aliases were not normalized before judge checks, causing false negatives (e.g. `lines` vs `n`).
2. Equivalent-tool relaxation can hide missing canonical tool capabilities when benchmark expects a specific tool contract.

This conflicts with governance intent: benchmark must expose, not mask, capability gaps.

## Decision

1. Keep default policy strict for tool identity checks.
   - `required_tools` / `first_tool` / `parity` are strict by default.
   - Equivalent matching is opt-in only via `allow_equivalent_tools=true` in case spec.
2. Normalize tool arguments before checks using `normalize_tool_args`.
3. Add `tool_coverage` summary to `AGENTIC_EVAL_AUDIT.json`:
   - `required_but_not_observed`
   - `registry_not_covered_by_suite`
   - `coverage_gap_detected`

## Consequences

Positive:

1. Missing canonical tool call capability is reported directly.
2. Parameter alias noise is reduced, improving deterministic reliability.
3. Benchmark output now explicitly shows tool coverage gaps for follow-up implementation planning.

Trade-offs:

1. Scores may drop after strictness is enforced.
2. Legacy “equivalent by default” assumptions are no longer valid unless explicitly configured.

## Verification

1. Unit tests:
   - `tests/test_llm_tool_calling_matrix.py`
   - `polaris/delivery/cli/tests/test_agentic_eval_cli.py`
2. Integration run:
   - `python -m polaris.delivery.cli agentic-eval --workspace . --suite tool_calling_matrix --role all --case-id l1_single_tool_accuracy --matrix-transport stream`
3. Expected evidence:
   - `tool_coverage.required_but_not_observed` is populated when canonical tool is missing.
   - strict matching fails when only equivalent tool name appears.
