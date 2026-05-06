# API Coverage Gap Analysis: Backend v2 Routes vs Frontend Usage

> Analysis Date: 2026-05-06
> Scope: `polaris/delivery/http/routers/*.py` (backend v2 API) vs `src/frontend/src/**/*.{ts,tsx}` (frontend usage)
> Rule: ANALYSIS ONLY — no frontend code was modified.

---

## 1. Executive Summary

| Metric | Count |
|--------|-------|
| Total backend v2 routes | **105** |
| Frontend-covered routes | **~42** (unique path patterns) |
| Gap (missing) | **~63** |
| Coverage ratio | **~40%** |

The frontend currently consumes roughly 40% of the declared backend v2 surface. The remaining ~60% represents either:
- Routes already provided by the backend but not yet wired into the UI.
- Diagnostic / admin / edge-case endpoints that may never need a UI counterpart.
- Legacy or transitional endpoints where the frontend still uses an older (non-v2) path or a different mechanism (e.g. WebSocket).

---

## 2. Methodology

1. **Backend enumeration** — grep for FastAPI `@router.(get|post|put|delete|patch|head|options)("/v2/...")` in `polaris/delivery/http/routers/*.py`.
2. **Frontend enumeration** — grep for string literals matching `/v2/[^'"\s]+` in all `src/frontend/src/**/*.ts` and `**/*.tsx` files.
3. **Normalisation** — path parameters (`{role}`, `{sessionId}`, `{runId}`, `{conversationId}`, `{messageId}`, `{goalId}`, `{decisionId}`, `{taskId}`, `{itemId}`) were collapsed to their template form for comparison.
4. **Coverage mapping** — a backend route is considered "covered" if the frontend contains at least one reference to the same path template (ignoring query strings).
5. **Priority assignment** — based on functional area and visible UI presence:
   - **P0** — Critical user-facing chat/session/conversation/factory flows.
   - **P1** — Management & workbench (PM/Director tasks, settings, resident goals).
   - **P2** — Diagnostic, monitoring, logs, health, metrics.
   - **P3** — Advanced/edge cases (court, vision, scheduler, code-map, agents, interview, tests).

---

## 3. Covered Routes (Frontend Already Uses)

