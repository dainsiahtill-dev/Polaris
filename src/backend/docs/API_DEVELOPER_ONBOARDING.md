# Polaris API v2 Developer Onboarding

> Practical guide for adding, testing, and maintaining endpoints in the `polaris/delivery/http/v2/` layer.

---

## 1. Quick Start

### 1.1 Run the backend

```bash
# From repo root
python -m polaris.delivery.server --host 127.0.0.1 --port 49977

# Legacy shim (still works)
python src/backend/server.py --host 127.0.0.1 --port 49977
```

### 1.2 Open Swagger UI

Visit: `http://127.0.0.1:49977/docs`

All v2 endpoints are mounted under `/v2/*` and tagged by subsystem (PM, Director, Role Chat, etc.).

### 1.3 Project layout (relevant paths)

```
polaris/delivery/http/
  v2/                     # Canonical v2 routers
    __init__.py           # Main v2 router assembly
    pm.py
    director.py
    ...
  routers/                # Legacy + shared utilities
    _shared.py            # StructuredHTTPException, require_auth, require_role
    stream_router.py     # legacy stream routes fail closed; use /v2/ws/runtime
    conversations.py      # CRUD example (v2 + deprecated v1)
  schemas/
    common.py             # Shared Pydantic response models
  dependencies.py         # Auth dependencies
  middleware/rbac.py      # Role-based access control
  error_handlers.py       # Global exception handlers
```

---

## 2. Adding a New v2 Endpoint

### 2.1 Create or open a router file

Use `polaris/delivery/http/v2/` for new canonical endpoints.

```python
# polaris/delivery/http/v2/my_feature.py
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    get_state,
    require_auth,
)
from polaris.delivery.http.schemas.common import HealthResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/my-feature", tags=["My Feature"])


class MyFeatureRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Feature name")
    config: dict[str, Any] | None = Field(None, description="Optional config")


class MyFeatureResponse(BaseModel):
    ok: bool
    feature_id: str
    name: str


@router.post("/run", response_model=MyFeatureResponse, dependencies=[Depends(require_auth)])
async def run_my_feature(
    request: Request,
    payload: MyFeatureRequest,
) -> MyFeatureResponse:
    """Run my feature and return results."""
    _state = get_state(request)

    if not payload.name:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_NAME",
            message="name must not be empty",
        )

    # ... business logic ...
    feature_id = "feat_123"

    return MyFeatureResponse(ok=True, feature_id=feature_id, name=payload.name)


@router.get("/health", response_model=HealthResponse)
async def my_feature_health() -> HealthResponse:
    """Health check for my feature."""
    return HealthResponse(status="ok", message="My feature is healthy")
```

### 2.2 Register the router

```python
# polaris/delivery/http/v2/__init__.py
from fastapi import APIRouter

from .my_feature import router as my_feature_router

v2_router = APIRouter(prefix="/v2")

# ... existing includes ...
v2_router.include_router(my_feature_router)
```

### 2.3 Verify

1. Restart the server.
2. Check Swagger UI for `POST /v2/my-feature/run`.
3. Run the test suite: `pytest polaris/tests/unit/delivery/http/routers/test_my_feature.py -v`

---

## 3. Testing Guidelines

### 3.1 Minimal unit test for a v2 endpoint

```python
# polaris/tests/unit/delivery/http/routers/test_my_feature.py
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from polaris.delivery.http.v2.my_feature import router as my_feature_router


@pytest.fixture
async def client() -> AsyncClient:
    app = FastAPI()
    app.include_router(my_feature_router)

    # Mock auth dependency so tests don't need real tokens
    async def _mock_auth() -> None:
        pass

    for route in app.routes:
        if hasattr(route, "dependencies"):
            route.dependencies = []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_run_my_feature_success(client: AsyncClient) -> None:
    payload = {"name": "test-feature", "config": {"key": "value"}}
    response = await client.post("/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["name"] == "test-feature"
    assert "feature_id" in data


@pytest.mark.anyio
async def test_run_my_feature_validation_error(client: AsyncClient) -> None:
    # Missing required field 'name'
    payload: dict[str, Any] = {"config": {}}
    response = await client.post("/run", json=payload)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_my_feature_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
```

### 3.2 Testing with mocked services

```python
@pytest.mark.anyio
async def test_run_my_feature_with_mocked_service(client: AsyncClient) -> None:
    with patch("polaris.delivery.http.v2.my_feature.some_service") as mock_svc:
        mock_svc.process = AsyncMock(return_value={"result": "success"})

        payload = {"name": "mocked-feature"}
        response = await client.post("/run", json=payload)
        assert response.status_code == 200
        mock_svc.process.assert_awaited_once()
```

### 3.3 Run tests

