# Chief Engineer Route Graph Ownership

Date: 2026-05-23

## Finding

`polaris/delivery/http/v2/chief_engineer.py` is the active backend route for
the Chief Engineer desktop workspace, but it was not declared in the graph
catalog or in the `chief_engineer.blueprint` cell manifest. PM and Director v2
routes already have explicit graph ownership, so Chief Engineer was the missing
role surface in the PM/Chief Engineer/Director desktop route set.

A related manifest drift existed in `cell.yaml`: the cell writes persisted
blueprints under `runtime/blueprints/*`, while the manifest only declared
`runtime/state/blueprints/*`.

## Contract

Chief Engineer desktop backend route ownership is:

- Cell: `chief_engineer.blueprint`
- Module: `polaris.delivery.http.v2.chief_engineer`
- Owned path: `polaris/delivery/http/v2/chief_engineer.py`

The cell manifest must also declare the persisted blueprint state/effect:

- State owner: `runtime/blueprints/*`
- Effect: `fs.write:runtime/blueprints/*`

## Data Flow

Chief Engineer desktop -> `/v2/chief-engineer/*` delivery route ->
`chief_engineer.blueprint` public command/query contracts ->
`BlueprintPersistence` -> `runtime/blueprints/*.json`.

The route remains a delivery adapter; blueprint generation and status lookup
remain owned by the Chief Engineer blueprint cell public contracts.

## Verification

- `src/backend/polaris/tests/test_cell_yaml_governance.py`
- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
- `docs/governance/ci/scripts/run_catalog_governance_gate.py --mode audit-only`