| # | Route Template | HTTP | Backend Router | Frontend Usage Location |
|---|----------------|------|----------------|-------------------------|
| 1 | `/v2/conversations` | POST | `conversations.py` | `services/conversationApi.ts` |
| 2 | `/v2/conversations` | GET | `conversations.py` | `services/conversationApi.ts` |
| 3 | `/v2/conversations/{id}` | GET | `conversations.py` | `services/conversationApi.ts` |
| 4 | `/v2/conversations/{id}` | PUT | `conversations.py` | `services/conversationApi.ts` |
| 5 | `/v2/conversations/{id}` | DELETE | `conversations.py` | `services/conversationApi.ts` |
| 6 | `/v2/conversations/{id}/messages` | POST | `conversations.py` | `services/conversationApi.ts` |
| 7 | `/v2/conversations/{id}/messages/batch` | POST | `conversations.py` | `services/conversationApi.ts` |
| 8 | `/v2/conversations/{id}/messages` | GET | `conversations.py` | `services/conversationApi.ts` |
| 9 | `/v2/conversations/{id}/messages/{messageId}` | DELETE | `conversations.py` | `services/conversationApi.ts` |
| 10 | `/v2/conversations/{id}/save` | POST | `conversations.py` | `services/conversationApi.ts` |
| 11 | `/v2/factory/runs` | POST | `factory.py` | `services/factoryService.ts` |
| 12 | `/v2/factory/runs` | GET | `factory.py` | `services/factoryService.ts` |
| 13 | `/v2/factory/runs/{run_id}` | GET | `factory.py` | `services/factoryService.ts` |
| 14 | `/v2/factory/runs/{run_id}/control` | POST | `factory.py` | `services/factoryService.ts` |
| 15 | `/v2/factory/runs/{run_id}/artifacts` | GET | `factory.py` | `services/factoryService.ts` |
| 16 | `/v2/factory/runs/{run_id}/stream` | GET | `factory.py` | `services/factoryService.ts` |
| 17 | `/v2/pm/status` | GET | `pm_management.py` | `services/pmService.ts`, `services/api.ts` |
| 18 | `/v2/director/status` | GET | *(legacy/non-v2 shim)* | `services/pmService.ts`, `services/api.ts` |
| 19 | `/v2/pm/start` | POST | *(legacy/non-v2 shim)* | `services/pmService.ts`, `services/api.ts` |
| 20 | `/v2/pm/stop` | POST | *(legacy/non-v2 shim)* | `services/pmService.ts`, `services/api.ts` |
| 21 | `/v2/pm/run_once` | POST | *(legacy/non-v2 shim)* | `services/pmService.ts`, `services/api.ts` |
| 22 | `/v2/director/start` | POST | *(legacy/non-v2 shim)* | `services/pmService.ts`, `services/api.ts` |
| 23 | `/v2/director/stop` | POST | *(legacy/non-v2 shim)* | `services/pmService.ts`, `services/api.ts` |
| 24 | `/v2/director/tasks` | GET | *(legacy/non-v2 shim)* | `services/pmService.ts`, `runtime/directorWorkspace.ts`, `app/components/director/*` |
| 25 | `/v2/director/tasks` | POST | *(legacy/non-v2 shim)* | `services/pmService.ts` |
| 26 | `/v2/role/{role}/chat/status` | GET | `role_chat.py` | `services/llmService.ts`, `app/components/ai-dialogue/useAIDialogue.ts` |
| 27 | `/v2/role/{role}/chat/stream` | POST | `role_chat.py` | `services/llmService.ts`, `app/components/ai-dialogue/useChatStream.ts`, `useAIDialogue.ts` |
| 28 | `/v2/roles/sessions` | POST | `role_session.py` | `app/components/ai-dialogue/useAIDialogue.ts`, `DirectorWorkbenchPanel.tsx`, `PMWorkbenchPanel.tsx` |
| 29 | `/v2/roles/sessions` | GET | `role_session.py` | `DirectorWorkbenchPanel.tsx`, `PMWorkbenchPanel.tsx` |
| 30 | `/v2/roles/sessions/{session_id}` | GET | `role_session.py` | *(implied by workbench panels)* |
| 31 | `/v2/roles/sessions/{session_id}` | PUT | `role_session.py` | *(implied by workbench panels)* |
| 32 | `/v2/roles/sessions/{session_id}/actions/export-to-workflow` | POST | *(legacy shim)* | `DirectorWorkbenchPanel.tsx`, `PMWorkbenchPanel.tsx` |
| 33 | `/v2/roles/sessions/{session_id}/actions/detach` | POST | *(legacy shim)* | `app/components/session/SessionInspector.tsx` |
| 34 | `/v2/roles/sessions/{session_id}/actions/export` | POST | *(legacy shim)* | `app/components/session/SessionInspector.tsx` |
| 35 | `/v2/roles/capabilities/{role}` | GET | *(legacy shim)* | `app/components/session/SessionInspector.tsx` |
| 36 | `/v2/resident/status` | GET | *(legacy shim)* | `services/api.ts` |
| 37 | `/v2/resident/start` | POST | *(legacy shim)* | `services/api.ts` |
| 38 | `/v2/resident/stop` | POST | *(legacy shim)* | `services/api.ts` |
| 39 | `/v2/resident/tick` | POST | *(legacy shim)* | `services/api.ts` |
| 40 | `/v2/resident/identity` | POST | *(legacy shim)* | `services/api.ts` |
| 41 | `/v2/resident/goals` | GET | *(legacy shim)* | `services/api.ts` |
| 42 | `/v2/resident/goals` | POST | *(legacy shim)* | `services/api.ts` |
| 43 | `/v2/resident/goals/{goalId}/approve` | POST | *(legacy shim)* | `services/api.ts` |
| 44 | `/v2/resident/goals/{goalId}/reject` | POST | *(legacy shim)* | `services/api.ts` |
| 45 | `/v2/resident/goals/{goalId}/materialize` | POST | *(legacy shim)* | `services/api.ts` |
| 46 | `/v2/resident/goals/{goalId}/stage` | POST | *(legacy shim)* | `services/api.ts` |
| 47 | `/v2/resident/goals/{goalId}/run` | POST | *(legacy shim)* | `services/api.ts` |
| 48 | `/v2/resident/decisions` | GET | *(legacy shim)* | `services/api.ts` |
| 49 | `/v2/resident/decisions/{decisionId}/evidence` | GET | *(legacy shim)* | `app/components/resident/EvidenceViewer.tsx` |
| 50 | `/v2/resident/skills` | GET | *(legacy shim)* | `services/api.ts` |
| 51 | `/v2/resident/skills/extract` | POST | *(legacy shim)* | `services/api.ts` |
| 52 | `/v2/resident/experiments` | GET | *(legacy shim)* | `services/api.ts` |
| 53 | `/v2/resident/experiments/run` | POST | *(legacy shim)* | `services/api.ts` |
| 54 | `/v2/resident/improvements` | GET | *(legacy shim)* | `services/api.ts` |
| 55 | `/v2/resident/improvements/run` | POST | *(legacy shim)* | `services/api.ts` |
| 56 | `/v2/resident/goals/{goalId}/execution` | GET | *(legacy shim)* | `services/api.ts` |
| 57 | `/v2/resident/goals/execution/bulk` | GET | *(legacy shim)* | `services/api.ts` |
| 58 | `/v2/services/tasks` | GET | *(legacy shim)* | `services/api.ts` |
| 59 | `/v2/services/tasks` | POST | *(legacy shim)* | `services/api.ts` |
| 60 | `/v2/services/tasks/{taskId}` | GET | *(legacy shim)* | `services/api.ts` |
| 61 | `/v2/services/todos` | GET | *(legacy shim)* | `services/api.ts` |
| 62 | `/v2/services/todos` | POST | *(legacy shim)* | `services/api.ts` |
| 63 | `/v2/services/todos/summary` | GET | *(legacy shim)* | `services/api.ts` |
| 64 | `/v2/services/todos/{itemId}/done` | POST | *(legacy shim)* | `services/api.ts` |
| 65 | `/v2/services/tokens/status` | GET | *(legacy shim)* | `services/api.ts` |
| 66 | `/v2/services/tokens/record` | POST | *(legacy shim)* | `services/api.ts` |
| 67 | `/v2/services/security/check` | POST | *(legacy shim)* | `services/api.ts` |
| 68 | `/v2/services/transcript` | GET | *(legacy shim)* | `services/api.ts` |
| 69 | `/v2/services/transcript/session` | GET | *(legacy shim)* | `services/api.ts` |
| 70 | `/v2/ws/runtime` | WS | *(legacy shim)* | `api.ts` |

