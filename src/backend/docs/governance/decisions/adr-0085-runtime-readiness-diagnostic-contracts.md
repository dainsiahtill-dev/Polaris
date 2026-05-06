# ADR-0085: Runtime Readiness And Diagnostic Contracts

Date: 2026-05-06
Status: Accepted for incremental rollout

## Context

Recent Electron startup failures exposed a structural pattern rather than a single defect:

- stdout lifecycle events were treated as backend readiness;
- HTTP probe response models drifted from actual payloads;
- route ownership allowed duplicate `GET /health`;
- local E2E launch did not inherit dev-runner bootstrap environment;
- runtime.v2 JetStream consumer cleanup was not fully idempotent;
- E2E smoke pass could be confused with full PM/Director acceptance.

These failures were difficult to diagnose because readiness, routing, rate-limit, WebSocket, and Playwright diagnostics each encoded a separate implicit contract.

## Decision

Polaris runtime readiness follows these contracts:

1. Electron renderer readiness is gated by backend HTTP `/health` success, not backend stdout events.
2. Public process probes are owned by `primary`:
   - `GET /health`
   - `GET /ready`
   - `GET /live`
3. Enhanced authenticated system probes are versioned:
   - `GET /v2/health`
   - `GET /v2/ready`
   - `GET /v2/live`
4. The full FastAPI app must not register duplicate `(method, path)` pairs.
5. runtime.v2 JetStream durable identity is server-owned and must include the server connection identity.
6. A repeated runtime.v2 `SUBSCRIBE` on the same WebSocket connection must disconnect the previous consumer manager first.
7. E2E result language must distinguish smoke pass, skipped acceptance, and acceptance pass.
8. HTTP endpoint policy is centralized so rate-limit, logging, metrics, and audit context classify probes and bootstrap endpoints consistently.
9. Router `_shared.require_auth` is a compatibility export of the canonical HTTP dependency implementation.
10. `/v2/ready` reuses primary readiness checks and adds v2-only checks while retaining v2's HTTP 200 `ready=false` compatibility behavior.

## Consequences

Positive:

- Startup failures become diagnosable by a single readiness chain.
- Duplicate route ownership fails in tests instead of depending on registration order.
- WebSocket consumer cleanup becomes safer across reconnects and repeated subscriptions.
- E2E reports stop overstating product acceptance when real-flow tests are skipped.
- Probe and bootstrap behavior is easier to diagnose because policy classes are testable in one place.

Tradeoffs:

- Legacy non-v2 enhanced health semantics move to `/v2/health`.
- Real-flow acceptance requires explicit environment setup and cannot be inferred from default smoke tests.
- `/v2/ready` still returns HTTP 200 when not ready; changing it to HTTP 503 is a future compatibility decision.

## Verification

See `src/backend/docs/governance/templates/verification-cards/vc-20260506-runtime-readiness-diagnostic-contracts.yaml`.
