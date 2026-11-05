# Engineering QA observation liveness

Observed L1-01 completed Director and passed checks but remained before QA admission. Host profile includes roughly85000SDKfiles; repeated synchronous current-candidate scans block the event loop. A source-bound gated observation reproduced heartbeat starvation. Exact live stack is unavailable due ptrace policy; no lock-cycle claim.

Existing roles.runtime EngineeringRunService remains the only owner. Offload finalization current-result/current-verification and QA admission/completion binding observations to the existing KernelOne bounded IO pool, preserving ContextVars. Each call executes a fresh observation; no manifest cache across awaits or queries. Cancellation must join the owned readonly observation before releasing its caller. Keep all input hashes, permissions, deadline identity, QA checks and original failure evidence.

Tests first: gated real QA entry must permit an event-loop callback; observation context propagates; cancellation cannot abandon pending IO. Run existing current-candidate, QA and event-selection regressions. No target edits or service restart implied by this source change. Catalog/state/effect ownership and public contracts remain unchanged. No new Cell, cache, controller or ledger; descriptors unaffected by internal scheduling helper.

Time complexity remains O(input bytes plus paths) per observation; no performance/throughput acceptance claim from heartbeat tests. Wide optional SDK grants and duplicate observation queries remain measured follow-up concerns. Historical reads must remain fresh, including after run deadlines.

Cancellation now reads bound run identity/state only before signaling its owner;
it no longer traverses candidate trees to stop effects. Resume still obtains a
fresh observation, off the event loop. Existing visual/per-tool synchronous
observation boundaries remain a separate limitation, not claimed solved here.

Verification:3new tests RED/GREEN,107targeted tests pass, Ruff/format and strict
mypy pass. Independent reviewer confirmed cancellation draining and context
isolation. A stale native capture test also failed on exact HEAD: corrected
its capture-scoped retry sequence to[1,2] while retaining UNKNOWN physical send
counts and real local HTTP request assertions. No physical-count gate weakened.