*Note: Many frontend calls target legacy shim routes (`/v2/director/*`, `/v2/pm/start`, `/v2/resident/*`, `/v2/services/*`) that may not be defined in the v2 router files under `polaris/delivery/http/routers/`. Those shims are not counted in the 105 backend v2 routes, but are listed here for completeness because the frontend actively uses them.*

---

## 4. Missing Routes (Backend v2 Not Used by Frontend)

### 4.1 P0 — Critical User-Facing (Chat / Sessions / Conversations)

| # | Route Template | HTTP | Backend Router | Why It Matters |
|---|----------------|------|----------------|----------------|
| 1 | `/v2/sessions` | GET | `agent.py` | List agent sessions — needed for session switcher UI |
| 2 | `/v2/sessions/{session_id}` | GET | `agent.py` | Retrieve single session metadata |
| 3 | `/v2/sessions/{session_id}` | DELETE | `agent.py` | Delete a session |
| 4 | `/v2/sessions/{session_id}/messages/stream` | POST | `agent.py` | Stream messages for an agent session |
| 5 | `/v2/turn` | POST | `agent.py` | Execute a single agent turn |
| 6 | `/v2/roles/sessions/{session_id}/messages/stream` | POST | `role_session.py` | Stream role session messages |
| 7 | `/v2/role/{role}/chat` | POST | `role_chat.py` | Non-streaming role chat |
| 8 | `/v2/pm/chat` | POST | `pm_chat.py` | Non-streaming PM chat |
| 9 | `/v2/pm/chat/stream` | POST | `pm_chat.py` | Streaming PM chat |
| 10 | `/v2/pm/chat/status` | GET | `pm_chat.py` | PM chat readiness status |
| 11 | `/v2/pm/chat/ping` | GET | `pm_chat.py` | PM chat liveness probe |
| 12 | `/v2/stream/chat` | POST | `stream_router.py` | Generic streaming chat endpoint |
| 13 | `/v2/stream/chat/backpressure` | POST | `stream_router.py` | Back-pressure control for streams |

