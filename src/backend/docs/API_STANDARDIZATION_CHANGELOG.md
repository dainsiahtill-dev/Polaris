# Polaris API Standardization Changelog

> Documenting all changes made during the Polaris v2 API standardization effort.
> Last updated: 2026-05-06

---

## 1. Overview

The Polaris API standardization effort consolidated fragmented endpoints from the legacy `app/`, `core/`, and `api/` roots into a unified `/v2/*` namespace under `polaris/delivery/http/v2/`. The goals were:

- **Unified routing**: All new endpoints live under `/v2/` with consistent tagging and OpenAPI documentation.
- **Structured errors**: Replace bare `HTTPException` with `StructuredHTTPException` following the ADR-003 error contract (`{error: {code, message, details}}`).
- **Pydantic response models**: Every v2 route declares explicit `response_model` classes.
- **RBAC by default**: All v2 routes require authentication via `require_auth`; sensitive operations add `require_role`.
- **SSE hardening**: Unified SSE utilities with security validations, replay protection, and exception-preserving cleanup.
- **Cell-aware architecture**: v2 routes delegate to Cell public services rather than inlining business logic.

---

## 2. New v2 Endpoints

### 2.1 Role Chat (`/v2/role/*`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/role/chat/ping` | Health check |
| GET | `/v2/role/{role}/chat/status` | LLM readiness for role |
| GET | `/v2/role/chat/roles` | List registered roles |
| POST | `/v2/role/{role}/chat` | Non-streaming role chat |
| POST | `/v2/role/{role}/chat/stream` | Streaming SSE role chat |
| GET | `/v2/role/{role}/llm-events` | LLM events per role |
| GET | `/v2/role/llm-events` | Global LLM events |
| GET | `/v2/role/cache-stats` | Cache statistics |
| POST | `/v2/role/cache-clear` | Clear cache (admin only) |

### 2.2 PM (`/v2/pm/*`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/pm/run_once` | Run PM once |
| POST | `/v2/pm/start` | Start PM loop |
| POST | `/v2/pm/stop` | Stop PM with graceful shutdown |
| GET | `/v2/pm/status` | PM process status |
| POST | `/v2/pm/run` | Unified orchestration entry |
| GET | `/v2/pm/runs/{run_id}` | Query PM run status |
| GET | `/v2/pm/llm-events` | PM LLM events |
| GET | `/v2/pm/cache-stats` | Cache stats |
| POST | `/v2/pm/cache-clear` | Clear cache |
| GET | `/v2/pm/token-budget-stats` | Token budget stats |

### 2.3 Director (`/v2/director/*`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/director/start` | Start Director |
| POST | `/v2/director/stop` | Stop Director |
| GET | `/v2/director/status` | Director status (local only) |
| POST | `/v2/director/tasks` | Create task |
| GET | `/v2/director/tasks` | List tasks |
| GET | `/v2/director/tasks/{task_id}` | Get task |
| POST | `/v2/director/tasks/{task_id}/cancel` | Cancel task |
| GET | `/v2/director/workers` | List workers |
| GET | `/v2/director/workers/{worker_id}` | Get worker |
| POST | `/v2/director/run` | Unified orchestration entry |
| GET | `/v2/director/runs/{run_id}` | Query Director run status |
| GET | `/v2/director/llm-events` | LLM events |
| GET | `/v2/director/cache-stats` | Cache stats |
| POST | `/v2/director/cache-clear` | Clear cache |
| GET | `/v2/director/token-budget-stats` | Token budget stats |

### 2.4 Unified Orchestration (`/v2/orchestration/*`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/orchestration/runs` | Create orchestration run |
| GET | `/v2/orchestration/runs` | List runs |
| GET | `/v2/orchestration/runs/{run_id}` | Get run status |
| GET | `/v2/orchestration/runs/{run_id}/tasks` | List run tasks |
| POST | `/v2/orchestration/runs/{run_id}/signal` | Send control signal |
| DELETE | `/v2/orchestration/runs/{run_id}` | Cancel run |

