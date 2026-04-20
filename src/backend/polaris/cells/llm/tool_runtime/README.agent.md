# LLM Tool Runtime Cell

## Purpose

Execute tool-call rounds safely between role dialogue output and KernelOne tool
runtime.

## Kind

`capability`

## Public Inputs

- `ExecuteToolRoundCommandV1`
- `QueryToolRuntimePolicyV1`

## Public Outputs

- `ToolRoundResultV1`
- `ToolRoundCompletedEventV1`

## Depends On

- `policy.permission`
- `policy.workspace_guard`
- `events.fact_stream`
- `audit.evidence`

## State Ownership

- None

## Effects Allowed

- `fs.read:workspace/**`
- `fs.write:runtime/events/runtime.events.jsonl`
- `process.spawn:tools/*`
- `ws.outbound:runtime/*`

## Invariants

- tool calls must pass policy checks before execution
- tool round result must include normalized call/result payloads
- no implicit cross-cell internal imports from callers

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/orchestrator.py`

## Verification

- `tests/test_quality_checker_director_tool_calls.py`
- `tests/test_command_security.py`
