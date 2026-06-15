# ADR-0094: Runtime Effect Sinks and Reliable Facts

Status: Accepted

Date: 2026-06-15

## Context

The 2026-06-15 production-stability audit found that several hot paths trusted upstream checks too much:

- ContextOS and LLM executors could still send provider-bound requests with no real output headroom.
- ExecutionBroker and AgentAccel file writes were practical effect sinks without uniform sink-layer policy evidence.
- TaskMarket lifecycle receipts could be emitted before the TaskMarket CAS commit.
- JetStream WebSocket consumers ACKed messages that were dropped under backpressure.

These are structural risks because a single upstream bypass can create prompt overflows, unguarded host effects, orphan receipts, or unrecoverable event loss.

## Decision

1. Provider-bound prompt assembly is fail-closed. If prompt + wrapper overhead + requested output cannot fit in the resolved model window, Polaris must reject before calling the provider.
2. Execution, file, and log-write sinks enforce policy at the sink. Upstream checks remain useful, but they are not authoritative.
3. Cross-cell receipts and facts occur after the owning state commit. They are emitted through deterministic, idempotent outbox records.
4. Reliable event consumers never ACK messages they did not deliver or intentionally filter. Backpressure must produce redelivery or explicit resync.
5. Governance gates must protect the above invariants; allowlists for structural reverse dependencies are temporary and must shrink.

## Consequences

- Some previously best-effort flows now return explicit budget or authorization errors.
- Public contracts may gain optional provenance fields for compatibility, but production sink checks can require those fields for high-risk effects.
- TaskMarket and fact-stream replay becomes safer because idempotency keys are part of the durable event contract.
- WebSocket clients must handle `RESYNC_REQUIRED` as a normal recovery signal.

## Verification

- Focused ContextOS/LLM budget tests.
- ExecutionBroker and tool-executor sink authorization tests.
- TaskMarket CAS/outbox/fact idempotency tests.
- JetStream queue-full redelivery/resync tests.
- KernelOne release and catalog governance gates.