### 2.5 Resident (`/v2/resident/*`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/resident/status` | Resident status |
| POST | `/v2/resident/start` | Start resident |
| POST | `/v2/resident/stop` | Stop resident |
| POST | `/v2/resident/tick` | Tick resident |
| GET | `/v2/resident/identity` | Get identity |
| PATCH | `/v2/resident/identity` | Patch identity |
| GET | `/v2/resident/agenda` | Get agenda |
| GET | `/v2/resident/goals` | List goals |
| POST | `/v2/resident/goals` | Create goal |
| POST | `/v2/resident/goals/{goal_id}/approve` | Approve goal |
| POST | `/v2/resident/goals/{goal_id}/reject` | Reject goal |
| POST | `/v2/resident/goals/{goal_id}/materialize` | Materialize goal |
| POST | `/v2/resident/goals/{goal_id}/stage` | Stage goal |
| POST | `/v2/resident/goals/{goal_id}/run` | Run goal |
| GET | `/v2/resident/goals/{goal_id}/execution` | Goal execution view |
| GET | `/v2/resident/goals/execution/bulk` | Bulk execution views |
| GET | `/v2/resident/decisions` | List decisions |
| GET | `/v2/resident/decisions/{decision_id}/evidence` | Decision evidence |
| POST | `/v2/resident/decisions` | Record decision |
| GET | `/v2/resident/skills` | List skills |
| POST | `/v2/resident/skills/extract` | Extract skills |
| GET | `/v2/resident/experiments` | List experiments |
| POST | `/v2/resident/experiments/run` | Run experiments |
| GET | `/v2/resident/improvements` | List improvements |
| POST | `/v2/resident/improvements/run` | Run improvements |

### 2.6 Services (`/v2/services/*`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/services/tasks` | Create background task |
| GET | `/v2/services/tasks/{task_id}` | Get background task |
| GET | `/v2/services/tasks` | List background tasks |
| POST | `/v2/services/todos` | Create todo |
| GET | `/v2/services/todos` | List todos |
| GET | `/v2/services/todos/summary` | Todo summary |
| POST | `/v2/services/todos/{item_id}/done` | Mark todo done |
| GET | `/v2/services/tokens/status` | Token budget status |
| POST | `/v2/services/tokens/record` | Record token usage |
| POST | `/v2/services/security/check` | Security check |
| GET | `/v2/services/transcript` | Get transcript |
| GET | `/v2/services/transcript/session` | Session info |
| POST | `/v2/services/transcript/message` | Record message |

### 2.7 Observability (`/v2/observability/*`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/observability/status` | Observability status |
| GET | `/v2/observability/services` | List tracked services |
| GET | `/v2/observability/services/{service_id}` | Service details |
| GET | `/v2/observability/metrics` | Aggregated metrics |
| GET | `/v2/observability/health` | Health status |
| GET | `/v2/observability/health/backend` | Backend health |
| WS | `/v2/observability/ws/events` | Real-time event WebSocket |
| POST | `/v2/observability/metrics/export` | Export metrics |

### 2.8 Audit (`/v2/audit/*`)

Compatibility facade mounting the migrated `audit_router` under `/v2/audit`.

### 2.9 Agent / Factory (`/v2/agent/*`, `/v2/factory/*`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/agent/sessions` | List sessions |
| GET | `/v2/agent/sessions/{session_id}` | Get session |
| GET | `/v2/agent/sessions/{session_id}/memory/search` | Search memory |
| GET | `/v2/agent/sessions/{session_id}/memory/artifacts/{artifact_id}` | Read artifact |
| GET | `/v2/agent/sessions/{session_id}/memory/episodes/{episode_id}` | Read episode |
| GET | `/v2/agent/sessions/{session_id}/memory/state` | Read state |
| POST | `/v2/agent/sessions/{session_id}/messages` | Send message |
| POST | `/v2/agent/sessions/{session_id}/messages/stream` | Stream message |
| DELETE | `/v2/agent/sessions/{session_id}` | Delete session |
| POST | `/v2/agent/turn` | Execute agent turn |
| POST | `/v2/factory/runs` | Create factory run |
| GET | `/v2/factory/runs/{run_id}` | Get run |
| GET | `/v2/factory/runs/{run_id}/events` | Run events |
| GET | `/v2/factory/runs/{run_id}/artifacts` | Run artifacts |
| GET | `/v2/factory/runs/{run_id}/audit-bundle` | Audit bundle |

