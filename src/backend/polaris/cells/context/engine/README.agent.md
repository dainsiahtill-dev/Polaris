# Context Engine

## Purpose

Assemble role-facing execution context through graph-constrained context lookup.

## Kind

`capability`

## Public Inputs

- `BuildRoleContextCommandV1`
- `ResolveRoleContextQueryV1`

## Public Outputs

- `RoleContextResultV1`
- `ContextResolvedEventV1`

## Depends On

- `context.catalog`
- `policy.workspace_guard`

## State Ownership

- None

## Effects Allowed

- `fs.read:workspace/**`
- `fs.read:docs/graph/**`

## Invariants

- context lookup remains graph-constrained
- context assembly does not widen authorization radius
- context assembly remains read-only

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/architecture/test_architecture_invariants.py`

