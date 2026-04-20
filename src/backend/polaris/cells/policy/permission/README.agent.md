# Permission Policy

## Purpose

Own role capability matrix evaluation and permission decisions for tool, command, write, and execution actions.

## Kind

`policy`

## Public Contracts

- commands: EvaluatePermissionCommandV1
- queries: QueryPermissionMatrixV1
- events: PermissionDeniedEventV1
- results: PermissionDecisionResultV1
- errors: PermissionPolicyErrorV1

## Depends On

- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `workspace/policy/permission/*`

## Effects Allowed

- `fs.read:workspace/**`
- `fs.write:workspace/policy/permission/*`

## Verification

- `tests/test_permission_service.py`
- `tests/test_permission_role_inheritance.py`
- `tests/test_permission_conditions.py`
