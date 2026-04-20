# Runtime Projection

## Purpose

Build read-only runtime status projections and transport payloads without hidden writes.

## Kind

`projection`

## Public Inputs

- `RuntimeProjectionQueryV1`

## Public Outputs

- `RuntimeProjectionResultV1`
- `RuntimeProjectedEventV1`
- `RuntimeObserverEventV1`

## Depends On

- `runtime.task_runtime`
- `runtime.state_owner`
- `audit.evidence`

## State Ownership

- None

## Effects Allowed

- `fs.read:runtime/*`
- `fs.read:workspace/history/*`
- `ws.outbound:runtime/*`

## Invariants

- query paths remain read-only
- projection may not create source-of-truth writes
- all text reads use explicit UTF-8
- observer-facing reasoning/tool events must be expressed via structured projection contracts, not inferred only from free-form messages

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/test_websocket_signal_hub.py`
