# Run Archive

## Purpose

Archive terminal runtime run artifacts into immutable workspace history run records.

## Kind

`capability`

## Public Inputs

- `ArchiveRunCommandV1`
- `ListHistoryRunsQueryV1`
- `GetArchiveManifestQueryV1`

## Public Outputs

- `ArchiveManifestV1`
- `HistoryRunsResultV1`
- `RunArchivedEventV1`

## Depends On

- `runtime.state_owner`
- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `workspace/history/runs/*`
- `workspace/history/index/runs.index.jsonl`

## Effects Allowed

- `fs.read:runtime/runs/*`
- `fs.write:workspace/history/runs/*`
- `fs.write:workspace/history/index/runs.index.jsonl`

## Invariants

- runtime source-of-truth remains unchanged after archiving
- archive publication is append-only
- all text writes use explicit UTF-8

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/archive/test_run_archive_contracts.py`

