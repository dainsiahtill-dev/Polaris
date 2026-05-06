# Runtime Readiness Diagnostic Architecture Blueprint

Date: 2026-05-06
Status: Implemented guardrails, acceptance gate pending real settings
Scope: Electron startup, backend readiness, HTTP probes, rate-limit/auth bootstrap policy, runtime.v2 WebSocket, Electron E2E diagnostics

## 1. Current Understanding

Polaris Desktop has four runtime readiness surfaces:

1. Electron main starts Python backend and exposes `window.polaris.getBackendInfo`.
2. Backend exposes legacy public probes `/health`, `/ready`, `/live` and authenticated v2/system endpoints.
3. Renderer uses IPC-provided `baseUrl` and token to call backend APIs.
4. Playwright Electron tests launch Electron through a direct fixture rather than `npm run dev`.

The recent incident was not a single defect. It was a contract drift pattern:

- `backend_started` stdout was treated as readiness although Uvicorn was not listening yet.
- `/health` returned a payload that did not match its `response_model`.
- E2E launch env did not inherit the local loopback rate-limit exemption.
- WebSocket/NATS cleanup was not idempotent for missing consumers.
- E2E locators depended on language text instead of stable test ids.

## 2. Evidence Inventory

Read and traced:

- `src/electron/main.cjs`
- `infrastructure/scripts/run-dev.js`
- `infrastructure/scripts/wait-and-run-electron.js`
- `src/backend/polaris/delivery/http/app_factory.py`
- `src/backend/polaris/delivery/http/routers/primary.py`
- `src/backend/polaris/delivery/http/routers/system.py`
- `src/backend/polaris/delivery/http/middleware/rate_limit.py`
- `src/backend/polaris/delivery/http/routers/_shared.py`
- `src/backend/polaris/delivery/http/dependencies.py`
- `src/backend/polaris/delivery/ws/endpoints/protocol.py`
- `src/backend/polaris/delivery/ws/endpoints/websocket_loop.py`
- `src/backend/polaris/infrastructure/messaging/nats/ws_consumer_manager.py`
- `src/backend/polaris/tests/electron/*`
- `playwright.electron.config.ts`

Confirmed risks:

- `GET /health` was still registered twice in the full FastAPI app: public `primary.health_check` and authenticated `system.health`.
- `/ready` and `/v2/ready` have divergent readiness semantics.
- Rate-limit policy only excluded `/health` and `/metrics`; bootstrap-critical authenticated endpoints rely on env loopback exemption.
- There are two `require_auth` implementations with different failure and context behavior.
- Runtime v2 `SUBSCRIBE` could replace the current `JetStreamConsumerManager` without disconnecting the previous one.
- Client-provided `client_id` was used directly in NATS durable names, enabling cross-connection durable collisions.
- Playwright default suite can pass while real PM/Director acceptance tests are skipped.
- E2E diagnostics are split across fixture, Playwright trace, and individual specs.

Unconfirmed or intentionally deferred:

- Whether `/v2/ready` should return HTTP 503 when dependency readiness fails. Current v2 behavior remains HTTP 200 with `ready=false` for compatibility, but the readiness checks now reuse the primary readiness contract.
- Whether `/settings` and `/runtime/storage-layout` should be globally rate-limit exempt or only loopback-exempt.
- Whether all legacy non-v2 system endpoints should remain long-term.

## 3. Defect Analysis

### 3.1 Readiness Signal Drift

Symptom: Electron renderer fetched backend APIs before backend was actually ready.

Root cause: stdout lifecycle event, HTTP probe response model, and renderer IPC readiness were separate implicit contracts.

Impact: flaky startup, misleading `getBackendInfo`, hard-to-reproduce `Failed to fetch`.

Architecture rule: only HTTP health can prove backend readiness for renderer fetches. Stdout events are diagnostic evidence, not readiness gates.

### 3.2 Probe Ownership Drift

Symptom: `/health` had two owners and two response shapes.

Root cause: legacy and system routers both registered the same path, and tests often mounted routers in isolation.

Impact: route behavior depended on registration order; tests could verify a route that the real app never reaches.

Architecture rule: `(method, path)` must have one owner in the full app. Enhanced system health belongs to `/v2/health`; public process probe belongs to `/health`.

### 3.3 Bootstrap Policy Drift

Symptom: localhost bootstrap calls were rate-limited during Electron startup.

Root cause: rate-limit policy was per-IP and not aware of bootstrap-critical endpoints; E2E and dev launch paths set env differently.

Impact: `/settings`, `/memos/list`, `/llm/status`, and `/files/read` could return 429 during local startup.

Architecture rule: endpoint access policy must be canonical and shared by auth, rate-limit, metrics, logging, and tests.

Proposed policy classes:

- `public_probe`: `/health`, `/ready`, `/live`; no auth. `/ready` and `/live` remain visible to logging, trace, metrics, and rate-limit headers for diagnostics.
- `low_signal`: `/health`, `/health/*`, `/metrics`, `/metrics/*`, `/favicon.ico`; excluded from detailed observability and normal rate buckets.
- `auth_probe`: `/v2/health`, `/v2/ready`, `/v2/live`; auth required and diagnostic-visible.
- `auth_bootstrap`: `/settings`, `/runtime/storage-layout`, startup file reads; auth required; loopback exempt in desktop/dev/E2E.
- `auth_action`: normal user/API actions; auth and rate-limit enabled.
- `stream_runtime`: WebSocket/SSE; auth/token required; own backpressure and reconnect policy.

