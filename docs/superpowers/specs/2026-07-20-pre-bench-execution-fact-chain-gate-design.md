# Pre-Bench Execution Fact Chain Gate Design

**Status:** locked before execution

## Goal

Authorize exactly one fresh isolated `factory_bench` only after the current
Polaris source snapshot proves this complete, auditable execution chain:

`Provider Request -> Tool Lifecycle -> Effect Receipt -> TaskBoundary ->`
`TaskRuntime -> Run Ledger -> QA -> Bench Report`.

The gate is platform-only. It never modifies a generated target project and it
does not treat unit tests, documentation, or a role step marked resolved as an
end-to-end success.

## Required proofs

1. DEO-1A through DEO-4 remain closed; the canonical mutation port is the sole
   physical effect consumer and TaskRuntime remains the sole durable effect
   authority.
2. B3.4-B3.6 are physically wired. Exact production `PreparedLLMRequest`
   instances qualify their final provider-visible request and pass one live,
   non-serializable dispatch port to every sync, structured, fallback, retry,
   SDK and stream transport. Legacy/malformed requests remain fail-closed.
3. PM, Architect, Chief Engineer, Director and QA final provider requests are
   independently auditable. Each call must prove correct role identity,
   messages, tools, tool choice, response format, semantic/native token counts,
   window utilization, required evidence coverage, and a same-workspace
   readable 24-hex `context_snapshot_ref` through both context endpoints.
4. Factory settlement, lock authority, fresh workspace identity, Instance
   Registry, isolated backend/frontend ports, runtime WebSocket projection,
   receipt settlement, Run Ledger and QA projections pass their non-Provider
   gates.
5. The source snapshot is frozen by HEAD plus a UTF-8 deterministic scoped diff
   hash. A source change during the run makes the result stale and unusable.

## Scheduling decision

`BENCH_SCHEDULABLE` may change from `false` to `true` only when all static,
focused, broad, startup and evidence-readability gates pass on one frozen
snapshot. This change authorizes one sequential isolated run only. It does not
authorize shared-backend workspace switching, ports `49977/5173`, parallel
bench runs, target-project edits, or a second attempt without another gate
evaluation.

## Acceptance

The bench closes only with a fresh non-stale report whose terminal state is
`COMPLETED_VERIFIED`, whose generated code is on disk, whose environment or
dependencies are usable, whose real build/test/lint gate ran, whose CLI/Web/API
entrypoint ran, and whose role-by-role final provider request snapshots are
readable and valid. Any failure reopens pre-bench remediation; blind rerun is
forbidden.
