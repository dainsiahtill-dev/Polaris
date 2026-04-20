# Resident Autonomy Cell

## Objective
Provide long-running resident autonomy capability, including decision trace,
goal governance, evidence bundling, and improvement loop execution.

## Boundaries
- Owns resident autonomy runtime internals under `internal/**`.
- Owns resident delivery endpoint `polaris/delivery/http/v2/resident.py`.
- Exposes cross-cell access only via `public/contracts.py`.

## State Ownership
- `runtime/state/resident/*`
- `runtime/resident/*`

## Allowed Effects
- `fs.read:workspace/**`
- `fs.read:runtime/**`
- `fs.write:runtime/state/resident/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `process.spawn:resident/*`
- `ws.outbound:resident/*`

## Public Contracts
- `RunResidentCycleCommandV1`
- `RecordResidentEvidenceCommandV1`
- `QueryResidentStatusV1`
- `ResidentCycleCompletedEventV1`
- `ResidentAutonomyResultV1`
- `ResidentAutonomyErrorV1`
