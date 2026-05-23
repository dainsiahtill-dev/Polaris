# Chief Engineer Blueprint Active Workspace And Diagnostics Refresh

Date: 2026-05-23

## Finding

Chief Engineer blueprint list/detail routes already had active workspace
coverage, but the command and status tests did not prove that
`settings.workspace_path` is used when `settings.workspace` still points at the
Polaris repository. The desktop workbench also updated the visible blueprint
evidence after a generate/status action without refreshing the diagnostics
snapshot, so the right-hand diagnostics panel could still report `0/0` and no
Director handoff after a successful backend blueprint result.

## Contract

Chief Engineer desktop blueprint surfaces must use the same active workspace
contract as the other role workspaces:

1. `settings.workspace_path`
2. `settings.workspace`

Affected backend surfaces:

- `POST /v2/chief-engineer/blueprints`
- `GET /v2/chief-engineer/blueprints/status`

Affected frontend surfaces:

- `ChiefEngineerWorkspace` blueprint generate action
- `ChiefEngineerWorkspace` blueprint status action
- `ChiefEngineerWorkspace` diagnostics panel

## Data Flow

Desktop selected workspace -> shared delivery `active_workspace_value()` ->
Chief Engineer command/query contracts -> persisted runtime blueprint evidence
-> workbench blueprint state -> diagnostics refresh from
`/v2/chief-engineer/diagnostics`.

## Graph Boundary

Chief Engineer blueprint generation and status remain delegated through
`chief_engineer.blueprint` public command/query contracts. The desktop route is
a delivery adapter and does not add a second persistence implementation.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`
- `src/frontend/src/services/__tests__/chiefEngineerService.test.ts`