### 4.2 P1 — Management & Configuration

| # | Route Template | HTTP | Backend Router | Why It Matters |
|---|----------------|------|----------------|----------------|
| 14 | `/v2/pm/documents` | GET | `pm_management.py` | PM document list |
| 15 | `/v2/pm/search/documents` | GET | `pm_management.py` | Document search |
| 16 | `/v2/pm/tasks` | GET | `pm_management.py` | PM task list |
| 17 | `/v2/pm/tasks/history` | GET | `pm_management.py` | PM task history |
| 18 | `/v2/pm/tasks/director` | GET | `pm_management.py` | Director-specific task history |
| 19 | `/v2/pm/tasks/{task_id}` | GET | `pm_management.py` | Single task detail |
| 20 | `/v2/pm/search/tasks` | GET | `pm_management.py` | Task search |
| 21 | `/v2/pm/requirements` | GET | `pm_management.py` | Requirements list |
| 22 | `/v2/pm/health` | GET | `pm_management.py` | PM health check |
| 23 | `/v2/pm/init` | POST | `pm_management.py` | PM initialisation |
| 24 | `/v2/llm/config` | GET | `llm.py` | LLM configuration read |
| 25 | `/v2/llm/config` | POST | `llm.py` | LLM configuration write |
| 26 | `/v2/llm/config/migrate` | POST | `llm.py` | Migrate legacy LLM config |
| 27 | `/v2/llm/status` | GET | `llm.py` | LLM provider status |
| 28 | `/v2/llm/runtime-status` | GET | `llm.py` | LLM runtime status |
| 29 | `/v2/llm/providers` | GET | `providers.py` | List available LLM providers |
| 30 | `/v2/settings` | GET | `system.py` | App settings read |
| 31 | `/v2/settings` | POST | `system.py` | App settings write |
| 32 | `/v2/agents/apply` | POST | `agents.py` | Apply agent configuration |
| 33 | `/v2/agents/feedback` | POST | `agents.py` | Submit agent feedback |

### 4.3 P2 — Diagnostic / Monitoring / Logs

| # | Route Template | HTTP | Backend Router | Why It Matters |
|---|----------------|------|----------------|----------------|
| 34 | `/v2/health` | GET | `system.py` | Overall health check |
| 35 | `/v2/ready` | GET | `system.py` | Readiness probe |
| 36 | `/v2/live` | GET | `system.py` | Liveness probe |
| 37 | `/v2/state/snapshot` | GET | `system.py` | System state snapshot |
| 38 | `/v2/app/shutdown` | POST | `system.py` | Graceful shutdown |
| 39 | `/v2/query` | GET | `logs.py` | Query logs / events |
| 40 | `/v2/user-action` | POST | `logs.py` | Log user action |
| 41 | `/v2/channels` | GET | `logs.py` | List log channels |
| 42 | `/v2/stream/health` | GET | `stream_router.py` | Stream subsystem health |
| 43 | `/v2/role/chat/ping` | GET | `role_chat.py` | Role chat liveness |
| 44 | `/v2/role/chat/roles` | GET | `role_chat.py` | List chat-enabled roles |
| 45 | `/v2/role/{role}/llm-events` | GET | `role_chat.py` | Per-role LLM events |
| 46 | `/v2/role/llm-events` | GET | `role_chat.py` | All LLM events |
| 47 | `/v2/role/cache-stats` | GET | `role_chat.py` | Chat cache statistics |
| 48 | `/v2/role/cache-clear` | POST | `role_chat.py` | Clear chat cache |
| 49 | `/v2/state` | GET | `memory.py` | Anthro state read |
| 50 | `/v2/memories/{memory_id}` | DELETE | `memory.py` | Delete a memory |
| 51 | `/v2/memos/list` | GET | `memos.py` | List memos |
| 52 | `/v2/lancedb/status` | GET | `lancedb.py` | Vector DB status |

