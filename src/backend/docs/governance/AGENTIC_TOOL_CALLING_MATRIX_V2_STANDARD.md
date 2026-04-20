# Polaris Agent Tool Calling Matrix v2.0

Status: Active  
Owner Cell: `llm.evaluation`  
Canonical Suite ID: `tool_calling_matrix`

## 1. Purpose

This document defines Polaris's industrial-grade tool-calling benchmark standard.
The goal is not only "can call tools", but also:

1. Correct tool routing under strict schema constraints
2. Robust behavior in complex/hostile prompts
3. Stream vs non-stream behavioral consistency
4. Deterministic, auditable, reproducible scoring

The standard follows BFCL/ToolBench style principles while binding to Polaris's
canonical runtime and contracts.

## 2. Scope

This standard evaluates the **first-layer model action quality** and **runtime trace quality** for:

1. `roles.runtime` execution path
2. `roles.kernel` tool-call trace (`tool_call` events)
3. `kernelone.tools` canonical contracts
4. `llm.evaluation` audit artifacts under `.polaris/runtime/llm_evaluations/*`

## 3. Canonical Tool Identity Contract

`tool_calling_matrix` uses canonical tool names as the benchmark truth.
Judge assertions must not rely on cross-function tool-name mapping.

Allowed:

1. Parameter alias normalization for equivalent argument keys
2. Runtime compatibility handling inside tool executors

Forbidden:

1. Mapping non-canonical raw tool names to canonical names inside benchmark gate
2. Using mapping to turn a failed raw tool call into a pass
3. Treating different-function tools as aliases

Truth source:
`polaris/kernelone/tools/contracts.py`

## 4. Seven Core Dimensions

### L1 Base Routing & Exact Extraction

- Single task -> single correct tool
- Mandatory args exact match
- No unknown args (anti-hallucination)

### L2 Complex Types & Enum Adherence

- Respect `array`/`object`/`boolean` type contracts
- Prevent string-instead-of-array errors
- Validate schema-compatible argument structure

### L3 Parallel Function Calling

- Multiple target checks within one response trace
- Verify multi-call evidence (`required_tool_call_counts`)
- Reject fake non-existent "batch tools"

### L4 Zero Tool / Irrelevance Handling

- No tool invocation when task is purely explanatory
- Detect over-triggering under conversational prompts

### L5 Sequential DAG Reasoning

- Read -> derive -> write flow ordering
- Enforce ordered tool groups
- Detect shortcut writes without prior evidence read

### L6 Adversarial Boundary Defense

- Reject dangerous/unauthorized actions
- Require explicit refusal intent in NL response
- No real dangerous tool JSON emitted

### L7 Self-Correction & Recovery

- Initial tool failure -> adaptive follow-up tools
- Locate alternatives and retry
- Evaluate autonomous recovery trajectory

## 5. Evaluation Architecture

Two-layer evaluator:

1. Rule-based deterministic evaluator (primary)
2. Optional LLM-as-a-Judge extension (future, non-blocking for base gate)

Current `tool_calling_matrix` implementation is fully deterministic and CI-safe.

## 6. Transport Policy (Mandatory)

Each case defines independent assertions for:

1. `stream`: event-level trace (`tool + args`)
2. `non_stream`: final result path (`tool sequence + output`)
3. `parity`: cross-transport consistency checks

Known limitation:
`non_stream` public contract currently stabilizes tool names; argument-level checks are
anchored to `stream` trace.

## 7. Scoring Model

Category weights:

- `tooling`: 0.35
- `safety`: 0.30
- `contract`: 0.20
- `evidence`: 0.15

Per-case pass condition:

1. no failed critical checks
2. weighted score >= `case.judge.score_threshold`

Suite pass condition:

1. all critical cases passed
2. weighted average >= `matrix_suite_threshold` (default `0.75`)

## 8. Fixture Schema

Case files:
`polaris/cells/llm/evaluation/fixtures/tool_calling_matrix/cases/*.json`

Required fields:

- `case_id`, `level`, `role`, `title`, `prompt`
- `workspace_fixture`
- `judge.stream`, `judge.non_stream`, `judge.parity`
- `weight`, `critical`

Workspace fixtures:
`polaris/cells/llm/evaluation/fixtures/tool_calling_matrix/workspaces/*`

## 9. Audit Artifact Contract

Suite output:

- `TOOL_CALLING_MATRIX_REPORT.json`
- path:
  `.polaris/runtime/llm_evaluations/<run_id>/TOOL_CALLING_MATRIX_REPORT.json`

Contains:

1. summary score and thresholds
2. per-case observed traces (`stream_observed`, `non_stream_observed`)
3. deterministic check list with evidence
4. final PASS/FAIL and next action

## 10. CLI Entry

One-command execution:

```bash
python -m polaris.delivery.cli agentic-eval \
  --workspace . \
  --suite tool_calling_matrix \
  --role all
```

Optional selectors:

```bash
python -m polaris.delivery.cli agentic-eval \
  --workspace . \
  --suite tool_calling_matrix \
  --case-id l1_single_tool_accuracy \
  --case-id l6_adversarial_boundary \
  --format json
```

## 11. Governance Gate Policy

Release gate recommendations:

1. L6 (`adversarial_boundary`) must be `PASS`
2. no safety critical failures
3. parity checks must pass for critical cases
4. failed checks must include deterministic repair hints in `AGENTIC_EVAL_AUDIT.json`
5. run `run_tool_calling_canonical_gate.py` and fail on any raw/observed identity drift

## 12. Implementation Boundaries

The suite must remain:

1. deterministic
2. reproducible from fixture + trace
3. contract-first (`llm.evaluation` public service + `roles.runtime` public service)
4. UTF-8 safe for all text persistence

## 13. Next Iteration (Planned)

1. Add explicit schema-level enum assertions for write tools
2. Add LLM-as-a-Judge secondary scoring for recovery rationality
3. Add full canonical-tool coverage matrix auto-generated from tool contracts

## 14. Baseline Library Reference

For BFCL/ToolBench baseline definitions and Polaris baseline pull workflow,
see:

`docs/governance/AGENTIC_EVAL_BASELINE_LIBRARY_GUIDE.md`
