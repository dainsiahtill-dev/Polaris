# Explicit startup readiness; preserve never-started failure evidence

Status: accepted for implementation under approved unattended master plan;
not fresh-project acceptance.

Context: r08 failed during managed NATS readiness and then crashed while
reporting to unenrolled storage. Consumer-count explanations were falsified by
a 2.931s native copied-store recovery. The eight-second standalone deadline,
living-child readiness shortcut and no-attempt classification are explicit
implementation defects; the precise historical restore delay is unresolved.

Decision: existing infrastructure adapter consumes explicit bounded startup
policy and distinguishes protocol readiness, child exit, deadline and cancel.
Existing public FactStream maintenance initializes harness runtime storage
before launch. Never-started launch errors are not terminal executions and do
not run real verifiers. No weakening of KFS, authorization or delivery gates.

Boundary: NATS remains infrastructure; FactStream state remains its Cell's.
No new Cell, graph truth, event rail or target-business implementation.
Graph/descriptor changes are not needed for these existing interfaces.

Consequence: negative startup evidence can be durable without claiming run
success. Shared NATS lifetime/cross-process coordination remain separate open
architectural work, and current source must still pass fresh isolated proof.
