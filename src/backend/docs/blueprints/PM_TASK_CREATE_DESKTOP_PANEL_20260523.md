# PM Task Create Desktop Panel Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

The backend already exposes `POST /v2/pm/tasks`, and the frontend already has a
typed `pmTaskService.create` wrapper. The PM desktop task panel could list,
search, hydrate, and inspect tasks, but it had no user-facing create control
bound to the existing backend route. This left a frontend/backend capability
gap on a core PM task workflow.

## Scope

This change is limited to PM desktop task management:

- Add a compact create form to `PMTaskPanel`.
- Use the existing `pmTaskService.create` typed frontend service.
- Show backend evidence for `POST /v2/pm/tasks`, including loading, failure,
  and created task states.
- Select the created task immediately so its auditable detail panel is visible.
- Add a regression test proving the form sends the backend payload and displays
  the created task evidence.

No backend route, graph edge, state owner, or Cell contract changes are
required.

## Architecture Sketch

```text
PMTaskPanel
  -> pmTaskService.create(payload)
      -> POST /v2/pm/tasks
      -> created task detail
  -> backendSelectedTask + onTaskSelect(created.id)
      -> TaskDetailPanel
```

The PM backend remains the source of truth for persisted tasks. The desktop
panel only submits the request and projects the returned task into the existing
detail flow.

## Assumption Register

- `pmTaskService.create` is the typed frontend service for `POST /v2/pm/tasks`.
- The backend accepts subject, description, priority, status, and acceptance
  fields through `PMTaskCreateRequest`.
- PMTaskPanel already supports backend-selected task projections from search
  results, so created tasks can reuse that path.
- Task creation should remain disabled while a create request is in flight.

## Verification Plan

- `npm run test -- PMTaskPanel pmService api.pmTaskService`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`

