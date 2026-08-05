# Runtime Projection

## Purpose

Build read-only runtime status projections and transport payloads without hidden writes.

## Kind

`projection`

## Public Inputs

- `RuntimeProjectionQueryV1`
- `ProjectOutcomeQueryV1`
- `ProjectOutcomeFactoryOwnerQueryV1`

## Public Outputs

- `RuntimeProjectionResultV1`
- `RuntimeProjectedEventV1`
- `RuntimeObserverEventV1`
- `DirectorStatusObservationV1`
- `FactoryChainOwnerObservationV1`
- `ProjectOutcomeFactoryOwnerBindingV1`

## Depends On

- `runtime.task_runtime`
- `runtime.state_owner`
- `audit.evidence`

Factory chain facts arrive through a bootstrap-bound projection-owned port;
this Cell has no static dependency on `factory.pipeline`.
Director status facts use the same composition pattern, so this Cell has no
static dependency on `director.execution`.

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
- Factory chain owner port must be bootstrap-bound exactly once; unbound or conflicting binding fails closed
- Director status owner port must be bootstrap-bound exactly once; unbound observations remain explicitly unavailable and never imply ready or success
- callers cannot provide Factory DTOs, chain hashes, evidence, or observation ports

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/test_realtime_single_rail_static_guard.py`
- `tests/test_runtime_projection_observability.py`
- `polaris/cells/runtime/projection/tests/test_director_status_owner_binding.py`
- `polaris/cells/runtime/projection/tests/test_gr1c_dependency_cut.py`
