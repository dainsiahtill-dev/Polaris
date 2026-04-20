# Evidence Audit

## Purpose

Own append-only runtime evidence events, integrity verification, and evidence query/export.

## Kind

`capability`

## Public Inputs

- `AppendEvidenceEventCommandV1`
- `QueryEvidenceEventsV1`
- `VerifyEvidenceChainV1`

## Public Outputs

- `EvidenceQueryResultV1`
- `EvidenceVerificationResultV1`
- `EvidenceAppendedEventV1`

## Depends On

- `policy.workspace_guard`

## State Ownership

- `runtime/events/*`

## Effects Allowed

- `fs.read:runtime/*`
- `fs.write:runtime/events/*`

## Invariants

- evidence storage is append-only
- evidence verification never mutates source data
- all text writes use explicit UTF-8

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/test_log_pipeline_storage_layout.py`

