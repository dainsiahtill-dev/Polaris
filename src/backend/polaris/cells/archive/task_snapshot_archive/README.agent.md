# Task Snapshot Archive

## Purpose

Archive terminal runtime task snapshots and related contracts into immutable workspace history task records.

## Kind

`capability`

## Public Inputs

- `ArchiveTaskSnapshotCommandV1`
- `GetTaskSnapshotManifestQueryV1`

## Public Outputs

- `ArchiveManifestV1`
- `TaskSnapshotArchivedEventV1`

## Depends On

- `runtime.state_owner`
- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `workspace/history/tasks/*`
- `workspace/history/index/tasks.index.jsonl`

## Effects Allowed

- `fs.read:runtime/tasks/*`
- `fs.write:workspace/history/tasks/*`
- `fs.write:workspace/history/index/tasks.index.jsonl`

## Invariants

- archived task snapshots are immutable after publication
- task archive writes must not mutate runtime source-of-truth
- all text writes use explicit UTF-8

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/archive/test_task_snapshot_archive_contracts.py`

