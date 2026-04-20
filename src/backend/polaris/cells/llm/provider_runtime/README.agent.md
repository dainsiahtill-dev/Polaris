# LLM Provider Runtime Cell

## Purpose

Provide provider action execution and role runtime provider invocation.

## Kind

`capability`

## Public Inputs

- `InvokeProviderActionCommandV1`
- `InvokeRoleProviderCommandV1`
- `QueryRoleRuntimeProviderSupportV1`

## Public Outputs

- `ProviderInvocationResultV1`
- `ProviderInvocationCompletedEventV1`

## Depends On

- `llm.provider_config`
- `policy.workspace_guard`
- `finops.budget_guard`
- `audit.evidence`

## State Ownership

- None

## Effects Allowed

- `network.http_outbound:llm/*`
- `llm.invoke:roles/*`
- `fs.read:config/llm/*`

## Invariants

- provider action/result mapping must be deterministic
- runtime provider invoke must respect blocked provider constraints
- external calls should use public boundary

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/provider_actions.py`
- `internal/runtime_invoke.py`
- `internal/runtime_support.py`

## Verification

- `tests/test_llm_provider_actions.py`
- `tests/test_llm_phase0_regression.py`
