# Runtime State Owner

## Purpose

Act as the single writer for runtime source-of-truth state under
`runtime/contracts/*`, `runtime/state/*`, and `runtime/runs/*`.

## Kind

`capability`

## Public Inputs

- `PersistRuntimeTaskStateCommandV1`
- `PersistRuntimeContractCommandV1`
- `PersistRuntimeRunCommandV1`
- `GetRuntimeSnapshotQueryV1`
- `GetRuntimeRunQueryV1`

## Public Outputs

- `RuntimeStateWriteResultV1`
- `RuntimeStateChangedEventV1`

## Depends On

- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `runtime/contracts/*`
- `runtime/state/*`
- `runtime/runs/*`

## Effects Allowed

- `fs.read:runtime/*`
- `fs.write:runtime/contracts/*`
- `fs.write:runtime/state/*`
- `fs.write:runtime/runs/*`

## Invariants

- runtime source-of-truth writes remain single-owner
- history publication is delegated to archive cells
- all text writes use explicit UTF-8

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/architecture/test_architecture_invariants.py`
