# Director local repair triage parity

Owner: factory.pipeline. Pattern fix; no public/state/effect boundary change.

Exact factory_8edf12521449 QA rounds2-4 have stage
runtime_plan_probe_unplannable, owned repair targets, no new tools/provider
attempt, and no caller retry authorization. Dynamic replay of these actual
summaries returns triage=false because owned targets short-circuit the stage.
The adapter correctly requires explicit Director interface-discrepancy retry
authorization. Ordinary LLM fallback therefore repeats the same pre-call block.

Flow: recognized unplannable stage; existing Factory triage reconstructs current
owner/interface evidence; existing policy authorizes same-Director retry only
when permitted; adapter consumes that authorization; effect receipt and verifier
prove progress. No CE restart, no automatic grant from a target list, no relaxed
quality/stagnation/deadline policy.

Ruling: explicit already-authorized retry may bypass triage; an explicit
unplannable stage must be routed through triage even when files are named.
Ordinary owned diagnostics without that stage retain their local fallback.
Missing-contract/foreign-owner routes stay denied. Test both named and unnamed
targets through the actual Factory quality-loop controller, plus the pure
decision boundary. Existing Rust test's old false verdict is changed only for
the unauthed unplannable case; local authorized retry remains false-for-triage.

Pre-mortem: stage precedence might send ordinary diagnostics to CE, erase owner
evidence on cache hit, or turn a file name into authority. Verify current owner
and interface evidence reach the existing authorization branch on every cached
round; no threshold/budget increase or generated-target edit. Fresh acceptance
remains separate from controlled recovery.