---

## 3. Deprecated Routes

| Deprecated Route | Replacement | Location |
|------------------|-------------|----------|
| `/pm/start_loop` | `/v2/pm/start` | `polaris/delivery/http/v2/pm.py` |
| `_merge_director_status` re-export | `merge_director_status` from `polaris.cells.runtime.projection.public.service` | `polaris/delivery/http/v2/director.py` |
| `scripts/contextos_gate_checker.py` | `polaris.delivery.cli.tools.contextos_gate_checker` | `scripts/` |
| `scripts/dev-tools.py` | `polaris.delivery.cli.tools.dev_tools` | `scripts/` |
| `scripts/check_legacy_imports.py` | `polaris.delivery.cli.tools.check_legacy_imports` | `scripts/` |
| `scripts/check_cell_imports.py` | `polaris.delivery.cli.tools.check_cell_imports` | `scripts/` |
| `scripts/benchmark_iterative_loop.py` | `polaris.delivery.cli.tools.benchmark_iterative_loop` | `scripts/` |

---

## 4. Breaking Changes

1. **All v2 routes require authentication**: Every endpoint under `/v2/*` includes `dependencies=[Depends(require_auth)]`. Unauthenticated requests receive `401 unauthorized`.

2. **Error response shape changed**: Routes using `StructuredHTTPException` now return:
   ```json
   {"error": {"code": "CODE", "message": "...", "details": {}}}
   ```
   instead of the previous bare `{"detail": "..."}`.

3. **Role chat streaming normalized**: The SSE event types from `/v2/role/{role}/chat/stream` are normalized to: `thinking_chunk`, `content_chunk`, `tool_call`, `tool_result`, `fingerprint`, `complete`, `error`, `ping`.

4. **Director status endpoint simplified**: `/v2/director/status` returns only local Director state. For unified runtime projection, use the WebSocket status endpoint.

5. **Task list source parameter**: `/v2/director/tasks` accepts `source=auto|local|workflow`. Default is `auto`, which prefers workflow tasks when available.

---

## 5. Response Model Changes

All new v2 routes use explicit Pydantic response models. Key models introduced:

### 5.1 PM / Director / Orchestration

- `PMOrchestrationResponse` (`run_id`, `status`, `workspace`, `stage`, `message`)
- `DirectorOrchestrationResponse` (`run_id`, `status`, `workspace`, `tasks_queued`, `message`)
- `OrchestrationSnapshotResponse` (`schema_version`, `run_id`, `workspace`, `mode`, `status`, `current_phase`, `overall_progress`, `tasks`)
- `TaskSnapshotResponse` (`task_id`, `status`, `phase`, `role_id`, `current_file`, `progress_percent`, `retry_count`, `error_category`, `error_message`)
- `TaskResponse` (`id`, `subject`, `description`, `status`, `priority`, `claimed_by`, `result`, `metadata`)
- `DirectorStatusResponse` (`state`, `workspace`, `metrics`, `tasks`, `workers`, `token_budget`)

### 5.2 Services

- `TaskResponse` (`id`, `command`, `state`, `timeout`, `result`)
- `TodoItemResponse` (`id`, `content`, `status`, `priority`, `tags`)
- `SecurityCheckResponse` (`is_safe`, `reason`, `suggested_alternative`)
- `TokenStatusResponse` (`used_tokens`, `budget_limit`, `remaining_tokens`, `percent_used`, `is_exceeded`)

### 5.3 Resident

- `ResidentStartRequest`, `ResidentIdentityPatch`, `DecisionRecordPayload`, `GoalProposalPayload`, `GoalRunRequest`

### 5.4 Role Chat / Common Schemas

- `RoleChatResponse`, `RoleChatStatusResponse`, `RoleChatPingResponse`, `RoleListResponse`
- `CacheStatsResponse`, `CacheClearResponse`, `RoleLLMEventsResponse`, `AllLLMEventsResponse`
- `AgentSessionResponse`, `AgentMessageResponse`, `AgentTurnResponse`, `AgentMemorySearchResponse`

---