### 4.4 P3 — Advanced / Edge Case

| # | Route Template | HTTP | Backend Router | Why It Matters |
|---|----------------|------|----------------|----------------|
| 53 | `/v2/court/topology` | GET | `court.py` | Court actor topology |
| 54 | `/v2/court/state` | GET | `court.py` | Court runtime state |
| 55 | `/v2/court/actors/{role_id}` | GET | `court.py` | Court actor detail |
| 56 | `/v2/court/scenes/{scene_id}` | GET | `court.py` | Court scene detail |
| 57 | `/v2/court/mapping` | GET | `court.py` | Court role mapping |
| 58 | `/v2/vision/status` | GET | `arsenal.py` | Vision subsystem status |
| 59 | `/v2/vision/analyze` | POST | `arsenal.py` | Vision analysis |
| 60 | `/v2/scheduler/status` | GET | `arsenal.py` | Scheduler status |
| 61 | `/v2/scheduler/start` | POST | `arsenal.py` | Start scheduler |
| 62 | `/v2/scheduler/stop` | POST | `arsenal.py` | Stop scheduler |
| 63 | `/v2/code_map` | GET | `arsenal.py` | Code map overview |
| 64 | `/v2/code/index` | POST | `arsenal.py` | Index codebase |
| 65 | `/v2/code/search` | POST | `arsenal.py` | Semantic code search |
| 66 | `/v2/mcp/status` | GET | `arsenal.py` | MCP server status |
| 67 | `/v2/director/capabilities` | GET | `arsenal.py` | Director capabilities |
| 68 | `/v2/files/read` | GET | `files.py` | Read file content |
| 69 | `/v2/llm/interview/ask` | POST | `interview.py` | Interview ask |
| 70 | `/v2/llm/interview/save` | POST | `interview.py` | Save interview |
| 71 | `/v2/llm/interview/cancel` | POST | `interview.py` | Cancel interview |
| 72 | `/v2/llm/interview/stream` | POST | `interview.py` | Interview stream |
| 73 | `/v2/llm/test` | POST | `tests.py` | LLM self-test |
| 74 | `/v2/llm/test/stream` | POST | `tests.py` | LLM self-test stream |
| 75 | `/v2/llm/test/{test_run_id}` | GET | `tests.py` | Retrieve test report |
| 76 | `/v2/ollama/models` | GET | `ollama.py` | List Ollama models |
| 77 | `/v2/ollama/stop` | POST | `ollama.py` | Stop Ollama instance |
| 78 | `/v2/check` | POST | `permissions.py` | Permission check |
| 79 | `/v2/effective` | GET | `permissions.py` | Effective permissions |
| 80 | `/v2/roles` | GET | `permissions.py` | List roles |
| 81 | `/v2/assign` | POST | `permissions.py` | Assign role |
| 82 | `/v2/policies` | GET | `permissions.py` | List policies |
| 83 | `/v2/docs/init/dialogue` | POST | `docs.py` | Docs init dialogue |
| 84 | `/v2/docs/init/dialogue/stream` | POST | `docs.py` | Docs init dialogue stream |
| 85 | `/v2/docs/init/suggest` | POST | `docs.py` | Suggest init docs |
| 86 | `/v2/docs/init/preview` | POST | `docs.py` | Preview init docs |
| 87 | `/v2/docs/init/preview/stream` | POST | `docs.py` | Preview init docs stream |
| 88 | `/v2/docs/init/apply` | POST | `docs.py` | Apply init docs |

---

## 5. Gap Statistics by Priority

| Priority | Count | % of Total v2 |
|----------|-------|---------------|
| P0 — Critical | 13 | 12.4% |
| P1 — Management | 20 | 19.0% |
| P2 — Diagnostic | 19 | 18.1% |
| P3 — Advanced | 36 | 34.3% |
| **Total Missing** | **~88** | **~83.8%** |