```bash
# Single test file
pytest polaris/tests/unit/delivery/http/routers/test_my_feature.py -v

# All delivery router tests
pytest polaris/tests/unit/delivery/http/routers/ -v

# With coverage
pytest polaris/tests/unit/delivery/http/routers/test_my_feature.py --cov=polaris.delivery.http.v2.my_feature -v
```

---

## 4. Error Handling

### 4.1 Always use `StructuredHTTPException`

Never raise bare `HTTPException`. The unified format is:

```json
{
  "error": {
    "code": "CONVERSATION_NOT_FOUND",
    "message": "Conversation not found",
    "details": {}
  }
}
```

### 4.2 Raising errors in endpoints

```python
from polaris.delivery.http.routers._shared import StructuredHTTPException

# 404 - Not Found
if not conversation:
    raise StructuredHTTPException(
        status_code=404,
        code="CONVERSATION_NOT_FOUND",
        message="Conversation not found",
    )

# 400 - Bad Request
if not message:
    raise StructuredHTTPException(
        status_code=400,
        code="MISSING_MESSAGE",
        message="message is required",
    )

# 409 - Conflict / Not Ready
raise StructuredHTTPException(
    status_code=409,
    code="PM_ROLE_NOT_CONFIGURED",
    message="PM role not configured",
    details={"roles_keys": list(roles.keys()) if roles else None},
)

# 422 - Unprocessable Entity (validation)
raise StructuredHTTPException(
    status_code=422,
    code="INVALID_REQUEST",
    message="Invalid request parameters",
    details={"field": "name", "reason": "must be non-empty"},
)

# 500 - Internal Error
try:
    result = await some_operation()
except (RuntimeError, ValueError) as exc:
    raise StructuredHTTPException(
        status_code=500,
        code="INTERNAL_ERROR",
        message=str(exc),
    ) from exc
```

### 4.3 Domain exception mapping (global handlers)

The `error_handlers.py` module maps domain exceptions to HTTP responses automatically. If you add a new domain exception, register it there:

```python
# polaris/delivery/http/error_handlers.py
from polaris.domain.my_feature.errors import MyFeatureError

async def my_feature_error_handler(request: Request, exc: MyFeatureError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "MY_FEATURE_ERROR",
                "message": str(exc),
                "details": getattr(exc, "details", {}),
            }
        },
    )

# Register in setup_exception_handlers(app)
app.add_exception_handler(MyFeatureError, my_feature_error_handler)
```

---

## 5. Response Models

### 5.1 Add models to `schemas/common.py`

```python
# polaris/delivery/http/schemas/common.py
from pydantic import BaseModel, Field


class MyFeatureResponse(BaseModel):
    """Response model for my feature run endpoint."""

    model_config = {"extra": "allow"}  # Forward compatibility

    ok: bool = Field(..., description="Success indicator")
    feature_id: str = Field(..., description="Unique feature identifier")
    name: str = Field(..., description="Feature name")
    result: dict[str, Any] | None = Field(None, description="Optional result data")


class MyFeatureListResponse(BaseModel):
    """Response model for listing features."""

    features: list[MyFeatureResponse] = Field(default_factory=list)
    total: int = Field(0, description="Total count")
```

### 5.2 Use `model_config = {"extra": "allow"}`

Always set this on response models so the API remains forward-compatible when new fields are added.

### 5.3 Reference models in router decorators

```python
@router.post(
    "/run",
    response_model=MyFeatureResponse,
    dependencies=[Depends(require_auth)],
)
async def run_my_feature(...) -> MyFeatureResponse:
    ...
```

---

## 6. Realtime Endpoints

### 6.1 Canonical transport

Polaris product realtime delivery is single-rail:

| Pattern | Use case | Allowed |
|---------|----------|---------|
| `response_model=...` | Standard request/response, initial snapshot, explicit user refresh | Yes |
| Nat-JetStream + `/v2/ws/runtime` runtime.v2 | Realtime event delivery | Yes |
| SSE / `EventSource` / `text/event-stream` / `StreamingResponse` | Product realtime streaming | No |
| HTTP polling / long-polling / timer fetch loops | Product realtime updates | No |

HTTP endpoints may create durable state, return snapshots, or accept commands.
Realtime updates must be published as structured JetStream events and consumed
through the shared runtime WebSocket.

### 6.2 Publish a realtime event

