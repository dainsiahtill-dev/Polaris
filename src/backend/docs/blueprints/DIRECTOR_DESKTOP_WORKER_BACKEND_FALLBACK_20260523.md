# Director Desktop Worker Backend Fallback

Date: 2026-05-23

## Scope

The Director desktop already receives worker rows from realtime runtime state, but the v2 backend also exposes authoritative worker routes:

- `GET /v2/director/workers`
- `GET /v2/director/workers/{worker_id}`

The desktop should use those routes as a fallback evidence source instead of showing an empty worker panel whenever realtime data is missing.

## Behavior

- Frontend service layer exposes typed worker list/detail calls.
- Director workspace polls `/v2/director/workers` at a conservative interval.
- Backend worker rows are normalized into the runtime worker shape.
- Realtime worker rows take precedence over backend fallback rows for the same worker id.
- The task board shows a compact worker evidence strip with status, current task mapping, and backend errors.

## Verification

- `pmService` tests cover worker list/detail route construction.
- Director workspace tests cover backend worker normalization, realtime precedence, and rendering backend workers when realtime rows are absent.
