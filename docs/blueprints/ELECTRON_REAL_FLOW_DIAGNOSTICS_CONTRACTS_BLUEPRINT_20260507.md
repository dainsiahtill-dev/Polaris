# Electron Real Flow, Runtime Diagnostics, and Role Contracts Blueprint

## 1. Current Understanding

- Polaris desktop needs a real LLM Electron full-chain gate that is explicit, seeded, and auditable.
- Runtime troubleshooting currently requires scattered logs for NATS, WebSocket reconnect, and rate limit behavior.
- PM, Chief Engineer, and Director task/blueprint fields currently cross several API and projection layers, so shared contract drift must be constrained.

## 2. Evidence List

- Read `package.json`, `infrastructure/scripts/run-electron-real-flow-e2e.mjs`, `.github/workflows/electron-real-flow-nightly.yml`, and Electron fixtures.
- Read NATS lifecycle code in `server_runtime.py`, `client.py`, and `ws_consumer_manager.py`.
- Read rate limit middleware in `delivery/http/middleware/rate_limit.py`.
- Read Director and Chief Engineer v2 route models, runtime projection, and frontend runtime hooks/components.
- Subagent E2E audit confirmed real-flow is opt-in by design and must fail when real settings are absent in nightly.

## 3. Defect Analysis

- Real-flow nightly evidence is incomplete if runtime artifacts stay only in temp/runtime roots, and entire home upload would risk leaking `config/settings.json`.
- Runtime health signals exist as low-level implementation details but are not exposed as a single desktop diagnostic surface.
- Role task contracts have duplicated Pydantic and TypeScript shapes; fields like `pm_task_id`, `blueprint_id`, guardrails, status, and evidence can drift.

## 4. Fix Plan

- Harden `run-electron-real-flow-e2e.mjs` and nightly workflow with seed preflight, no host settings in CI, explicit artifacts, and retention.
- Add a read-only runtime diagnostics v2 endpoint for NATS lifecycle, WebSocket runtime status, and rate-limit configuration.
- Add a desktop diagnostics panel entry that uses the new endpoint and existing websocket state.
- Introduce shared role contract models in backend and matching frontend types; wire Director/Chief Engineer API models through the shared backend contract.

## 5. Test Plan

- Happy path: real-flow runner dry-run with seeded UTF-8 settings; diagnostics endpoint returns structured sections; UI renders diagnostics.
- Edge cases: missing real settings in runner; missing nats-server; no rate-limit env overrides; empty role contract fields.
- Exception cases: invalid JSON seed; invalid blueprint/task values; diagnostics API survives NATS unavailable.
- Regression cases: existing Director, CE, LLM, runtime parsing, Electron visual tests remain green.

## 6. Rollback Plan

- Revert this blueprint and changed files in:
  - `.github/workflows/electron-real-flow-nightly.yml`
  - `infrastructure/scripts/run-electron-real-flow-e2e.mjs`
  - runtime diagnostics backend/frontend files
  - shared contract backend/frontend files
- Confirm rollback by rerunning targeted pytest, vitest, typecheck, and Electron smoke.