### 3.4 Runtime v2 Consumer Drift

Symptom: WebSocket failed with `consumer not found`; additional audit found subscribe lifecycle risks.

Root cause: JetStream consumer lifecycle was distributed across protocol handling and manager cleanup. `client_id` was both client identity and durable ownership key.

Impact: duplicate subscribe can leak consumers; repeated client IDs can collide across windows/tabs.

Architecture rule: server-side connection identity owns NATS durable names. Client ID is display/resume metadata, not durable ownership.

### 3.5 E2E Acceptance Drift

Symptom: `npm run test:e2e` passed while real PM/Director full-chain tests were skipped.

Root cause: smoke tests and acceptance tests share one suite output but not one acceptance contract.

Impact: "E2E passed" can be misread as "product flow accepted".

Architecture rule:

- `smoke`: app launches and core panels render.
- `runtime`: backend/WS/probe/diagnostic contracts.
- `acceptance`: real PM/Director/QA flow; skipped acceptance is not PASS.

## 4. Stable Architecture Proposal

### 4.1 Canonical Readiness Contract

Electron startup must follow:

```text
spawn backend
  -> publish diagnostic state: starting
  -> wait HTTP /health 200
  -> publish diagnostic state: running, ready=true
  -> create renderer window
  -> renderer calls getBackendInfo
```

`backend_started` stdout remains useful, but only as a timeline marker.

### 4.2 Route Ownership Gate

Every full app route must satisfy:

```text
(method, path) has exactly one owner
owner module matches namespace:
  /health,/ready,/live -> primary router
  /v2/* -> v2/system router until v2 modules finish migration
```

Add a route uniqueness test so future duplicate routes fail before runtime.

### 4.3 Diagnostic Artifact Contract

Every Electron E2E failure should attach or write:

- Electron main stdout/stderr tail
- `desktop-backend.json`
- `getBackendStatus` payload
- backend `/health` result
- renderer console/pageerror summary
- failed request summary
- screenshot and Playwright trace

### 4.4 Runtime v2 Consumer Contract

WebSocket runtime v2 must satisfy:

- A new `SUBSCRIBE` on the same connection first disconnects the old manager.
- NATS durable names include server-side connection identity.
- NATS `APIError` is classified, not allowed to crash ASGI.
- Send cursor advances only after `send_json_safe` succeeds.
- Client ACK remains the authority for durable ack cursor.

### 4.5 Acceptance Language

Reports must distinguish:

- `PASS`: required gate executed and passed.
- `SKIPPED`: intentionally not executed; never counted as accepted.
- `SMOKE_PASS`: basic app health only.
- `ACCEPTANCE_PASS`: PM/Director/QA full chain completed with required reason.

## 5. Immediate Fix Plan

Implemented guardrails:

1. Remove the duplicate non-v2 `system /health`; keep public `/health` in `primary` and enhanced health in `/v2/health`.
2. Add a full-app route uniqueness guard test.
3. Make `desktop-backend.json` publication atomic.
4. Make runtime v2 repeated `SUBSCRIBE` disconnect the previous consumer first.
5. Include server-side connection identity in NATS durable ownership.
6. Catch NATS `APIError` in consumer connect/cleanup paths.
7. Advance WebSocket v2 send cursor only after successful send.
8. Add canonical endpoint policy shared by rate-limit, metrics, logging, and audit context.
9. Merge router `_shared.require_auth` onto the canonical dependency implementation so auth context and fail-closed behavior are consistent.
10. Split Playwright scripts into smoke/runtime/acceptance; acceptance is fail-closed unless `KERNELONE_E2E_USE_REAL_SETTINGS=1`.
11. Reuse primary readiness checks in `/v2/ready` while preserving v2's HTTP 200 compatibility behavior.

Deferred:

- Unified E2E failure artifact writer.
- Optional migration of `/v2/ready` from HTTP 200 `ready=false` to readiness-style HTTP 503.

## 6. Test Plan

Happy path:

- `npm run test:e2e`
- `/health`, `/ready`, `/live`, `/v2/health`, `/v2/ready`, `/v2/live` focused tests.

Edge cases:

- Full app duplicate route detection.
- Repeated runtime.v2 `SUBSCRIBE` disconnects old manager.
- NATS missing consumer cleanup is idempotent.
- NATS `APIError` does not crash ASGI.

Exception cases:

- `/ready` with NATS required but disconnected returns structured 503.
- WebSocket send failure does not advance v2 cursor.

Regression cases:

- Electron `/settings` fetch after startup remains stable.
- Playwright locators use stable test ids where available.

## 7. Rollback Plan

Rollback files for this batch:

- `src/electron/main.cjs`
- `src/backend/polaris/delivery/http/routers/system.py`
- `src/backend/polaris/delivery/ws/endpoints/protocol.py`
- `src/backend/polaris/delivery/ws/endpoints/websocket_loop.py`
- `src/backend/polaris/infrastructure/messaging/nats/ws_consumer_manager.py`
- route and WS tests added in `src/backend/polaris/tests/**`
- this blueprint and governance records

Rollback method:

```bash
git restore -- <file>
```

After rollback, rerun Electron E2E and the focused backend tests because route ownership and WS lifecycle behavior are externally observable.