```python
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.routers.jetstream_utils import publish_to_jetstream
from polaris.infrastructure.messaging.nats.nats_types import create_runtime_event

router = APIRouter(prefix="/my-feature", tags=["My Feature"])


class MyFeatureRunRequest(BaseModel):
    message: str = Field(..., min_length=1)


@router.post("/runs", dependencies=[Depends(require_auth)])
async def start_my_feature_run(payload: MyFeatureRunRequest) -> dict[str, Any]:
    run_id = "feature-123"
    event = create_runtime_event(
        workspace_key="default",
        run_id=run_id,
        channel=f"event.my_feature:{run_id}",
        kind="my_feature.started",
        payload={
            "type": "my_feature.started",
            "run_id": run_id,
            "summary": payload.message,
        },
        meta={"source": "my_feature"},
    )
    await publish_to_jetstream(
        subject=f"hp.runtime.default.event.my_feature.{run_id}",
        payload=event.to_dict(),
    )
    return {"run_id": run_id, "status": "running"}
```

When adding a new channel family, update the runtime.v2 subject builder so the
logical channel maps to the canonical JetStream subject. Keep payloads structured;
do not make clients infer state from prose-only messages.

### 6.3 Client-side consumption

Frontend code must subscribe through `RuntimeTransportProvider` /
`runtimeSocketManager`, not `EventSource` or a feature-local WebSocket client.

```typescript
import { useEffect } from 'react';
import { useRuntimeTransport } from '@/runtime/transport';

export function useMyFeatureRun(runId: string | null): void {
  const { subscribeChannels, registerMessageHandler } = useRuntimeTransport();

  useEffect(() => {
    if (!runId) return undefined;
    const unsubscribe = subscribeChannels([
      { channel: `event.my_feature:${runId}`, tailLines: 0 },
    ]);
    const unregister = registerMessageHandler((message) => {
      const envelope = typeof message === 'object' && message !== null
        ? (message as Record<string, unknown>).event
        : null;
      if (!envelope || typeof envelope !== 'object') return;
      // Update React state from the structured payload here.
    });
    return () => {
      unregister();
      unsubscribe();
    };
  }, [registerMessageHandler, runId, subscribeChannels]);
}
```

### 6.4 Forbidden realtime patterns

Do not introduce product realtime code that:

- returns `StreamingResponse` event streams or any `text/event-stream` response;
- creates browser `EventSource` clients or feature-local WebSocket clients;
- schedules HTTP status refreshes with timers;
- uses HTTP long polling, file polling, polling fallback, or hidden retry loops
  as a realtime substitute.

Allowed exceptions are test harness waits, UI clocks/animations, WebSocket
heartbeat/reconnect timers, initial snapshot hydration, explicit user refresh,
and one-shot command/query responses.

---

## 7. Auth and RBAC

### 7.1 Protect endpoints with `require_auth`

```python
from polaris.delivery.http.routers._shared import require_auth
from fastapi import Depends

@router.get("/public-data")  # No auth required
async def public_data() -> dict[str, str]:
    return {"message": "Hello, world!"}

@router.get("/private-data", dependencies=[Depends(require_auth)])
async def private_data() -> dict[str, str]:
    return {"message": "Secret data"}
```

### 7.2 Role-based access control

```python
from polaris.delivery.http.routers._shared import require_role
from polaris.delivery.http.auth.roles import UserRole

# Admin and Developer only
@router.post(
    "/admin-action",
    dependencies=[
        Depends(require_auth),
        Depends(require_role([UserRole.ADMIN, UserRole.DEVELOPER])),
    ],
)
async def admin_action() -> dict[str, str]:
    return {"message": "Admin action executed"}

# Admin only
@router.delete(
    "/cache-clear",
    dependencies=[
        Depends(require_auth),
        Depends(require_role([UserRole.ADMIN])),
    ],
)
async def clear_cache() -> dict[str, str]:
    return {"message": "Cache cleared"}
```

### 7.3 Role hierarchy

```python
# polaris/delivery/http/auth/roles.py
class UserRole(str, Enum):
    VIEWER = "viewer"        # Read-only access (level 1)
    DEVELOPER = "developer"  # Read + write + execute (level 2)
    ADMIN = "admin"          # Full access including destructive operations (level 3)
```

When checking permissions, prefer `require_role([UserRole.ADMIN, UserRole.DEVELOPER])` over single-role checks when both should have access.

### 7.4 Accessing auth context in endpoints

```python
from polaris.delivery.http.routers._shared import get_state

@router.get("/whoami", dependencies=[Depends(require_auth)])
async def whoami(request: Request) -> dict[str, Any]:
    state = get_state(request)
    auth_context = getattr(request.state, "auth_context", None)

    return {
        "workspace": str(state.settings.workspace),
        "auth_context": {
            "role": auth_context.role if auth_context else None,
            "user_id": auth_context.user_id if auth_context else None,
        },
    }
```

---

## 8. Common Pitfalls

### 8.1 Do NOT use bare `HTTPException`

