# Factory Archive

## Purpose

Archive terminal factory outputs into immutable workspace history factory records.

## Kind

`capability`

## Public Inputs

- `ArchiveFactoryRunCommandV1`
- `GetFactoryArchiveManifestQueryV1`

## Public Outputs

- `ArchiveManifestV1`
- `FactoryArchivedEventV1`

## Depends On

- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `workspace/history/factory/*`
- `workspace/history/index/factory.index.jsonl`

## Effects Allowed

- `fs.read:workspace/factory/*`
- `fs.write:workspace/history/factory/*`
- `fs.write:workspace/history/index/factory.index.jsonl`

## Invariants

- factory archive publication is append-only
- runtime state remains untouched by factory archiving
- all text writes use explicit UTF-8

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/archive/test_factory_archive_contracts.py`

