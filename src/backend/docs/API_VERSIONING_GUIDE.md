# Polaris API Versioning Guide

> How to work with Polaris v2 APIs: structure, deprecation, error handling, SSE, and RBAC.
> Last updated: 2026-05-06

---

## Table of Contents

1. [How v2 Routes Are Structured](#1-how-v2-routes-are-structured)
2. [How to Add New v2 Routes](#2-how-to-add-new-v2-routes)
3. [How to Deprecate Old Routes](#3-how-to-deprecate-old-routes)
4. [Error Response Format Specification](#4-error-response-format-specification)
5. [SSE Event Schema Specification](#5-sse-event-schema-specification)
6. [RBAC Usage Examples](#6-rbac-usage-examples)

---

## 1. How v2 Routes Are Structured

### 1.1 Router Hierarchy

All v2 routes are mounted under `/v2` via `polaris/delivery/http/v2/__init__.py`:

```python
from fastapi import APIRouter
from .pm import router as pm_router
from .director import router as director_router
# ... etc

router = APIRouter(prefix="/v2")
router.include_router(pm_router)        # -> /v2/pm/*
router.include_router(director_router)  # -> /v2/director/*
# ... etc
```

Each sub-router defines its own `prefix` and `tags`:

```python
# polaris/delivery/http/v2/pm.py
router = APIRouter(prefix="/pm", tags=["PM"])
```

### 1.2 Route Pattern

A canonical v2 route has:

1. **Explicit Pydantic request/response models**
2. **`dependencies=[Depends(require_auth)]`** on every route
3. **`response_model=...`** for typed responses
4. **Structured error handling** via `StructuredHTTPException`

Example:

```python
from fastapi import APIRouter, Depends
from polaris.delivery.http.dependencies import require_auth
from polaris.delivery.http.routers._shared import StructuredHTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/example", tags=["Example"])


class CreateThingRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    metadata: dict[str, str] = Field(default_factory=dict)


class ThingResponse(BaseModel):
    id: str
    name: str
    status: str


@router.post(
    "/things",
    response_model=ThingResponse,
    dependencies=[Depends(require_auth)],
)
async def create_thing(request: CreateThingRequest) -> ThingResponse:
    try:
        result = await some_service.create(request.name, request.metadata)
        return ThingResponse(id=result.id, name=result.name, status="created")
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_REQUEST",
            message=str(exc),
        ) from exc
```

### 1.3 File Layout

```
polaris/delivery/http/v2/
__init__.py          # Main v2 router assembly
pm.py                # PM endpoints
director.py          # Director endpoints
orchestration.py     # Unified orchestration
resident.py          # Resident engineer
services.py          # Background tasks, todos, tokens, security, transcript
audit.py             # Audit compatibility facade
observability.py     # Metrics, health, WebSocket events
```

---

## 2. How to Add New v2 Routes

### 2.1 Step-by-Step

1. **Identify the target Cell**: Use the public service from the appropriate Cell (e.g., `polaris.cells.orchestration.workflow_runtime.public.service`).

2. **Define request/response models** using Pydantic `BaseModel` with `Field(...)` descriptors.

3. **Create the route** in the appropriate v2 router file (or create a new one and include it in `__init__.py`).

4. **Add `dependencies=[Depends(require_auth)]`** to every route.

5. **Use `StructuredHTTPException`** for all error responses.

6. **Add `response_model=...`** for typed responses.

7. **Write tests** in `polaris/tests/unit/delivery/http/routers/test_<name>_v2.py`.

8. **Run validation**:
   ```bash
   ruff check polaris/delivery/http/v2/your_router.py --fix
   ruff format polaris/delivery/http/v2/your_router.py
   mypy polaris/delivery/http/v2/your_router.py
   pytest polaris/tests/unit/delivery/http/routers/test_your_v2.py -v
   ```

### 2.2 Example: Adding a New Service Endpoint

```python
# polaris/delivery/http/v2/services.py

class HealthCheckResponse(BaseModel):
    healthy: bool
    version: str


@router.get("/health", response_model=HealthCheckResponse, dependencies=[Depends(require_auth)])
async def get_health() -> HealthCheckResponse:
    return HealthCheckResponse(healthy=True, version="2.0.0")
```

### 2.3 Registering a New Router

```python
# polaris/delivery/http/v2/__init__.py
from .your_module import router as your_router

router = APIRouter(prefix="/v2")
# ... existing includes ...
router.include_router(your_router)
```

---

## 3. How to Deprecate Old Routes

### 3.1 Deprecation Pattern

When replacing a v1 route with a v2 equivalent:

1. **Keep the old endpoint** but add deprecation metadata.
2. **Emit a `DeprecationWarning`** via Python's `warnings` module.
3. **Forward to the new implementation** or duplicate the logic.
4. **Document the replacement** in the docstring.

Example from `polaris/delivery/http/v2/pm.py`:

```python
import warnings

@router.post(
    "/start_loop",
    dependencies=[Depends(require_auth)],
)
async def pm_start_loop(
    resume: bool = False,
    pm_service: PMService = Depends(get_pm_service),
) -> dict:
    """Start PM in loop mode (deprecated -- use /v2/pm/start).

    DEPRECATED: This endpoint is deprecated. Use /v2/pm/start instead.
    Will be removed in v2.0.
    """
    warnings.warn(
        "/pm/start_loop is deprecated. Use /v2/pm/start instead. Will be removed in v2.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    return await pm_service.start_loop(resume=resume)
```

### 3.2 Shim Layer for Migrated Modules

For modules that have been moved to new locations, create a compatibility shim:

```python
# scripts/contextos_gate_checker.py
"""DEPRECATED: Use polaris.delivery.cli.tools.contextos_gate_checker instead."""
from polaris.delivery.cli.tools.contextos_gate_checker import main

if __name__ == "__main__":
    import warnings
    warnings.warn(
        "This script is deprecated. Use polaris.delivery.cli.tools.contextos_gate_checker instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    main()
```

### 3.3 Marking Code with `# DEPRECATED`

Use the `# DEPRECATED` comment marker for fitness rule checkers to detect:

```python
fallback_to_full_file: bool = True  # DEPRECATED: no longer used, kept for compat
```

---

## 4. Error Response Format Specification

### 4.1 Unified Error Structure (ADR-003)

All v2 API errors follow this JSON structure:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description",
    "details": {
      "key": "value"
    }
  }
}
```

### 4.2 Raising Structured Errors

Use `StructuredHTTPException` from `polaris/delivery/http/routers/_shared.py`:

```python
from polaris.delivery.http.routers._shared import StructuredHTTPException

raise StructuredHTTPException(
    status_code=404,
    code="RUN_NOT_FOUND",
    message=f"Run {run_id} not found",
    details={"run_id": run_id},
)
```

### 4.3 Error Handler Registration

Ensure `setup_exception_handlers(app)` is called in your app factory. This registers handlers for:

| Exception Type | HTTP Status | Response Shape |
|----------------|-------------|----------------|
| `DomainException` | From `exc.status_code` | `{error: {code, message, details}}` |
| `RequestValidationError` | 422 | `{error: {code: "VALIDATION_ERROR", message, details: {errors}}}` |
| `StructuredHTTPException` | From `exc.status_code` | `{error: {code, message, details}}` |
| Generic `Exception` | 500 | `{error: {code: "INTERNAL_ERROR", message, details: {type}}}` |

### 4.4 Common Error Codes Reference

```python
# 400 Bad Request
INVALID_REQUEST          # Missing or malformed input
UNSUPPORTED_ROLE         # Role not in registered list
INVALID_DOCS_PATH        # Docs path outside workspace

# 401 Unauthorized
# (Raised by require_auth -- no structured body, just HTTPException)

# 403 Forbidden
# (Raised by require_role -- detail contains "role 'x' not authorized")

# 404 Not Found
RUN_NOT_FOUND            # Factory/orchestration run missing
REPORT_NOT_FOUND         # LLM test report missing
ROLE_NOT_FOUND           # Court actor not found
SESSION_NOT_FOUND        # Agent session missing

# 409 Conflict
RUNTIME_ROLES_NOT_READY  # Required LLM roles not configured
PM_ROLE_NOT_CONFIGURED   # PM LLM not ready
ARCHITECT_NOT_CONFIGURED # Architect LLM not ready

# 422 Validation Error
VALIDATION_ERROR         # Pydantic validation failure

# 500 Internal Server Error
GENERATION_FAILED        # LLM generation error
INTERNAL_ERROR           # Unhandled exception
```

---

## 5. SSE Event Schema Specification

### 5.1 SSE Frame Format

All SSE endpoints emit frames in this format:

```
event: <type>
data: <json-payload>

```

### 5.2 Canonical Event Types

| Type | Payload Schema | Description |
|------|---------------|-------------|
| `thinking_chunk` | `{"content": "..."}` | Reasoning/thinking token |
| `content_chunk` | `{"content": "..."}` | Response content token |
| `tool_call` | `{"tool": "name", "args": {...}}` | Tool invocation |
| `tool_result` | `{...}` | Tool execution result |
| `fingerprint` | `{"fingerprint": "..."}` | Response fingerprint |
| `complete` | `{"content": "...", "thinking": "...", "tool_calls": [...]}` | Stream complete |
| `error` | `{"error": "..."}` | Terminal error |
| `ping` | `{}` | Keep-alive |

### 5.3 Creating an SSE Endpoint

```python
from polaris.delivery.http.routers.sse_utils import create_sse_response, sse_event_generator
import asyncio

@router.post("/stream")
async def stream_example() -> StreamingResponse:
    async def _producer(queue: asyncio.Queue) -> None:
        for i in range(3):
            await queue.put({"type": "content_chunk", "data": {"content": f"chunk {i}"}})
            await asyncio.sleep(0.1)
        await queue.put({"type": "complete", "data": {"content": "done"}})

    return create_sse_response(sse_event_generator(_producer, timeout=30.0))
```

### 5.4 JetStream SSE Consumer

For remote event streaming via JetStream:

```python
from polaris.delivery.http.routers.sse_utils import (
    create_sse_jetstream_consumer,
    create_sse_response_from_jetstream,
)

@router.get("/remote-stream")
async def remote_stream(last_event_id: int = 0) -> StreamingResponse:
    consumer = create_sse_jetstream_consumer(
        workspace_key="my-workspace",
        subject="hp.runtime.my-workspace.events",
        last_event_id=last_event_id,
    )
    return create_sse_response_from_jetstream(consumer)
```

### 5.5 Security Considerations

- Payloads are limited to **256KB**
- Event timestamps older than **1 hour** are rejected (replay protection)
- Consumer names use **cryptographically random** suffixes
- Subjects are validated against `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}$`

---

## 6. RBAC Usage Examples

### 6.1 Role Hierarchy

```python
from polaris.delivery.http.auth.roles import UserRole

# Levels (higher = more privileged)
UserRole.VIEWER.level    # 1
UserRole.DEVELOPER.level # 2
UserRole.ADMIN.level     # 3
```

### 6.2 Basic Auth on All Routes

```python
from fastapi import Depends
from polaris.delivery.http.dependencies import require_auth

@router.get("/items", dependencies=[Depends(require_auth)])
async def list_items() -> list[dict]:
    return []
```

### 6.3 Role-Restricted Routes

```python
from fastapi import Depends
from polaris.delivery.http.auth.roles import UserRole
from polaris.delivery.http.middleware.rbac import require_role

# Admin only
@router.post(
    "/admin/clear-cache",
    dependencies=[
        Depends(require_auth),
        Depends(require_role([UserRole.ADMIN])),
    ],
)
async def clear_cache() -> dict:
    return {"ok": True}

# Admin or Developer
@router.post(
    "/developer/reload",
    dependencies=[
        Depends(require_auth),
        Depends(require_role([UserRole.ADMIN, UserRole.DEVELOPER])),
    ],
)
async def reload() -> dict:
    return {"ok": True}
```

### 6.4 Checking Role in Route Body

```python
from fastapi import Request
from polaris.delivery.http.middleware.rbac import extract_role_from_request

@router.get("/my-role")
async def my_role(request: Request) -> dict:
    role = extract_role_from_request(request)
    return {"role": role.value, "level": role.level}
```

### 6.5 Permission-Based Access

```python
from polaris.delivery.http.dependencies import require_permission

@router.get(
    "/sensitive-data",
    dependencies=[
        Depends(require_auth),
        Depends(require_permission("sensitive:read")),
    ],
)
async def get_sensitive_data() -> dict:
    return {"data": "secret"}
```

### 6.6 RBAC Middleware Setup

```python
from fastapi import FastAPI
from polaris.delivery.http.middleware.rbac import RBACMiddleware

app = FastAPI()
app.add_middleware(RBACMiddleware)
```

The middleware initializes `request.state.user_role = UserRole.VIEWER` for every incoming request. Client headers like `X-User-Role` are intentionally ignored.

### 6.7 Auth Context in Tests

```python
from polaris.delivery.http.auth.roles import UserRole
from polaris.kernelone.auth_context import SimpleAuthContext

# Simulate authenticated request with admin role
request.state.auth_context = SimpleAuthContext(
    principal="test",
    metadata={"roles": ["admin"]},
)
request.state.user_role = UserRole.ADMIN
```

---

## Quick Reference

| Task | File / Import |
|------|---------------|
| Main v2 router | `polaris.delivery.http.v2.router` |
| Structured errors | `polaris.delivery.http.routers._shared.StructuredHTTPException` |
| Auth dependency | `polaris.delivery.http.dependencies.require_auth` |
| Role dependency | `polaris.delivery.http.middleware.rbac.require_role` |
| RBAC middleware | `polaris.delivery.http.middleware.rbac.RBACMiddleware` |
| SSE utilities | `polaris.delivery.http.routers.sse_utils` |
| Error handlers | `polaris.delivery.http.error_handlers.setup_exception_handlers` |
| User roles | `polaris.delivery.http.auth.roles.UserRole` |
| Auth context | `polaris.kernelone.auth_context.SimpleAuthContext` |
