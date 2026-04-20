# Events Fact Stream

## Purpose

Provide append-only runtime fact stream ingestion, fanout, and query infrastructure for PM, Director, QA and realtime projection consumers.

## Kind

`capability`

## Public Contracts

- commands: AppendFactEventCommandV1
- queries: QueryFactEventsV1
- events: FactEventAppendedV1
- results: FactStreamQueryResultV1
- errors: FactStreamErrorV1

## Depends On

- `policy.workspace_guard`
- `audit.evidence`
- `runtime.projection`

## State Ownership

- `runtime/events/*`

## Effects Allowed

- `fs.read:runtime/events/*`
- `fs.write:runtime/events/*`
- `ws.outbound:runtime/*`

## Verification

- `tests/test_runtime_event_fanout.py`
- `tests/test_realtime_hub_v2.py`
- `tests/test_websocket_signal_hub.py`
