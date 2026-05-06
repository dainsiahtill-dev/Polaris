# Polaris V2 API Quick Reference

Concise developer reference for the Polaris V2 HTTP API. All paths are canonical `/v2/*` routes.

---

## 1. Endpoint Index

### System & Health
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/health` | Backend health including PM/Director runtime state |
| GET | `/v2/ready` | Readiness probe (LanceDB + runtime checks) |
| GET | `/v2/live` | Liveness probe |
| GET | `/v2/state/snapshot` | Application state snapshot |
| POST | `/v2/app/shutdown` | Gracefully shutdown PM and Director |

### Settings
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/settings` | Get workspace and runtime settings |
| POST | `/v2/settings` | Update settings atomically |

### Role Chat (Unified)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/role/chat/ping` | Health check for role chat |
| GET | `/v2/role/chat/roles` | List all registered LLM roles |
| GET | `/v2/role/{role}/chat/status` | LLM readiness for a role |
| POST | `/v2/role/{role}/chat` | Chat with a role (non-streaming) |
| POST | `/v2/role/{role}/chat/stream` | Chat with a role (SSE streaming) |
| GET | `/v2/role/{role}/llm-events` | LLM call events for a role |
| GET | `/v2/role/llm-events` | LLM events across all roles |
| GET | `/v2/role/cache-stats` | LLM cache statistics |
| POST | `/v2/role/cache-clear` | Clear global LLM cache |

### PM Chat
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/pm/chat/ping` | PM chat health check |
| GET | `/v2/pm/chat/status` | PM role LLM readiness |
| POST | `/v2/pm/chat` | PM chat (non-streaming) |
| POST | `/v2/pm/chat/stream` | PM chat (SSE streaming) |

### PM Management
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/pm/status` | PM system status |
| GET | `/v2/pm/health` | Project health analysis |
| POST | `/v2/pm/init` | Initialize PM system |
| GET | `/v2/pm/documents` | List tracked documents |
| GET | `/v2/pm/documents/{doc_path}` | Get document (opt. version) |
| POST | `/v2/pm/documents/{doc_path}` | Create or update document |
| DELETE | `/v2/pm/documents/{doc_path}` | Delete document |
| GET | `/v2/pm/documents/{doc_path}/versions` | Document versions |
| GET | `/v2/pm/documents/{doc_path}/compare` | Compare two versions |
| GET | `/v2/pm/search/documents` | Search documents |
| GET | `/v2/pm/tasks` | List tasks |
| GET | `/v2/pm/tasks/{task_id}` | Get task by ID |
| GET | `/v2/pm/tasks/{task_id}/assignments` | Task assignment history |
| GET | `/v2/pm/tasks/history` | Task history |
| GET | `/v2/pm/tasks/director` | Director-dispatched tasks |
| GET | `/v2/pm/search/tasks` | Search tasks |
| GET | `/v2/pm/requirements` | List requirements |
| GET | `/v2/pm/requirements/{req_id}` | Get requirement by ID |

### PM Orchestration
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/pm/run_once` | Run PM once |
| POST | `/v2/pm/start` | Start PM loop |
| POST | `/v2/pm/stop` | Stop PM |
| GET | `/v2/pm/status` | PM process status |
| POST | `/v2/pm/run` | Unified PM orchestration run |
| GET | `/v2/pm/runs/{run_id}` | Query PM run status |
| GET | `/v2/pm/llm-events` | PM LLM events |
| GET | `/v2/pm/cache-stats` | LLM cache stats |
| POST | `/v2/pm/cache-clear` | Clear LLM cache |
| GET | `/v2/pm/token-budget-stats` | Token budget stats |

### Director
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/director/start` | Start Director service |
| POST | `/v2/director/stop` | Stop Director service |
| GET | `/v2/director/status` | Director status (local) |
| POST | `/v2/director/tasks` | Create a task |
| GET | `/v2/director/tasks` | List tasks |
| GET | `/v2/director/tasks/{task_id}` | Get task by ID |
| POST | `/v2/director/tasks/{task_id}/cancel` | Cancel a task |
| GET | `/v2/director/workers` | List workers |
| GET | `/v2/director/workers/{worker_id}` | Get worker by ID |
| POST | `/v2/director/run` | Unified Director orchestration run |
| GET | `/v2/director/runs/{run_id}` | Query Director run status |
| GET | `/v2/director/llm-events` | LLM events (global/role filtered) |
| GET | `/v2/director/cache-stats` | LLM cache stats |
| POST | `/v2/director/cache-clear` | Clear LLM cache |
| GET | `/v2/director/token-budget-stats` | Token budget stats |

