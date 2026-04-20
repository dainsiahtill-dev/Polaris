# Workspace Guard

## Purpose

Enforce workspace path legality, scope constraints, and write-guard checks.

## Kind

`policy`

## Public Inputs

- `WorkspaceWriteGuardQueryV1`
- `WorkspaceArchiveWriteGuardQueryV1`

## Public Outputs

- `WorkspaceGuardDecisionV1`
- `WorkspaceGuardViolationEventV1`

## Depends On

- `audit.evidence`

## State Ownership

- `workspace/policy/*`

## Effects Allowed

- `fs.read:workspace/**`
- `fs.read:runtime/**`

## Invariants

- guard evaluation remains read-only
- unsafe paths are rejected before writes occur
- all path decisions are workspace scoped

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/architecture/test_polaris_kernel_fs_guard.py`

