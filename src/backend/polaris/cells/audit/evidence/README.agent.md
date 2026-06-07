# Evidence Audit

## Purpose

Own append-only runtime evidence events, integrity verification, and evidence query/export.

## Kind

`capability`

## Public Inputs

- `AppendEvidenceEventCommandV1` through `append_evidence_event(...)`
- `QueryEvidenceEventsV1`
- `VerifyEvidenceChainV1`

## Public Outputs

- `EvidenceQueryResultV1`
- `EvidenceVerificationResultV1`
- `EvidenceAppendedEventV1`

## Depends On

- `policy.workspace_guard`

## State Ownership

- `runtime/evidence/*`

## Effects Allowed

- `fs.read:runtime/*`
- `fs.write:runtime/evidence/*`

## Public Service

- `append_evidence_event(AppendEvidenceEventCommandV1) -> EvidenceAppendedEventV1`
  appends one UTF-8 JSONL event through KernelOne FS under
  `runtime/evidence/<kind>.jsonl`. Cross-cell callers must use this public
  service and must not write evidence logs directly.

## Invariants

- evidence storage is append-only
- evidence verification never mutates source data
- all text writes use explicit UTF-8

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. `public/service.py`
5. owned implementation files only if needed

## Verification

- `polaris/cells/audit/evidence/tests/test_evidence_contract.py`
- `tests/test_log_pipeline_storage_layout.py`
