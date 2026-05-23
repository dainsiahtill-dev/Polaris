# Chief Engineer Desktop Blueprint Status Check

Date: 2026-05-23

## Scope

Chief Engineer exposes a task-scoped blueprint status query at:

- `GET /v2/chief-engineer/blueprints/status?task_id={task_id}`

The desktop should let operators inspect that backend status before deciding to generate a new blueprint.

## Behavior

- Each task in the Chief Engineer "pending blueprint" section can query backend blueprint status.
- The result panel displays endpoint provenance, returned status, blueprint id, and summary.
- If the backend returns a blueprint payload, the existing blueprint detail viewer is populated from that response.
- The check is read-only and does not call the blueprint generation command.

## Verification

- Chief Engineer workspace tests cover the status-query button, endpoint call, rendered status evidence, populated detail viewer, and no generation POST side effect.