### Factory
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/factory/runs` | List factory runs |
| POST | `/v2/factory/runs` | Start a factory run |
| GET | `/v2/factory/runs/{run_id}` | Run status |
| GET | `/v2/factory/runs/{run_id}/events` | Audit events |
| GET | `/v2/factory/runs/{run_id}/audit-bundle` | Machine-readable audit bundle |
| GET | `/v2/factory/runs/{run_id}/stream` | SSE stream of status/events |
| POST | `/v2/factory/runs/{run_id}/control` | Control run (cancel) |
| GET | `/v2/factory/runs/{run_id}/artifacts` | List artifacts |

### Role Sessions
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/roles/sessions` | Create a role session |
| GET | `/v2/roles/sessions` | List sessions |
| GET | `/v2/roles/sessions/{session_id}` | Get session |
| PUT | `/v2/roles/sessions/{session_id}` | Update session |
| DELETE | `/v2/roles/sessions/{session_id}` | Delete session (soft/hard) |
| GET | `/v2/roles/sessions/{session_id}/messages` | Get messages |
| POST | `/v2/roles/sessions/{session_id}/messages` | Send message |
| POST | `/v2/roles/sessions/{session_id}/messages/stream` | Send message (SSE) |
| POST | `/v2/roles/sessions/{session_id}/actions/attach` | Attach to workflow |
| POST | `/v2/roles/sessions/{session_id}/actions/detach` | Detach from workflow |
| GET | `/v2/roles/sessions/{session_id}/artifacts` | Session artifacts |
| GET | `/v2/roles/sessions/{session_id}/audit` | Audit log |
| GET | `/v2/roles/sessions/{session_id}/memory/search` | Search Context OS memory |
| GET | `/v2/roles/sessions/{session_id}/memory/artifacts/{artifact_id}` | Read artifact |
| GET | `/v2/roles/sessions/{session_id}/memory/episodes/{episode_id}` | Read episode |
| GET | `/v2/roles/sessions/{session_id}/memory/state` | Read state entry |
| POST | `/v2/roles/sessions/{session_id}/actions/export` | Export session |
| POST | `/v2/roles/sessions/{session_id}/actions/export-to-workflow` | Export to workflow |
| GET | `/v2/roles/capabilities/{role}` | Role capability config |

### Conversations
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/conversations` | Create conversation |
| GET | `/v2/conversations` | List conversations |
| GET | `/v2/conversations/{conversation_id}` | Get conversation |
| PUT | `/v2/conversations/{conversation_id}` | Update conversation |
| DELETE | `/v2/conversations/{conversation_id}` | Delete conversation |
| POST | `/v2/conversations/{conversation_id}/messages` | Add message |
| GET | `/v2/conversations/{conversation_id}/messages` | List messages |
| POST | `/v2/conversations/{conversation_id}/messages/batch` | Batch add messages |
| DELETE | `/v2/conversations/{conversation_id}/messages/{message_id}` | Delete message |

### Agent
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/sessions` | List agent sessions |
| GET | `/v2/sessions/{session_id}` | Get agent session |
| POST | `/v2/sessions/{session_id}/messages` | Send message (non-streaming) |
| POST | `/v2/sessions/{session_id}/messages/stream` | Send message (SSE) |
| DELETE | `/v2/sessions/{session_id}` | Delete agent session |
| GET | `/v2/sessions/{session_id}/memory/search` | Search session memory |
| GET | `/v2/sessions/{session_id}/memory/artifacts/{artifact_id}` | Read artifact |
| GET | `/v2/sessions/{session_id}/memory/episodes/{episode_id}` | Read episode |
| GET | `/v2/sessions/{session_id}/memory/state` | Read state entry |
| POST | `/v2/turn` | Execute a single agent turn |