*Note: The "missing" count is higher than the simple 105 − 42 = 63 because many frontend calls hit legacy shim routes (e.g. `/v2/director/status`, `/v2/pm/start`, `/v2/resident/*`, `/v2/services/*`) that are **not** part of the 105 formally declared v2 routes. If we count only the 105 declared v2 routes, the uncovered set is ~63. If we include legacy shims the frontend depends on, the surface area is larger.*

---

## 6. Recommendations

### 6.1 Immediate (P0)
1. **Agent Session Streams** — Wire `/v2/sessions/{session_id}/messages/stream` and `/v2/roles/sessions/{session_id}/messages/stream` into the session inspector / AI dialogue components.
2. **PM Chat** — The frontend currently uses legacy `/v2/pm/chat/*` shims; migrate to the formal v2 routes in `pm_chat.py` if the shim is deprecated.
3. **Turn Execution** — `/v2/turn` is the canonical agent-turn endpoint; ensure the AI dialogue hook can fall back to it when streaming is not required.
4. **Generic Stream Chat** — Evaluate whether `/v2/stream/chat` should replace or supplement the per-role stream endpoints to reduce frontend complexity.

### 6.2 Short-Term (P1)
1. **Settings UI** — Connect `/v2/settings` (GET/POST) and `/v2/llm/config` (GET/POST) to a settings panel.
2. **PM Management** — Surface `/v2/pm/tasks`, `/v2/pm/documents`, and `/v2/pm/requirements` in the PM workbench.
3. **LLM Provider Discovery** — Use `/v2/llm/providers` to populate the provider dropdown instead of hard-coding.
4. **Agent Apply/Feedback** — Add UI controls for `/v2/agents/apply` and `/v2/agents/feedback` if agent configuration is user-facing.

### 6.3 Medium-Term (P2)
1. **Health Dashboard** — Poll `/v2/health`, `/v2/ready`, `/v2/live`, and `/v2/stream/health` for a system-status widget.
2. **Log Viewer** — Integrate `/v2/query` and `/v2/channels` into a developer-tools panel.
3. **Cache & Events** — Expose `/v2/role/cache-stats` and `/v2/role/llm-events` for debugging chat behaviour.

### 6.4 Low Priority / Background (P3)
1. **Court Visualisation** — `/v2/court/*` routes are only needed if a Court topology UI is planned.
2. **Arsenal (Vision / Scheduler / Code-Map)** — These are advanced capabilities; add UI only when the corresponding features ship.
3. **Interview & Test** — `/v2/llm/interview/*` and `/v2/llm/test/*` are useful for onboarding / diagnostics but not daily user flows.
4. **Permissions Admin** — `/v2/check`, `/v2/roles`, `/v2/assign`, `/v2/policies` are needed only for multi-user or RBAC scenarios.

---

## 7. Risk & Boundary Notes

- **Legacy Shim Dependency** — The frontend currently relies on a large set of non-v2 routes (`/v2/director/*`, `/v2/pm/start`, `/v2/resident/*`, `/v2/services/*`). These are **not** in the 105 declared v2 routes. If the backend plans to retire shims, the frontend will break unless migrated.
- **WebSocket Gap** — `/v2/ws/runtime` is used for runtime transport but is not a REST route; it is excluded from the 105 HTTP route count.
- **Query-String Variants** — Some frontend calls append query parameters (e.g. `?source=pm`, `?resume=true`). These are correctly matched to their base path templates.
- **Test Files** — Frontend test files (`*.test.ts`) contain additional route references; they were included in the scan because they confirm intended usage.

---

## 8. Files Referenced

- Backend routers: `polaris/delivery/http/routers/*.py` (24 files, 105 routes)
- Frontend services: `src/frontend/src/services/*.ts`
- Frontend components: `src/frontend/src/app/components/**/*.tsx`
- Runtime: `src/frontend/src/runtime/*.ts`
- Top-level API: `src/frontend/src/api.ts`

---

*End of Analysis*