## 6. Error Format Standardization

### 6.1 Migration to StructuredHTTPException

The `StructuredHTTPException` class in `polaris/delivery/http/routers/_shared.py` replaces bare `HTTPException` for all v2 routes:

```python
class StructuredHTTPException(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.structured_message = message
        self.structured_details = dict(details) if details else {}
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message, "details": self.structured_details},
        )
```

### 6.2 Error Handler Registration

`polaris/delivery/http/error_handlers.py` registers handlers for:

- `DomainException` -> `{error: {code, message, details}}`
- `RequestValidationError` -> `{error: {code: "VALIDATION_ERROR", message, details: {errors}}}`
- `StructuredHTTPException` -> `{error: {code, message, details}}`
- Generic `Exception` -> `{error: {code: "INTERNAL_ERROR", message, details: {type}}}`

### 6.3 Common Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `INVALID_REQUEST` | 400 | Missing or invalid input |
| `UNSUPPORTED_ROLE` | 400 | Role not in registered list |
| `VALIDATION_ERROR` | 422 | Pydantic validation failure |
| `RUNTIME_ROLES_NOT_READY` | 409 | Required LLM roles not ready |
| `PM_ROLE_NOT_CONFIGURED` | 409 | PM LLM not configured |
| `ARCHITECT_NOT_CONFIGURED` | 409 | Architect LLM not configured |
| `RUN_NOT_FOUND` | 404 | Factory/orchestration run missing |
| `REPORT_NOT_FOUND` | 404 | LLM test report missing |
| `ROLE_NOT_FOUND` | 404 | Court actor not found |
| `SESSION_NOT_FOUND` | 404 | Agent session missing |
| `GENERATION_FAILED` | 500 | LLM generation error |
| `INTERNAL_ERROR` | 500 | Unhandled exception |

---

## 7. SSE Event Type Unification

### 7.1 Canonical Event Schema

All SSE endpoints use the unified schema from `sse_event_generator` in `polaris/delivery/http/routers/sse_utils.py`:

```
event: <type>
data: <json-payload>

```

### 7.2 Event Types

| Event Type | When Emitted |
|------------|--------------|
| `thinking_chunk` | LLM thinking/reasoning token |
| `content_chunk` | LLM response content token |
| `tool_call` | Tool invocation start |
| `tool_result` | Tool execution result |
| `fingerprint` | Response fingerprint |
| `complete` | Stream finished successfully |
| `error` | Terminal error |
| `ping` | Keep-alive (timeout-based) |

### 7.3 Security Hardening (v2)

- **S1**: Payload size limits (256KB max)
- **S2**: Schema validation with `RuntimeEventEnvelope`
- **S3**: Replay attack protection with timestamp validation (1-hour window)
- **S4**: Cryptographically random ephemeral consumer names
- **S5**: Subject pattern validation (`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}$`)
- **S6**: Event timestamp freshness validation

### 7.4 JetStream SSE Consumer

`SSEJetStreamConsumer` provides cursor-based resume, ephemeral consumers, and graceful cleanup. The `sse_jetstream_generator` preserves original stream exceptions over secondary disconnect errors (B4 fix).

---

## 8. Auth Changes

### 8.1 New RBAC Skeleton

File: `polaris/delivery/http/middleware/rbac.py`

- `UserRole` enum: `VIEWER` (1), `DEVELOPER` (2), `ADMIN` (3)
- `RBACMiddleware`: ASGI middleware that initializes `request.state.user_role = VIEWER`
- `extract_role_from_request`: Reads `auth_context.metadata["roles"]`; ignores client headers like `X-User-Role`
- `require_role(allowed)`: Dependency factory returning a checker that raises `403` if role not in allowed set

### 8.2 Auth Context Binding

`require_auth` in `polaris/delivery/http/dependencies.py`:

- Validates `Authorization` bearer token only (query params rejected)
- Binds `SimpleAuthContext(principal="authenticated", scopes={"*"}, metadata={"roles": ["viewer"]})`
- Sets `request.state.user_role = UserRole.VIEWER`

### 8.3 Permission Checking

`require_permission(permission)` factory checks `auth_context.has_scope(permission)` and raises `403` on denial.