### Cognitive Runtime
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/cognitive-runtime/resolve-context` | Resolve runtime context |
| POST | `/v2/cognitive-runtime/lease-edit-scope` | Lease edit scope |
| POST | `/v2/cognitive-runtime/validate-change-set` | Validate change set |
| POST | `/v2/cognitive-runtime/runtime-receipts` | Record runtime receipt |
| GET | `/v2/cognitive-runtime/runtime-receipts/{receipt_id}` | Get receipt |
| POST | `/v2/cognitive-runtime/handoffs/export` | Export handoff pack |
| POST | `/v2/cognitive-runtime/handoffs/rehydrate` | Rehydrate handoff pack |
| POST | `/v2/cognitive-runtime/map-diff-to-cells` | Map diff to cells |
| POST | `/v2/cognitive-runtime/projection-compile` | Request projection compile |
| POST | `/v2/cognitive-runtime/promote-or-reject` | Promote or reject changes |
| POST | `/v2/cognitive-runtime/rollback-ledger` | Record rollback ledger |
| GET | `/v2/cognitive-runtime/handoffs/{handoff_id}` | Get handoff pack |

### LLM Configuration
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/llm/config` | Get LLM config (redacted) |
| POST | `/v2/llm/config` | Save and reconcile LLM config |
| POST | `/v2/llm/config/migrate` | Migrate legacy config |
| GET | `/v2/llm/status` | Overall LLM system status |
| GET | `/v2/llm/runtime-status` | Runtime status for all roles |
| GET | `/v2/llm/runtime-status/{role_id}` | Runtime status for one role |

### LLM Providers
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/llm/providers` | List all providers |
| GET | `/v2/llm/providers/{provider_type}/info` | Provider info |
| GET | `/v2/llm/providers/{provider_type}/config` | Default config schema |
| POST | `/v2/llm/providers/{provider_type}/validate` | Validate provider config |
| POST | `/v2/llm/providers/health-all` | Health check all providers |

### LLM Tests
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/llm/test` | Run LLM readiness tests |
| POST | `/v2/llm/test/stream` | Stream test results (SSE) |
| GET | `/v2/llm/test/{test_run_id}` | Get test report |
| GET | `/v2/llm/test/{test_run_id}/transcript` | Get test transcript |

### Interview
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/llm/interview/ask` | Generate interview answer |
| POST | `/v2/llm/interview/save` | Save interview report |
| POST | `/v2/llm/interview/cancel` | Cancel interview stream |
| POST | `/v2/llm/interview/stream` | Stream interview (SSE) |

### Docs Init
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/docs/init/dialogue` | Docs wizard dialogue turn |
| POST | `/v2/docs/init/dialogue/stream` | Docs wizard dialogue (SSE) |
| POST | `/v2/docs/init/suggest` | Suggest docs fields |
| POST | `/v2/docs/init/preview` | Preview generated docs |
| POST | `/v2/docs/init/preview/stream` | Preview docs (SSE) |
| POST | `/v2/docs/init/apply` | Apply generated docs |

### Runtime
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/runtime/storage/layout` | Workspace storage layout |
| POST | `/v2/runtime/clear` | Clear runtime scope |
| GET | `/v2/runtime/migration/status` | Storage migration status |
| POST | `/v2/runtime/reset/tasks` | Stop PM/Director, reset tasks |

### History
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/history/runs` | List historical runs |
| GET | `/v2/history/runs/{run_id}/manifest` | Archived run manifest |
| GET | `/v2/history/runs/{run_id}/events` | Archived run events |
| GET | `/v2/history/tasks/snapshots` | Task snapshots |
| GET | `/v2/history/factory/snapshots` | Factory snapshots |

### Permissions
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/permissions/v2/check` | Check permission |
| GET | `/v2/permissions/v2/effective` | Get effective permissions |
| GET | `/v2/permissions/v2/roles` | List roles |
| POST | `/v2/permissions/v2/assign` | Assign role |
| GET | `/v2/permissions/v2/policies` | List policies |

### Logs
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/query` | Query canonical log events |
| POST | `/v2/user-action` | Log a user action |
| GET | `/v2/channels` | List log channels |

### Memory
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/state` | Anthropomorphic memory state |
| DELETE | `/v2/memories/{memory_id}` | Delete memory entry |

### Arsenal
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/vision/status` | Vision service status |
| POST | `/v2/vision/analyze` | Analyze image |
| GET | `/v2/scheduler/status` | Scheduler status |
| POST | `/v2/scheduler/start` | Start scheduler |
| POST | `/v2/scheduler/stop` | Stop scheduler |
| GET | `/v2/code_map` | 3D code map |
| POST | `/v2/code/index` | Index workspace code |
| POST | `/v2/code/search` | Search indexed code |
| GET | `/v2/mcp/status` | MCP server status |
| GET | `/v2/director/capabilities` | Director capability matrix |

