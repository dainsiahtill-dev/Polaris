# Factory Control Actions Desktop Blueprint

Date: 2026-05-24

## Scope

Complete the Factory run control surface used by the desktop PM / Chief Engineer / Director orchestration view.

## Current Gap

`FactoryControlRequest` declares these valid actions:

- `pause`
- `resume`
- `cancel`
- `retry_phase`
- `retry_from_checkpoint`

The HTTP router currently only implements `cancel`, so valid desktop control actions can return `501`.

## Design

Use the existing `factory.pipeline` Cell boundary:

- Router: `polaris/delivery/http/routers/factory.py`
- Service: `polaris/cells/factory/pipeline/internal/factory_run_service.py`

Data flow:

```text
Desktop Factory control
  -> POST /v2/factory/runs/{run_id}/control
  -> FactoryRunService control method
  -> FactoryStore state update + audit event
  -> FactoryRunStatusContract projection
```

## Decisions

- `pause` reuses `FactoryRunService.execute_pause`.
- `resume` reuses `FactoryRunService.execute_resume`.
- `retry_from_checkpoint` sets the run to `RECOVERING` from the last known checkpoint even after a failed terminal status.
- `retry_phase` resolves the requested `RunPhase` to a configured Factory stage before setting `RECOVERING`.
- Unsupported target phases return a structured `400` instead of silently choosing an unrelated stage.

## Verification

- Update router tests that previously asserted `pause` was unsupported.
- Add retry control tests for both mocked router and real service paths.
- Run targeted Ruff, Mypy and Pytest gates.
