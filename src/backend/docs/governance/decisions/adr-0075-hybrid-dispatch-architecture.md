# ADR-0075: Hybrid Dispatch Architecture for Polaris v2.0

**Status**: Approved  
**Date**: 2026-04-16  
**Author**: Architecture Team  
**Deciders**: Polaris Core Governance Committee  

---

## Context

Polaris v2.0 introduces a multi-agent pipeline composed of PM, Chief Engineer (CE), Director, and QA. Historically, these roles were either fully decoupled (loose message passing) or tightly coupled (direct method invocation). Both extremes created problems:

- **Full decoupling** caused high latency, duplicate work, and poor observability when CE and Director needed to share intermediate state.
- **Full tight coupling** made scaling individual roles impossible and created cascading failures when one role stalled.

We needed a dispatch model that matches coupling strength to the actual collaboration pattern of each handoff.

---

## Decision

Adopt a **hybrid dispatch architecture** with three distinct coupling modes:

### 1. PM → Chief Engineer: Loose Coupling via Task Market

- PM publishes high-level goals as **tasks** onto a shared Task Market.
- CE consumes tasks asynchronously, performs technical analysis, and produces blueprints or constraints.
- There is no direct RPC; all coordination is event-driven and durable.

**Rationale**: PM and CE operate on different time scales. PM plans in minutes; CE analyzes in seconds to minutes. Loose coupling prevents PM blocking on CE backlog.

### 2. CE → Director: Tight Coupling via DirectorPool

- CE submits a prepared **execution plan** directly to a **DirectorPool** (managed worker pool).
- The pool assigns the plan to an idle Director instance and awaits completion.
- Synchronous or semi-synchronous call with timeout and circuit breaker.

**Rationale**: CE output is only valuable if it is executed faithfully and immediately. Any delay or loss here creates orphan blueprints. Tight coupling ensures atomic handoff and rapid feedback.

### 3. Director → QA: Loose Coupling via Task Market

- Director publishes completed work units back to the **Task Market** as verification tasks.
- QA picks up tasks asynchronously, runs quality gates, and emits pass/fail events.
- Results are eventually consistent; QA may batch or prioritize tasks.

**Rationale**: QA is a scaling bottleneck by design. Decoupling Director from QA allows QA to scale horizontally without stalling the execution pipeline.

---

## Consequences

### Positive

- **Observability**: Each coupling mode has a single integration surface (Task Market API or DirectorPool RPC), making tracing and metrics straightforward.
- **Conflict Prevention**: Loose boundaries prevent back-pressure from QA or CE from stalling PM planning. Tight CE→Director boundary prevents plan drift.
- **Scalability**: DirectorPool can be sized to match compute capacity; Task Market consumers (CE, QA) can scale independently based on queue depth.

### Negative

- **Operational Complexity**: Two distinct transport patterns must be maintained, monitored, and secured.
- **Failure Mode Duality**: Loose paths require idempotency and retry logic; tight paths require timeout and circuit-breaker tuning.

### Mitigations

- Task Market tasks are versioned and idempotent by design.
- DirectorPool calls are wrapped with a 60-second default timeout and exponential backoff retry.
- All handoffs emit structured telemetry (`dispatch_mode`, `handoff_latency_ms`, `queue_depth`) to a unified observability backend.

---

## Related Decisions

- ADR-0074: Multi-Layer Dead Loop Prevention Architecture
- ADR-0076: ContextOS 2.0 摘要策略选型

---

## Conclusion

Approve the hybrid dispatch model: **PM→CE loose, CE→Director tight, Director→QA loose**. Update runtime configuration and deployment topology to reflect these three coupling zones.
