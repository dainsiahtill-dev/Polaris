# Polaris API v1 to v2 Migration Guide

> For API consumers migrating from legacy v1 endpoints to the unified `/v2` namespace.
> Last updated: 2026-05-06

---

## Table of Contents

1. [Overview](#1-overview)
2. [Breaking Changes Summary](#2-breaking-changes-summary)
3. [Endpoint Mapping Table](#3-endpoint-mapping-table)
4. [Error Format Changes](#4-error-format-changes)
5. [Authentication & RBAC Changes](#5-authentication--rbac-changes)
6. [Realtime Transport Changes](#6-realtime-transport-changes)
7. [Code Examples](#7-code-examples)
8. [Deprecation Timeline](#8-deprecation-timeline)
9. [FAQ](#9-faq)

---

## 1. Overview

Polaris v2 API consolidates fragmented legacy endpoints (`/pm/chat`, `/director/*`, `/factory/*`, etc.) into a unified `/v2/*` namespace under `polaris/delivery/http/v2/`. The goals of the migration are:

- **Unified routing**: All new endpoints live under `/v2/` with consistent tagging and OpenAPI documentation.
- **Structured errors**: Every error response follows the ADR-003 contract (`{error: {code, message, details}}`).
- **RBAC by default**: All v2 routes require authentication; sensitive operations require explicit roles.
- **Realtime single rail**: product realtime streaming uses Nat-JetStream + `/v2/ws/runtime` runtime.v2 WebSocket only. Legacy HTTP stream routes fail closed and must not be used for UI realtime.
- **Cell-aware architecture**: v2 routes delegate to Cell public services rather than inlining business logic.

If you are building a new integration, start directly with v2. If you have an existing v1 client, use this guide to migrate.

---

## 2. Breaking Changes Summary

| Change | v1 Behavior | v2 Behavior | Impact |
|--------|-------------|-------------|--------|
| **Error format** | `{"detail": "string"}` | `{"error": {"code": "...", "message": "...", "details": {}}}` | All error parsing logic must be updated. |
| **Realtime transport** | Ad-hoc HTTP stream endpoints | Nat-JetStream + `/v2/ws/runtime` runtime.v2 WebSocket | Clients must subscribe to WebSocket channels; do not use HTTP stream routes. |
| **Authentication** | Optional on some routes | Required on **all** `/v2/*` routes | Clients must send `Authorization: Bearer <token>` on every request. |
| **Role chat** | `POST /v2/pm/chat` only | `POST /v2/role/{role}/chat` for all 5 roles | PM chat moved to unified role entry. |
| **Director status** | Single monolithic payload | `source=auto\|local\|workflow` parameter | Clients must choose projection source. |
| **Factory runs** | `POST /factory/runs` | `POST /v2/factory/runs` | Prefix change only; response models enriched. |
| **Deprecated shims** | `app/`, `core/`, `api/` roots | Migrated to `polaris/delivery/http/v2/` | Old import paths removed. |

### Removed Routes (no v2 equivalent)

The following legacy routes are removed without replacement:

- `/pm/start_loop` — replaced by `/v2/pm/start`
- `/_merge_director_status` re-export — import `merge_director_status` from `polaris.cells.runtime.projection.public.service` directly
- All routes under legacy `app/`, `core/`, `api/` roots

---

## 3. Endpoint Mapping Table

### 3.1 Role Chat (Unified Entry)

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| `POST /v2/pm/chat` | `POST /v2/role/pm/chat` | Unified role chat entry. All 5 roles supported. |
| `POST /v2/pm/chat/stream` | `/v2/ws/runtime` role/session channels | Legacy HTTP stream route is fail-closed; use runtime.v2 WebSocket events. |
| `GET /v2/pm/chat/status` | `GET /v2/role/pm/chat/status` | LLM readiness check per role. |
| — | `GET /v2/role/chat/roles` | **New**: list all registered roles. |
| — | `GET /v2/role/chat/ping` | **New**: health check for role chat router. |

**Supported roles**: `pm`, `architect`, `chief_engineer`, `director`, `qa`

### 3.2 PM

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| `POST /pm/start_loop` | `POST /v2/pm/start` | Deprecated shim emits `DeprecationWarning`. |
| `POST /pm/run_once` | `POST /v2/pm/run_once` | No change in behavior. |
| `POST /pm/stop` | `POST /v2/pm/stop` | No change in behavior. |
| `GET /pm/status` | `GET /v2/pm/status` | No change in behavior. |
| — | `POST /v2/pm/run` | **New**: unified orchestration entry. |
| — | `GET /v2/pm/runs/{run_id}` | **New**: query PM orchestration run status. |
| — | `GET /v2/pm/llm-events` | **New**: PM LLM call events. |
| — | `GET /v2/pm/cache-stats` | **New**: cache statistics. |
| — | `POST /v2/pm/cache-clear` | **New**: clear LLM cache. |
| — | `GET /v2/pm/token-budget-stats` | **New**: token budget statistics. |

### 3.3 Director

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| `POST /director/start` | `POST /v2/director/start` | No change in behavior. |
| `POST /director/stop` | `POST /v2/director/stop` | No change in behavior. |
| `GET /director/status` | `GET /v2/director/status` | Accepts `source=auto\|local\|workflow`. Default `local`. |
| `POST /director/tasks` | `POST /v2/director/tasks` | No change in behavior. |
| `GET /director/tasks` | `GET /v2/director/tasks` | Accepts `source=auto\|local\|workflow`. Default `auto`. |
| `GET /director/tasks/{task_id}` | `GET /v2/director/tasks/{task_id}` | No change in behavior. |
| `POST /director/tasks/{task_id}/cancel` | `POST /v2/director/tasks/{task_id}/cancel` | No change in behavior. |
| `GET /director/workers` | `GET /v2/director/workers` | No change in behavior. |
| `GET /director/workers/{worker_id}` | `GET /v2/director/workers/{worker_id}` | No change in behavior. |
| — | `POST /v2/director/run` | **New**: unified orchestration entry. |
| — | `GET /v2/director/runs/{run_id}` | **New**: query Director run status. |
| — | `GET /v2/director/llm-events` | **New**: Director LLM call events. |

### 3.4 Unified Orchestration

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| — | `POST /v2/orchestration/runs` | **New**: create an orchestration run. |
| — | `GET /v2/orchestration/runs` | **New**: list orchestration runs. |
| — | `GET /v2/orchestration/runs/{run_id}` | **New**: get run status. |
| — | `GET /v2/orchestration/runs/{run_id}/tasks` | **New**: list run tasks. |
| — | `POST /v2/orchestration/runs/{run_id}/signal` | **New**: send control signal (`cancel`, `pause`, `resume`, `retry`, `skip`). |
| — | `DELETE /v2/orchestration/runs/{run_id}` | **New**: cancel run. |

### 3.5 Factory

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| `GET /factory/runs` | `GET /v2/factory/runs` | Response uses `FactoryRunList` model. |
| `POST /factory/runs` | `POST /v2/factory/runs` | Response uses `FactoryRunStatusContract` model. |
| `GET /factory/runs/{run_id}` | `GET /v2/factory/runs/{run_id}` | Response enriched with `phase`, `roles`, `gates`. |
| `GET /factory/runs/{run_id}/events` | `GET /v2/factory/runs/{run_id}/events` | No change in behavior. |
| `GET /factory/runs/{run_id}/audit-bundle` | `GET /v2/factory/runs/{run_id}/audit-bundle` | No change in behavior. |
| `GET /factory/runs/{run_id}/stream` | `/v2/ws/runtime` with `event.factory:<run_id>` | Legacy HTTP stream route is fail-closed; Factory realtime is WebSocket-only. |
| `POST /factory/runs/{run_id}/control` | `POST /v2/factory/runs/{run_id}/control` | Only `cancel` action supported. |
| `GET /factory/runs/{run_id}/artifacts` | `GET /v2/factory/runs/{run_id}/artifacts` | No change in behavior. |

### 3.6 Agent / Session

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| `GET /agent/sessions` | `GET /v2/agent/sessions` | Response uses `AgentSessionListResponse`. |
| `GET /agent/sessions/{session_id}` | `GET /v2/agent/sessions/{session_id}` | Response uses `AgentSessionResponse`. |
| `GET /agent/sessions/{session_id}/memory/search` | `GET /v2/agent/sessions/{session_id}/memory/search` | Response uses `AgentMemorySearchResponse`. |
| `GET /agent/sessions/{session_id}/memory/artifacts/{artifact_id}` | `GET /v2/agent/sessions/{session_id}/memory/artifacts/{artifact_id}` | Response uses `AgentArtifactResponse`. |
| `GET /agent/sessions/{session_id}/memory/episodes/{episode_id}` | `GET /v2/agent/sessions/{session_id}/memory/episodes/{episode_id}` | Response uses `AgentEpisodeResponse`. |
| `GET /agent/sessions/{session_id}/memory/state` | `GET /v2/agent/sessions/{session_id}/memory/state` | Response uses `AgentMemoryStateResponse`. |
| `POST /agent/sessions/{session_id}/messages` | `POST /v2/agent/sessions/{session_id}/messages` | Response uses `AgentMessageResponse`. |
| `POST /agent/sessions/{session_id}/messages/stream` | `/v2/ws/runtime` role-session/dialogue channels | Legacy HTTP stream route is fail-closed; role-session realtime is WebSocket-only. |
| `DELETE /agent/sessions/{session_id}` | `DELETE /v2/agent/sessions/{session_id}` | Response uses `SessionDeleteResponse`. |
| `POST /agent/turn` | `POST /v2/agent/turn` | Response uses `AgentTurnResponse`. |

### 3.7 Services (New in v2)

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| — | `POST /v2/services/tasks` | Create background task. |
| — | `GET /v2/services/tasks/{task_id}` | Get background task. |
| — | `GET /v2/services/tasks` | List background tasks. |
| — | `POST /v2/services/todos` | Create todo. |
| — | `GET /v2/services/todos` | List todos. |
| — | `GET /v2/services/todos/summary` | Todo summary. |
| — | `POST /v2/services/todos/{item_id}/done` | Mark todo done. |
| — | `GET /v2/services/tokens/status` | Token budget status. |
| — | `POST /v2/services/tokens/record` | Record token usage. |
| — | `POST /v2/services/security/check` | Check command safety. |
| — | `GET /v2/services/transcript` | Get transcript messages. |
| — | `GET /v2/services/transcript/session` | Transcript session info. |
| — | `POST /v2/services/transcript/message` | Record transcript message. |

### 3.8 Resident (New in v2)

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| — | `GET /v2/resident/status` | Resident status. |
| — | `POST /v2/resident/start` | Start resident. |
| — | `POST /v2/resident/stop` | Stop resident. |
| — | `POST /v2/resident/tick` | Tick resident. |
| — | `GET /v2/resident/identity` | Get identity. |
| — | `PATCH /v2/resident/identity` | Patch identity. |
| — | `GET /v2/resident/goals` | List goals. |
| — | `POST /v2/resident/goals` | Create goal. |
| — | `POST /v2/resident/goals/{goal_id}/run` | Run goal. |

### 3.9 Observability (New in v2)

| v1 Endpoint | v2 Endpoint | Notes |
|-------------|-------------|-------|
| — | `GET /v2/observability/status` | Observability status. |
| — | `GET /v2/observability/services` | List tracked services. |
| — | `GET /v2/observability/metrics` | Aggregated metrics. |
| — | `GET /v2/observability/health` | Health status. |
| — | `GET /v2/observability/health/backend` | Backend health. |
| — | `WS /v2/observability/ws/events` | Real-time WebSocket events. |
| — | `POST /v2/observability/metrics/export` | Export metrics to JSON. |

---

## 4. Error Format Changes

### 4.1 v1 Error Format

```json
{
  "detail": "Task not found"
}
```

### 4.2 v2 Error Format (ADR-003)

```json
{
  "error": {
    "code": "RUN_NOT_FOUND",
    "message": "Run run-123 not found",
    "details": {
      "run_id": "run-123"
    }
  }
}
```

### 4.3 How to Adapt

**Python (requests)**

```python
import requests

# v1 (old)
response = requests.get("http://localhost:49977/director/tasks/invalid-id")
if response.status_code == 404:
    error_msg = response.json()["detail"]  # "Task not found"

# v2 (new)
response = requests.get(
    "http://localhost:49977/v2/director/tasks/invalid-id",
    headers={"Authorization": "Bearer <token>"}
)
if response.status_code == 404:
    error_body = response.json()["error"]   # dict with code, message, details
    code = error_body["code"]               # "RUN_NOT_FOUND"
    message = error_body["message"]         # "Run ... not found"
    details = error_body.get("details", {}) # {"run_id": "..."}
```

**JavaScript (fetch)**

```javascript
// v1 (old)
const res = await fetch('/director/tasks/invalid-id');
if (!res.ok) {
  const { detail } = await res.json();
  console.error(detail);
}

// v2 (new)
const res = await fetch('/v2/director/tasks/invalid-id', {
  headers: { 'Authorization': 'Bearer <token>' }
});
if (!res.ok) {
  const { error } = await res.json();
  console.error(error.code, error.message, error.details);
}
```

### 4.4 Common Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `INVALID_REQUEST` | 400 | Missing or invalid input |
| `UNSUPPORTED_ROLE` | 400 | Role not in registered list |
| `VALIDATION_ERROR` | 422 | Pydantic validation failure |
| `RUNTIME_ROLES_NOT_READY` | 409 | Required LLM roles not ready |
| `PM_ROLE_NOT_CONFIGURED` | 409 | PM LLM not configured |
| `RUN_NOT_FOUND` | 404 | Factory/orchestration run missing |
| `SESSION_NOT_FOUND` | 404 | Agent session missing |
| `GENERATION_FAILED` | 500 | LLM generation error |
| `INTERNAL_ERROR` | 500 | Unhandled exception |

---

## 5. Authentication & RBAC Changes

### 5.1 All v2 Routes Require Auth

Every endpoint under `/v2/*` requires an `Authorization: Bearer <token>` header. There are no anonymous v2 endpoints.

```bash
# v2 request
curl -H "Authorization: Bearer $POLARIS_TOKEN" \
     http://localhost:49977/v2/pm/status
```

### 5.2 Role Hierarchy

| Role | Level | Description |
|------|-------|-------------|
| `viewer` | 1 | Can read status and query data |
| `developer` | 2 | Can trigger runs and manage tasks |
| `admin` | 3 | Can clear caches and manage system state |

### 5.3 Role-Restricted Endpoints

Some endpoints require specific roles in addition to authentication:

```python
# Admin only
POST /v2/role/cache-clear      # requires ADMIN or DEVELOPER
POST /v2/pm/cache-clear        # requires auth (any role)
```

The server ignores client-supplied `X-User-Role` headers. Roles are bound server-side via `auth_context.metadata["roles"]`.

### 5.4 Permission-Based Access

```python
from polaris.delivery.http.dependencies import require_permission

# Route protected by custom permission
@router.get("/sensitive", dependencies=[Depends(require_permission("sensitive:read"))])
```

---

## 6. Realtime Transport Changes

### 6.1 Canonical Runtime Channels

All product realtime events are published to Nat-JetStream and delivered to
clients through `/v2/ws/runtime` using the `runtime.v2` WebSocket protocol.
Legacy HTTP stream routes are retained only as fail-closed compatibility
surfaces.

| Channel | Purpose |
|---------|---------|
| `runtime_events` | task lifecycle, role state, workflow events |
| `llm` | LLM call state, content previews, token/cost metadata |
| `process` | process/runtime diagnostics |
| `dialogue` | role-session dialogue events |
| `event.factory` | all Factory events in the active workspace |
| `event.factory:<run_id>` | one pinned Factory run |
| `event.file_edit` | file edit evidence |

### 6.2 Legacy Stream Route Mapping

| Legacy route | Required replacement |
|--------------|----------------------|
| `/v2/pm/chat/stream` | `/v2/ws/runtime` role/dialogue channels |
| `/v2/role/{role}/chat/stream` | `/v2/ws/runtime` role/dialogue channels |
| `/v2/roles/sessions/{id}/messages/stream` | `/v2/ws/runtime` dialogue channel |
| `/v2/factory/runs/{run_id}/stream` | `/v2/ws/runtime` with `event.factory:<run_id>` |
| `/v2/stream/chat` | `/v2/ws/runtime` `llm` / `runtime_events` channels |

### 6.3 WebSocket Subscription Format

```json
{
  "type": "SUBSCRIBE",
  "protocol": "runtime.v2",
  "channels": ["runtime_events", "llm", "process", "dialogue", "event.factory"],
  "roles": ["pm", "chief_engineer", "director", "qa"],
  "tail": 200
}
```

### 6.4 Security Hardening

- WebSocket subscriptions resolve logical channels to workspace-scoped
  JetStream subjects.
- The server rejects missing or non-`runtime.v2` protocol messages.
- Product code must not add HTTP long-polling, timer fetch loops, file
  polling, or polling fallback.
- Browser runtime code must use the shared `runtimeSocketManager`; feature-local
  realtime transports are not allowed.

---

## 7. Code Examples

### 7.1 Python: Role Chat (Non-Streaming)

```python
import requests

BASE = "http://localhost:49977"
TOKEN = "<your-token>"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# v1 (old)
response = requests.post(
    f"{BASE}/v2/pm/chat",  # legacy path still mounted
    headers=HEADERS,
    json={"message": "Plan a login feature"}
)
print(response.json())

# v2 (new) — unified role entry
response = requests.post(
    f"{BASE}/v2/role/pm/chat",
    headers=HEADERS,
    json={"message": "Plan a login feature"}
)
result = response.json()
print(result["ok"], result.get("response"))
```

### 7.2 Python: Role Chat Realtime

```python
import json
import websocket

BASE = "http://localhost:49977"
WS_BASE = "ws://localhost:49977"
TOKEN = "<your-token>"

ws = websocket.create_connection(f"{WS_BASE}/v2/ws/runtime?token={TOKEN}")
ws.send(json.dumps({
    "type": "SUBSCRIBE",
    "protocol": "runtime.v2",
    "channels": ["runtime_events", "llm", "dialogue"],
    "roles": ["architect", "pm", "director", "qa"],
    "tail": 200,
}))

while True:
    message = json.loads(ws.recv())
    print(message)
```

### 7.3 JavaScript: Role Chat (Non-Streaming)

```javascript
const BASE = 'http://localhost:49977';
const TOKEN = '<your-token>';

// v2 unified role chat
async function chatWithRole(role, message) {
  const res = await fetch(`${BASE}/v2/role/${role}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${TOKEN}`
    },
    body: JSON.stringify({ message })
  });

  if (!res.ok) {
    const { error } = await res.json();
    throw new Error(`${error.code}: ${error.message}`);
  }

  const data = await res.json();
  return data.response;
}

chatWithRole('pm', 'Plan a login feature')
  .then(console.log)
  .catch(console.error);
```

### 7.4 JavaScript: Role Chat Realtime

```javascript
const WS_BASE = 'ws://localhost:49977';
const TOKEN = '<your-token>';

function connectRuntime() {
  const ws = new WebSocket(`${WS_BASE}/v2/ws/runtime?token=${TOKEN}`);
  ws.addEventListener('open', () => {
    ws.send(JSON.stringify({
      type: 'SUBSCRIBE',
      protocol: 'runtime.v2',
      channels: ['runtime_events', 'llm', 'dialogue'],
      roles: ['pm', 'chief_engineer', 'director', 'qa'],
      tail: 200
    }));
  });
  ws.addEventListener('message', (event) => {
    console.log(JSON.parse(event.data));
  });
  return ws;
}

connectRuntime();
```

### 7.5 Python: Error Handling Adapter

```python
import requests

class PolarisV2Client:
    def __init__(self, base_url: str, token: str) -> None:
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base}{path}"
        response = requests.request(method, url, headers=self.headers, **kwargs)

        if not response.ok:
            body = response.json()
            error = body.get("error", {})
            raise PolarisV2Error(
                status_code=response.status_code,
                code=error.get("code", "UNKNOWN"),
                message=error.get("message", "Unknown error"),
                details=error.get("details", {}),
            )
        return response.json()

    def chat(self, role: str, message: str) -> dict:
        return self._request(
            "POST",
            f"/v2/role/{role}/chat",
            json={"message": message},
        )

    def pm_status(self) -> dict:
        return self._request("GET", "/v2/pm/status")


class PolarisV2Error(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict) -> None:
        self.status_code = status_code
        self.code = code
        self.details = details
        super().__init__(f"[{code}] {message}")
```

---

## 8. Deprecation Timeline

| Milestone | Date | Action |
|-----------|------|--------|
| v2 GA | 2026-04-24 | All v2 routes stable and tested. |
| v1 Soft Deprecation | 2026-05-06 | Legacy routes still work but are marked `# DEPRECATED`. |
| v1 Hard Deprecation | 2026-06-30 | Legacy routes return `410 Gone` or are removed. |
| v1 Removal | 2026-09-30 | All legacy routes and shims removed. |

### Current Deprecated Routes

These routes still function but will be removed:

- `POST /pm/start_loop` — use `/v2/pm/start`
- `GET /factory/runs` — use `/v2/factory/runs`
- `POST /factory/runs` — use `/v2/factory/runs`
- `GET /factory/runs/{run_id}` — use `/v2/factory/runs/{run_id}`
- `GET /factory/runs/{run_id}/events` — use `/v2/factory/runs/{run_id}/events`
- `GET /factory/runs/{run_id}/audit-bundle` — use `/v2/factory/runs/{run_id}/audit-bundle`
- `GET /factory/runs/{run_id}/stream` — removed for realtime; use `/v2/ws/runtime` with `event.factory:<run_id>`
- `POST /factory/runs/{run_id}/control` — use `/v2/factory/runs/{run_id}/control`
- `GET /factory/runs/{run_id}/artifacts` — use `/v2/factory/runs/{run_id}/artifacts`
- `GET /health` — use `/v2/observability/health`
- `GET /ready` — use `/v2/observability/health/backend`
- `GET /live` — use `/v2/observability/health`

---

## 9. FAQ

### Q1: Do I need to change my token?

No. The same bearer token works for both v1 and v2. However, v2 **always** requires the token; there are no anonymous v2 endpoints.

### Q2: What happens if I send the wrong error parser?

If your client still expects `{"detail": "..."}` on a v2 endpoint, it will fail to parse the response. Update to expect `{"error": {"code": "...", "message": "...", "details": {}}}`.

### Q3: Can I still use the old `/v2/pm/chat` path?

Yes, the legacy path is still mounted as a shim, but it is deprecated. Migrate to `/v2/role/pm/chat` for consistency.

### Q4: How do I know which roles are available?

```bash
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:49977/v2/role/chat/roles
```

### Q5: The old HTTP stream route returns an error. What should I check?

1. Confirm the client has migrated to `/v2/ws/runtime`.
2. Send `protocol: "runtime.v2"` in subscription messages.
3. Subscribe to the appropriate logical channel, for example `event.factory:<run_id>`.
4. Verify the bearer token is included in the WebSocket URL or authorization handshake.

### Q6: How do I migrate my factory run client?

Change the base path from `/factory/runs` to `/v2/factory/runs`. The request/response shapes are backward-compatible but enriched with additional fields (`phase`, `roles`, `gates`).

### Q7: What is the unified orchestration API?

`/v2/orchestration/runs` is a new cross-role entry point that can orchestrate PM, Director, Architect, Chief Engineer, and QA in a single run. It replaces separate PM/Director run calls for complex workflows.

### Q8: My v1 client used `X-User-Role` headers. What should I do?

Stop sending `X-User-Role`. The server ignores client-supplied role headers. Roles are resolved from the server-bound auth context. If you need elevated access, configure the server-side auth metadata.

### Q9: Where can I find the full OpenAPI schema?

Start the backend and visit `http://localhost:49977/docs` (Swagger UI) or `http://localhost:49977/openapi.json`.

### Q10: Are there SDK helpers for v2?

Yes. Frontend SDK hooks are available:

- `useV2Api.ts` — generic v2 API hooks
- `useV2ApiError.ts` — structured error parsing
- `runtimeSocketManager.ts` — unified runtime WebSocket manager

---

## Quick Reference Card

```
Base URL:     http://localhost:49977
Auth:         Authorization: Bearer <token>
Error shape:  {error: {code, message, details}}
WS channels:  runtime_events, llm, process, dialogue,
              event.factory, event.factory:<run_id>, event.file_edit
Roles:        viewer < developer < admin
```
