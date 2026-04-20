# Roles Kernel Cell

## Purpose

Provide the shared execution kernel for role prompt construction, output
parsing, quality checks, retry policy, and runtime-level event emission.

## Kind

`capability`

## Public Inputs

- `BuildRolePromptCommandV1`
- `ParseRoleOutputCommandV1`
- `CheckRoleQualityCommandV1`
- `ExecuteRoleKernelTurnCommandV1`
- `ClassifyKernelErrorQueryV1`
- `ResolveRetryPolicyQueryV1`

## Public Outputs

- `RoleKernelResultV1`
- `RoleKernelPromptBuiltEventV1`
- `RoleKernelParsedOutputEventV1`
- `RoleKernelQualityCheckedEventV1`

## Depends On

- `llm.provider_runtime`
- `policy.permission`
- `policy.workspace_guard`
- `audit.evidence`
- `finops.budget_guard`

## State Ownership

- None

## Effects Allowed

- `fs.read:workspace/**`
- `ws.outbound:runtime/*`
- `llm.invoke:roles/*`
- `process.spawn:roles/*`

## Invariants

- kernel logic must stay free of session ownership semantics
- adapter selection belongs outside the kernel boundary
- runtime events must be emitted explicitly
- assistant turn handling must separate raw parser input from sanitized transcript output

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/kernel.py`
- `internal/turn_engine.py`
- `internal/prompt_builder.py`
- `internal/output_parser.py`
- `internal/quality_checker.py`
- `internal/llm_caller.py`
- `internal/retry_policy_engine.py`
- `internal/error_category.py`
- `generated/verify.pack.json`

## Verification

- `tests/test_prompt_builder_retry.py`
- `tests/test_output_parser_patch_file.py`
- `tests/test_quality_checker_director_tool_calls.py`
- `tests/test_llm_caller.py`
- `tests/test_role_kernel_write_budget.py`
- `tests/test_turn_engine_semantic_stages.py`
- `tests/test_turn_engine_policy_convergence.py`
- `tests/test_kernel_stream_tool_loop.py`