```python
# BAD - breaks unified error format
from fastapi import HTTPException
raise HTTPException(status_code=404, detail="Not found")

# GOOD - consistent ADR-003 error format
from polaris.delivery.http.routers._shared import StructuredHTTPException
raise StructuredHTTPException(
    status_code=404,
    code="NOT_FOUND",
    message="Resource not found",
)
```

### 8.2 Do NOT return raw dicts without `response_model`

```python
# BAD - no OpenAPI schema, no validation
@router.get("/data")
async def get_data() -> dict[str, Any]:
    return {"ok": True, "data": []}

# GOOD - documented, validated, typed
@router.get("/data", response_model=MyDataResponse)
async def get_data() -> MyDataResponse:
    return MyDataResponse(ok=True, data=[])
```

### 8.3 Do NOT forget `dependencies=[Depends(require_auth)]`

```python
# BAD - endpoint is publicly accessible
@router.post("/sensitive-action")
async def sensitive_action() -> dict[str, str]:
    ...

# GOOD - protected with auth
@router.post("/sensitive-action", dependencies=[Depends(require_auth)])
async def sensitive_action() -> dict[str, str]:
    ...
```

### 8.4 Do NOT block the event loop with sync I/O

```python
# BAD - blocks all requests
@router.get("/config")
async def get_config() -> dict[str, Any]:
    with open("config.json") as f:  # Blocks!
        return json.load(f)

# GOOD - use asyncio.to_thread for file I/O
import asyncio

@router.get("/config")
async def get_config() -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _load_config)

# BETTER - use asyncio.to_thread (Python 3.9+)
@router.get("/config")
async def get_config() -> dict[str, Any]:
    return await asyncio.to_thread(_load_config)
```

### 8.5 Do NOT forget to register new routers

```python
# BAD - endpoint exists but returns 404
# my_feature.py is not included in v2/__init__.py

# GOOD - always include in v2/__init__.py
from .my_feature import router as my_feature_router
v2_router.include_router(my_feature_router)
```

### 8.6 Do NOT use bare `except:`

```python
# BAD - catches KeyboardInterrupt, SystemExit, etc.
try:
    result = await operation()
except:  # noqa: S110
    return {"ok": False}

# GOOD - catch specific exceptions
try:
    result = await operation()
except (RuntimeError, ValueError) as exc:
    raise StructuredHTTPException(
        status_code=500,
        code="OPERATION_FAILED",
        message=str(exc),
    ) from exc
```

### 8.7 Do NOT forget UTF-8 encoding

```python
# BAD - relies on system default encoding
with open("data.txt") as f:
    content = f.read()

# GOOD - explicit UTF-8
with open("data.txt", encoding="utf-8") as f:
    content = f.read()
```

### 8.8 Do NOT duplicate v1 endpoints without deprecation

When adding a v2 endpoint that replaces v1, keep the v1 endpoint as a shim:

```python
@router.post("/v2/my-feature/run", response_model=MyFeatureResponse)
async def run_my_feature_v2(...) -> MyFeatureResponse:
    """Canonical v2 endpoint."""
    ...

@router.post("/my-feature/run", response_model=MyFeatureResponse)  # DEPRECATED
async def run_my_feature(...) -> MyFeatureResponse:
    """Deprecated - use /v2/my-feature/run instead."""
    return await run_my_feature_v2(...)
```

---

## Appendix: Code Quality Checklist

Before submitting a PR with new endpoints:

```bash
# 1. Formatting
ruff check polaris/delivery/http/v2/my_feature.py --fix
ruff format polaris/delivery/http/v2/my_feature.py

# 2. Type checking
mypy polaris/delivery/http/v2/my_feature.py

# 3. Tests
pytest polaris/tests/unit/delivery/http/routers/test_my_feature.py -v

# 4. Full delivery suite (if touching shared code)
pytest polaris/tests/unit/delivery/http/ -v
```

---

## Appendix: Quick Reference Card

| Task | Pattern |
|------|---------|
| New router file | `polaris/delivery/http/v2/{feature}.py` |
| Register router | `v2_router.include_router({feature}_router)` in `v2/__init__.py` |
| Auth required | `dependencies=[Depends(require_auth)]` |
| Role restriction | `dependencies=[Depends(require_role([UserRole.ADMIN]))]` |
| Error response | `raise StructuredHTTPException(status_code=..., code="...", message="...")` |
| Response model | Define in `schemas/common.py`, use `response_model=...` |
| Realtime push | Publish structured JetStream event, subscribe via `/v2/ws/runtime` runtime.v2 |
| Forward compat | `model_config = {"extra": "allow"}` on response models |
| File I/O in async | `await asyncio.to_thread(sync_fn, ...)` |
| UTF-8 files | `open(path, encoding="utf-8")` |