---

## 9. Test Coverage

### 9.1 New Test Files

| Test File | Coverage |
|-----------|----------|
| `polaris/tests/unit/delivery/http/routers/test_v2_error_paths.py` | Structured error paths (400, 403, 404, 409, 500) across role_chat, factory, docs, tests, court, providers, pm_chat |
| `polaris/tests/unit/delivery/http/test_rbac.py` | UserRole parsing, level ordering, `require_role` allowed/denied, RBACMiddleware defaults, auth integration |
| `polaris/tests/test_sse_regression.py` | B4 exception shadowing fix, B5 UnicodeDecodeError fix, negative last_event_id rejection, publish subject validation |
| `polaris/tests/unit/delivery/http/routers/test_*_v2.py` | Per-router v2 endpoint smoke tests (agents, arsenal, cognitive_runtime, court, factory, files, history, interview, lancedb, logs, memos, memory, ollama, permissions, pm_chat, pm_management, primary, providers, system, tests) |
| `polaris/tests/integration/delivery/test_factory_lifecycle.py` | Factory run lifecycle (create, query, events, artifacts, audit) |
| `polaris/tests/integration/delivery/routers/test_pm_chat_router.py` | PM chat router integration |
| `polaris/tests/integration/roles/runtime/test_session_orchestrator_e2e.py` | Session orchestrator E2E |

### 9.2 Test Statistics

- `pytest --collect-only -q` (2026-04-24): **13,511 collected / 62 errors**
- Coverage (2026-04-24): **23.3%** (69,360 / 297,487 lines)
- 0% coverage modules: 390 (delivery: 155, cells: 103, kernelone: 103)

---

## 10. Governance Improvements

### 10.1 Cell Internal Fence

- Cross-Cell access is restricted to `public/` contracts only.
- `internal/` directories are fenced; direct imports trigger catalog governance gates.
- All 63 declared Cells in `docs/graph/catalog/cells.yaml` now have descriptors (60/63 with generated `descriptor.pack.json`).

### 10.2 Context Packs

- `ContextHandoffPack` is the canonical handoff contract between roles.
- `roles.kernel` is prohibited from creating a second handoff schema.
- Context packs are versioned and validated against `infrastructure/accel/schema/context_pack.schema.json`.

### 10.3 Dependency Alignment

- `depends_on` in `cell.yaml` files is aligned with the catalog.
- 25 high-level遗留 gaps in catalog gate were identified for remediation.
- `fitness-rules.yaml` blockers are tracked but not yet fully automated.

### 10.4 Descriptor Coverage

- Descriptor pack generator: `python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`
- Current coverage: 60/63 Cells have `generated/descriptor.pack.json`

### 10.5 Verification Gates

Key automated gates:

- `catalog_governance_audit`
- `catalog_governance_fail_on_new`
- `kernelone_release_gate`
- `delivery_cli_hygiene_gate`
- `opencode_convergence_gate`
- `manifest_catalog_reconciliation_gate`
- `structural_bug_governance_gate`
- `tool_calling_canonical_gate`

---

## Files Modified / Created

- `polaris/delivery/http/v2/__init__.py`
- `polaris/delivery/http/v2/pm.py`
- `polaris/delivery/http/v2/director.py`
- `polaris/delivery/http/v2/orchestration.py`
- `polaris/delivery/http/v2/resident.py`
- `polaris/delivery/http/v2/services.py`
- `polaris/delivery/http/v2/audit.py`
- `polaris/delivery/http/v2/observability.py`
- `polaris/delivery/http/routers/_shared.py` (StructuredHTTPException)
- `polaris/delivery/http/routers/role_chat.py`
- `polaris/delivery/http/routers/sse_utils.py`
- `polaris/delivery/http/middleware/rbac.py`
- `polaris/delivery/http/dependencies.py`
- `polaris/delivery/http/error_handlers.py`
- `polaris/delivery/http/schemas/common.py`
- `polaris/kernelone/errors.py`
- `polaris/tests/unit/delivery/http/routers/test_v2_error_paths.py`
- `polaris/tests/unit/delivery/http/test_rbac.py`
- `polaris/tests/test_sse_regression.py`