### Court
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/court/topology` | Court topology structure |
| GET | `/v2/court/state` | Current court state |
| GET | `/v2/court/actors/{role_id}` | Role detail |
| GET | `/v2/court/scenes/{scene_id}` | Scene configuration |
| GET | `/v2/court/mapping` | Tech-to-court role mapping |

### Stream
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v2/stream/chat` | Neural weave SSE stream chat |
| POST | `/v2/stream/chat/backpressure` | Stream with backpressure |
| GET | `/v2/stream/health` | Stream subsystem health |

### Services
| Method | Path | Description |
|--------|------|-------------|
| POST | `/services/tasks` | Create background task |
| GET | `/services/tasks/{task_id}` | Get background task |
| GET | `/services/tasks` | List background tasks |
| POST | `/services/todos` | Create todo |
| GET | `/services/todos` | List todos |
| GET | `/services/todos/summary` | Todo summary |
| POST | `/services/todos/{item_id}/done` | Mark todo done |
| GET | `/services/tokens/status` | Token budget status |
| POST | `/services/tokens/record` | Record token usage |
| POST | `/services/security/check` | Check command safety |
| GET | `/services/transcript` | Get transcript |
| GET | `/services/transcript/session` | Transcript session info |
| POST | `/services/transcript/message` | Record transcript message |

### Misc
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/ollama/models` | List Ollama models |
| POST | `/v2/ollama/stop` | Stop Ollama models |
| GET | `/v2/lancedb/status` | LanceDB status |
| GET | `/v2/memos/list` | List workspace memos |
| GET | `/v2/files/read` | Read workspace file |

---

## 2. Authentication

All V2 endpoints require authentication via the `Authorization` header.

```bash
curl -H "Authorization: Bearer <token>" http://localhost:49977/v2/health
```

### Rules
- **Header only**: `Authorization: Bearer <token>`
- **Query param tokens are rejected** to prevent leakage in logs/history/referer.
- **RBAC**: The server ignores client-supplied `X-User-Role`. Effective role is resolved server-side from `SimpleAuthContext` metadata and defaults to `VIEWER`.
- Some endpoints additionally require specific roles via `require_role([UserRole.ADMIN, UserRole.DEVELOPER])`.

### Auth Flow
```
Client -> Authorization: Bearer <token>
Server -> require_auth() validates token
Server -> binds SimpleAuthContext(principal="authenticated", scopes={"*"}, metadata={"roles": ["viewer"]})
Server -> RBAC middleware extracts role from auth_context (ignores X-User-Role)
```

---

## 3. Error Codes

All errors follow the unified `StructuredHTTPException` format (ADR-003):

```json
{
  "code": "ERROR_CODE",
  "message": "Human-readable description",
  "details": {
    "key": "additional context"
  }
}
```

### Common Error Codes

| Code | HTTP | Meaning |
|------|------|---------|
| `UNAUTHORIZED` | 401 | Invalid or missing bearer token |
| `INVALID_REQUEST` | 400 | Malformed request payload |
| `VALIDATION_ERROR` | 400 | Domain validation failed |
| `WORKSPACE_NOT_CONFIGURED` | 400 | Workspace missing or empty |
| `RUNTIME_ROLES_NOT_READY` | 409 | Required LLM roles not ready |
| `LLM_NOT_READY` | 409 | LLM runtime not ready |
| `PM_RUNNING` | 409 | Cannot switch workspace while PM running |
| `DIRECTOR_RUNNING` | 409 | Cannot switch workspace while Director running |
| `PM_NOT_INITIALIZED` | 400 | PM system not initialized |
| `SESSION_NOT_FOUND` | 404 | Session/Conversation not found |
| `TASK_NOT_FOUND` | 404 | Task not found |
| `RUN_NOT_FOUND` | 404 | Factory/PM/Director run not found |
| `DOCUMENT_NOT_FOUND` | 404 | Document not found |
| `REQUIREMENT_NOT_FOUND` | 404 | Requirement not found |
| `MANIFEST_NOT_FOUND` | 404 | Archived manifest not found |
| `ROUND_NOT_FOUND` | 404 | History round not found |
| `PROVIDER_NOT_FOUND` | 404 | LLM provider not found |
| `ROLE_NOT_CONFIGURED` | 404 | Role not configured in LLM config |
| `ARCHITECT_NOT_CONFIGURED` | 409 | Architect role missing provider/model |
| `UNSUPPORTED_ROLE` | 400 | Role not in registered roles list |
| `GENERATION_FAILED` | 500 | LLM generation failed |
| `STATUS_CHECK_ERROR` | 500 | Status check failed |
| `INTERNAL_ERROR` | 500 | Generic server error |
| `INVALID_CONFIG` | 400 | Invalid LLM config payload |
| `INVALID_DOCS_PATH` | 400 | Docs path outside allowed root |
| `MEMORY_STORE_NOT_INITIALIZED` | 503 | Memory store unavailable |
| `LOG_USER_ACTION_FAILED` | 500 | Failed to write user action log |

---

## 4. SSE Events

Streaming endpoints return `text/event-stream` with the following canonical event types.

### Generic SSE Format
```
event: <type>
data: <json-payload>

