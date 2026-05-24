# Director Workflow Blueprint Ready Gate - Desktop

## Context

Factory desktop separates PM planning, Chief Engineer blueprinting, and Director execution. Director diagnostics previously treated any workflow task with no dependencies as ready, even when the task carried no Chief Engineer blueprint evidence. That allowed a workflow task to appear executable before the CE handoff layer had produced or attached a blueprint.

## Decision

Director readiness will keep local/manual tasks executable with the existing dependency rule, but workflow-projected tasks must include at least one blueprint reference before they count as ready:

- `blueprint_id`
- `blueprint_path`
- `runtime_blueprint_path`

Workflow tasks that are otherwise ready but lack blueprint evidence are reported separately as `missing_blueprint_task_ids` and block Director orchestration with `director_ready_tasks_missing_blueprints`.

## Data Flow

```text
PM plan/task rows
  -> Chief Engineer generates blueprint refs
  -> Runtime projection exposes Director workflow rows
  -> /v2/director/diagnostics validates dependency + blueprint evidence
  -> /v2/director/run fails closed when workflow-ready tasks lack CE blueprint refs
```

## Verification Plan

- Add backend regression tests for workflow task readiness with and without blueprint evidence.
- Update frontend diagnostics labels/types so the new blocker is visible and disables execution controls.
- Run targeted backend pytest, Ruff, format, mypy, and frontend component/service tests.
