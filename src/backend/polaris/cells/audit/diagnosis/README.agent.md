# Audit Diagnosis Cell

## Purpose

Diagnose runtime audit failures and provide structured trace-query capability
without mutating business source-of-truth state.

## Kind

`capability`

## Public Inputs

- `RunAuditDiagnosisCommandV1`
- `QueryAuditDiagnosisTrailV1`
- `QueryExactRunCausalAuditV1`

## Public Outputs

- `AuditDiagnosisResultV1`
- `AuditDiagnosisCompletedEventV1`

## Delivery API

- `GET /v2/audit/runs/{factory_run_id}/causal`
- Uses backend instance workspace binding; arbitrary workspace injection is
  forbidden.

## Depends On

- `audit.evidence`
- `context.engine`
- `control_plane.run_ledger`
- `factory.pipeline`
- `runtime.task_runtime`
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
- exact-run diagnosis returns one current `root_cause_code`, one owner Cell,
  one retry boundary, and separates historical errors from the current blocker
- role/tool failures require final provider request evidence; its absence is an
  explicit evidence gap, never a guessed model diagnosis
- explicit workspace resolves locally before environment or backend hints;
  optional loopback hints disable environment proxies
- QA/Director recovery never restarts PM/CE unless their own stage is the
  exact current failed boundary
- websocket lifecycle audit writes are append-only
- all text file writes use explicit UTF-8

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/diagnosis_engine.py`
- `internal/exact_run_causal_audit.py`
- `internal/toolkit/*`

## Verification

- `tests/test_audit_llm_runtime.py`
- `tests/test_command_security.py`
- `polaris/cells/audit/diagnosis/internal/tests/test_exact_run_causal_audit.py`
- `polaris/cells/audit/diagnosis/internal/tests/test_audit_runtime_root_resolution.py`
- `polaris/tests/unit/delivery/http/test_audit_router_exact_run_causal.py`
