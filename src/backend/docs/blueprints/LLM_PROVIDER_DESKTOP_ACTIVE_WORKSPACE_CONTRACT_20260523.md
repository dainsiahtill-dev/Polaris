# LLM Provider Desktop Active Workspace Contract

Date: 2026-05-23

## Finding

LLM configuration, runtime-status, provider health, and provider model routes
used `settings.workspace` directly when deriving KernelOne cache/runtime paths
or resolving provider config. In Electron desktop sessions,
`settings.workspace_path` is the selected target project workspace while
`settings.workspace` can still point at the Polaris repository or a legacy
value.

This can make PM, Chief Engineer, and Director settings/readiness surfaces load
or test LLM configuration from the wrong workspace.

## Contract

LLM delivery routes must resolve workspace with this precedence:

1. `settings.workspace_path`
2. `settings.workspace`

Affected backend surfaces:

- `GET /llm/config`
- `GET /v2/llm/config`
- `POST /llm/config`
- `POST /v2/llm/config`
- `GET /llm/runtime-status`
- `GET /v2/llm/runtime-status`
- `GET /llm/runtime-status/{role_id}`
- `GET /v2/llm/runtime-status/{role_id}`
- `POST /llm/providers/{provider_id}/health`
- `POST /v2/llm/providers/{provider_id}/health`
- `POST /llm/providers/{provider_id}/models`
- `POST /v2/llm/providers/{provider_id}/models`

## Data Flow

Desktop selected workspace -> `settings.workspace_path` -> delivery active
workspace resolver -> KernelOne cache/runtime helpers -> existing
`llm.provider_config` and `llm.provider_runtime` public services.

Legacy callers that only populate `settings.workspace` continue through the
fallback path.

## Graph Boundary

Current graph ownership keeps `polaris/delivery/http/routers/llm.py` and
`polaris/delivery/http/routers/providers.py` under `llm.control_plane`.
Provider context resolution remains delegated to
`polaris.cells.llm.provider_config.public.service`, and provider actions remain
delegated to `polaris.cells.llm.provider_runtime.public.service`.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_llm_v2.py`
- `src/backend/polaris/tests/unit/delivery/http/routers/test_providers_v2.py`
