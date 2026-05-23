# Director Desktop Task LLM Event Evidence

Date: 2026-05-23

## Scope

The Director backend exposes task-scoped LLM event history at:

- `GET /v2/director/tasks/{task_id}/llm-events`

The Director desktop task detail panel should surface this evidence beside task contract, worker, and file activity data.

## Behavior

- Selecting a Director task loads up to 25 backend LLM events for that task.
- The task detail panel displays the endpoint provenance, total/error/retry stats, and the latest event rows.
- Loading, empty, and backend-error states are explicit.
- The panel is read-only and does not mutate runtime state.

## Verification

- Workspace tests cover selection-triggered backend service calls and rendering returned LLM event rows.
- Task panel tests cover read-only rendering of LLM event stats and latest events.
