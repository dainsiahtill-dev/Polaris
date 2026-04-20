# Audit Diagnosis Cell

## Purpose

Diagnose runtime audit failures and provide structured trace-query capability
without mutating business source-of-truth state.

## Kind

`capability`

## Public Inputs

- `RunAuditDiagnosisCommandV1`
- `QueryAuditDiagnosisTrailV1`

## Public Outputs

- `AuditDiagnosisResultV1`
- `AuditDiagnosisCompletedEventV1`

## Depends On

- `audit.evidence`
- `storage.layout`
- `policy.workspace_guard`

## State Ownership

- `runtime/events/ws.connection.events.jsonl`

## Effects Allowed

- `fs.read:runtime/events/*`
- `fs.write:runtime/events/ws.connection.events.jsonl`
- `fs.read:workspace/**`
- `network.http_outbound:audit/*`

## Invariants

- diagnosis query paths are read-only for business state
- websocket lifecycle audit writes are append-only
- all text file writes use explicit UTF-8

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/diagnosis_engine.py`
- `internal/toolkit/*`

## Verification

- `tests/test_audit_llm_runtime.py`
- `tests/test_command_security.py`
