# Role Kernel Desktop Diagnostics Contract

Date: 2026-05-23

## Scope

Wire existing PM and Director v2 Kernel diagnostics routes into the desktop role surfaces:

- `GET /v2/pm/cache-stats`
- `POST /v2/pm/cache-clear`
- `GET /v2/pm/token-budget-stats`
- `GET /v2/pm/llm-events`
- `GET /v2/director/cache-stats`
- `POST /v2/director/cache-clear`
- `GET /v2/director/token-budget-stats`
- `GET /v2/director/llm-events`
- `GET /v2/director/tasks/{task_id}/llm-events`

## Desktop Behavior

- PM startup diagnostics must show the backend cache, token-budget, and recent LLM-event snapshots alongside LanceDB, LLM, and workspace readiness.
- Director workspace must expose a compact Kernel diagnostics strip beside its capability matrix, including global LLM event evidence from `/v2/director/llm-events`.
- Cache clearing must call the backend route and reload the stats after success.
- The desktop must not synthesize sample stats when the backend route fails; it should show the backend error.

## Verification

- `pmService` tests cover route construction and query encoding.
- PM diagnostics tests cover cache/token/LLM-event rendering and cache-clear behavior.
- Director workspace tests cover cache/token/LLM-event rendering and cache-clear behavior.
