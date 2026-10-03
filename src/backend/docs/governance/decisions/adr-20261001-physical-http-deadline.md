# ADR: One invocation deadline across logical wait and physical HTTP

Status: accepted by user2026-10-01 for direct implementation under the master plan.

The role's async wait expired at660s while synchronous requests continued to
852s. Idle read timeouts cannot enforce a total budget, and cancelling an await
cannot kill a worker. Increasing the timeout or falsely settling a live request
would not solve the architectural boundary.

Decision: KernelOne owns an ephemeral monotonic invocation budget/cancel signal;
the existing provider helper owns bounded aiohttp I/O behind its sync-compatible
surface. Native projection and wire transport must identify the same actual
client. Existing physical gate remains sole attempt/terminal authority. Budget
controls never mint grants or create a second receipt ledger. Nested calls only
narrow deadlines; caller cancellation prevents subsequent retries.

Consequences: Provider response and retry consumers remain compatible. Tests
must inject at the actual raw-I/O seam rather than obsolete requests.post.
Explicit proxy/CA behavior is preserved, unsupported proxy schemes reject.
Local cancellation says nothing about remote compute/billing. Output-budget
feasibility, late-result durable continuation and full Bench completion remain
separate requirements; this ADR must not be used to claim those are done.