```

### Canonical Event Types

| Event | Description | Payload Example |
|-------|-------------|-----------------|
| `thinking_chunk` | Reasoning/thinking token | `{"content": "..."}` |
| `content_chunk` | Response content token | `{"content": "..."}` |
| `tool_call` | Tool invocation | `{"tool": "name", "args": {}}` |
| `tool_result` | Tool execution result | `{"result": {}}` |
| `fingerprint` | Response fingerprint | `{"fingerprint": "..."}` |
| `complete` | Stream completed | `{"content": "...", "thinking": "..."}` |
| `error` | Error occurred | `{"error": "message"}` |
| `ping` | Keep-alive | `{}` |
| `status` | Status update | `{...status payload...}` |
| `event` | Generic audit event | `{...event payload...}` |
| `stage` | Progress stage | `{"stage": "name", "progress": 50}` |

### Router-Specific SSE Patterns

**Role Chat / Agent / Stream Router**
- `thinking_chunk`, `content_chunk`, `tool_call`, `tool_result`, `complete`, `error`

**Factory Stream**
- `status` (run status snapshot), `event` (audit events), `complete`, `error`

**Docs Init Preview Stream**
- `stage` (progress stages), `thinking` (LLM thinking), `complete`, `error`

**LLM Test Stream**
- `start`, `suite_start`, `suite_result`, `suite_complete`, `complete`

### SSE Security
- Payload size limit: 256KB (`MAX_PAYLOAD_SIZE`)
- Timestamp freshness validation (replay window: 1 hour)
- HMAC-SHA256 event signatures for integrity
- Cryptographically random ephemeral consumer names

---

## 5. Response Format

### Success Envelope
Most endpoints return an `ok` envelope:

```json
{
  "ok": true,
  "...": "resource-specific fields"
}
```

### Error Envelope
```json
{
  "code": "ERROR_CODE",
  "message": "Description",
  "details": {}
}
```

### List Pagination
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 50
}
```

---

## 6. Examples

### Role Chat (Non-Streaming)
```bash
curl -X POST http://localhost:49977/v2/role/pm/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "Plan a login feature"}'
```

### Role Chat (Streaming)
```bash
curl -X POST http://localhost:49977/v2/role/pm/chat/stream \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "Plan a login feature"}'
```

### Session Creation
```bash
curl -X POST http://localhost:49977/v2/roles/sessions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "pm",
    "host_kind": "electron_workbench",
    "workspace": "/path/to/workspace",
    "session_type": "workbench",
    "title": "My PM Session"
  }'
```

### Factory Run
```bash
# Start a factory run
curl -X POST http://localhost:49977/v2/factory/runs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "directive": "Implement user authentication",
    "start_from": "auto",
    "run_director": true,
    "loop": false
  }'

# Stream run events
curl -N http://localhost:49977/v2/factory/runs/{run_id}/stream \
  -H "Authorization: Bearer <token>"
```

### Settings Update
```bash
curl -X POST http://localhost:49977/v2/settings \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace": "C:/projects/my-app",
    "ramdisk_root": "",
    "debug_tracing": true
  }'
```

---

## Notes

- **Base URL**: Default backend runs on `http://localhost:49977`
- **Deprecated routes**: Many non-`/v2/*` paths exist as backward-compatible aliases but should not be used for new integrations.
- **Workspace**: Most endpoints are workspace-scoped; the active workspace is derived from settings or provided explicitly.
- **UTF-8**: All text I/O uses explicit UTF-8 encoding.
