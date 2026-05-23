# Chief Engineer Kernel Diagnostics Parity

Date: 2026-05-23

## Finding

PM and Director desktop surfaces expose role-kernel diagnostics through
role-scoped backend routes:

- `GET /v2/pm/cache-stats`
- `POST /v2/pm/cache-clear`
- `GET /v2/pm/token-budget-stats`
- `GET /v2/pm/llm-events`
- `GET /v2/director/cache-stats`
- `POST /v2/director/cache-clear`
- `GET /v2/director/token-budget-stats`
- `GET /v2/director/llm-events`

Chief Engineer desktop only surfaces generic role LLM evidence through
`/v2/role/chief_engineer/llm-events`, leaving the role without the same
cache and token-budget observability used by PM and Director.

## Contract

Chief Engineer must expose the same read-only role-kernel diagnostics and
cache-clear command under its role route:

- `GET /v2/chief-engineer/llm-events?limit=5`
- `GET /v2/chief-engineer/cache-stats`
- `POST /v2/chief-engineer/cache-clear`
- `GET /v2/chief-engineer/token-budget-stats`

The desktop evidence strip must show endpoint provenance, latest LLM event
summary, cache counters, token-budget summary, and a cache clear result without
inventing data when backend calls fail.

## Boundary

- Target cell: `chief_engineer.blueprint`
- Owned backend path: `polaris/delivery/http/v2/chief_engineer.py`
- Shared capability reused: `polaris.cells.roles.kernel.public.service`
- Frontend scope: Chief Engineer desktop evidence surface and typed role-kernel
  service mapping.
- No target-project code is generated or modified.

## Verification

- Backend route tests for Chief Engineer diagnostics parity.
- PM service tests for role-kernel base path mapping.
- Chief Engineer workspace tests for endpoint provenance and cache clear.
- Focused PM/Chief Engineer/Director frontend regression.
