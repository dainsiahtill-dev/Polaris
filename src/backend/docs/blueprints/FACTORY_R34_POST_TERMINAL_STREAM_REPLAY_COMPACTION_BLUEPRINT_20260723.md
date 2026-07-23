# Factory R34: Post-Terminal Stream Replay Compaction

Status: closed; Bench remains `not_schedulable` pending pre-bench proof.

## Observed fact

R32 durably completed the Chief Engineer physical Provider attempt at
`2026-07-22T23:44:50.811670Z`. Replay then projected 10,088 reasoning events,
mostly 1-6 characters each, from `23:44:50.822139Z` through
`00:21:48.883745Z`. The role emitted neither `llm_call_end` nor
`llm_call_error` before the Factory deadline.

This is not a Provider, context-budget, role-identity, or tool-schema failure.
The physical response completed in about 125 seconds. The remaining 37 minutes
were local post-terminal replay amplification through role events, audit
journals, and runtime WebSocket projection.

## Architecture

```text
Provider SSE deltas
  -> FinalProviderAttemptGate buffers response
  -> durable provider_attempt.terminal
  -> StreamExecutor decodes buffered raw events
  -> Factory-only contiguous text coalescer
  -> Role stream events
  -> audit/runtime.v2 projections
  -> llm_call_end
```

## Contract

- Physical Provider buffering and durable terminal ownership remain unchanged.
- Coalescing applies only when a Factory physical-dispatch port is bound.
- Only adjacent text events of the same semantic kind (`chunk` or
  `reasoning_chunk`) may be combined.
- Text bytes and ordering must remain exact.
- Pending text must flush before tool calls, errors, kind changes, and stream
  completion.
- Tool-call envelopes, usage, terminal status, retry identity, and receipts
  must remain unchanged.
- Ungoverned/live streams retain current low-latency event granularity.
- No target-project code, Bench bypass, or Provider request is authorized.

## Module responsibilities

- `kernelone.llm.engine.stream.executor`: decode raw provider events and
  coalesce Factory post-terminal text replay before role projection.
- `roles.kernel`: consume the same semantic event contract; no alternate
  terminal source or fallback state is introduced.
- `FinalProviderAttemptGate`: remains sole physical-attempt terminal owner.

## Verification

1. Red regression reproduces thousands of tiny structured reasoning deltas
   behind a bound Factory port.
2. Replayed reasoning text is byte-identical and event count is bounded.
3. Text-kind/tool-call ordering remains exact.
4. Ungoverned stream granularity remains unchanged.
5. Existing stream executor, Role Kernel, and physical-attempt suites pass.
6. Ruff, format, mypy, compileall, and diff audit pass before schedulability is
   reconsidered.

## Closure evidence

- Regression before fix: 4,096 tiny reasoning deltas produced 4,096 role
  events; ordering regression also failed.
- Regression after fix: 4,096 deltas produced 4 byte-identical reasoning
  batches; text/tool ordering and ungoverned granularity passed.
- R32 evidence projects from 10,088 events / 40,822 characters to about 40
  batches at the 1,024-character boundary, a 252.2x cardinality reduction.
- Focused stream executor: 51 passed.
- KernelOne stream + Roles Kernel: 2,803 passed.
- Anthropic provider integration: 36 passed.
- Ruff, format, mypy, compileall, and targeted diff check passed.
