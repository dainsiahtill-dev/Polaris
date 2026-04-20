# Audit Verdict Cell

## Purpose

Provide deterministic review-gate verdicting and artifact read/write boundary
for QA/audit flows.

## Kind

`workflow`

## Public Inputs

- `RunAuditVerdictCommandV1`
- `QueryAuditVerdictV1`

## Public Outputs

- `AuditVerdictResultV1`
- `AuditVerdictIssuedEventV1`

## Depends On

- `audit.evidence`
- `runtime.projection`
- `policy.workspace_guard`

## State Ownership

- Transitional: no exclusive state owner declared yet

## Effects Allowed

- `fs.read:runtime/**`
- `fs.write:runtime/contracts/*`
- `fs.write:runtime/results/*`
- `fs.write:runtime/state/*`
- `fs.write:runtime/status/*`
- `fs.write:runtime/control/*`
- `fs.write:runtime/events/*`

## Invariants

- review verdict should be reproducible from artifacts and explicit policies
- artifact text/json writes must keep UTF-8 semantics
- cross-cell callers must use public contracts/service exports

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/review_gate.py`
- `internal/artifact_service.py`

## Verification

- `tests/test_artifact_service.py`
- `tests/test_integration_qa_command.py`
