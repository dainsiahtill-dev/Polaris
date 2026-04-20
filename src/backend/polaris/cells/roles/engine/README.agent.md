# Roles Engine Cell

## Purpose

Provide shared role-engine strategy selection, registry, and hybrid execution
primitives for role runtime.

## Kind

`capability`

## Public Inputs

- `SelectEngineCommandV1`
- `RegisterEngineCommandV1`
- `ClassifyTaskQueryV1`
- `EngineRegistrySnapshotQueryV1`

## Public Outputs

- `EngineSelectionResultV1`
- `EngineExecutionResultV1`
- `EngineRegistrySnapshotResultV1`
- `EngineSelectedEventV1`

## Depends On

- `llm.control_plane`
- `context.engine`
- `policy.permission`
- `policy.workspace_guard`
- `roles.runtime`

## State Ownership

- None

## Effects Allowed

- `fs.read:workspace/**`
- `llm.invoke:roles/*`
- `process.spawn:roles/*`
- `ws.outbound:runtime/*`

## Invariants

- strategy selection must be deterministic for a given task/context
- public callers should use service exports, not `internal/**`
- registry state is runtime-local and not a source-of-truth owner

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/base.py`
- `internal/classifier.py`
- `internal/registry.py`
- `internal/hybrid.py`
- `internal/sequential_adapter.py`

## Verification

- `tests/test_sequential_engine.py`
- `tests/test_roles_kernel.py`
- `tests/test_runtime_role_binding.py`
- `tests/test_role_kernel_write_budget.py`
