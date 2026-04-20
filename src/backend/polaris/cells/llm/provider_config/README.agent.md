# LLM Provider Config Cell

## Purpose

Provide normalized provider/test execution context resolution and settings sync
for LLM runtime paths.

## Kind

`capability`

## Public Inputs

- `ResolveProviderContextCommandV1`
- `ResolveLlmTestExecutionContextCommandV1`
- `SyncSettingsFromLlmCommandV1`

## Public Outputs

- `ProviderConfigResultV1`
- `ProviderConfigResolvedEventV1`

## Depends On

- `storage.layout`
- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- None

## Effects Allowed

- `fs.read:config/llm/*`
- `fs.read:workspace/.polaris/**`

## Invariants

- provider context resolution must be deterministic for same input/config
- settings sync cannot mutate unrelated settings domains
- external callers use public boundary, not internal modules

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/provider_context.py`
- `internal/test_context.py`
- `internal/settings_sync.py`

## Verification

- `tests/test_llm_provider_request_context.py`
- `tests/test_llm_test_context.py`
