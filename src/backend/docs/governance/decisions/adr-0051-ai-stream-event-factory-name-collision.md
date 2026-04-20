# ADR-0051: AIStreamEvent Factory Name Collision Hardening

Date: 2026-03-26

## Status

Accepted

## Context

Runtime stream logs intermittently crashed with:

`TypeError: object of type 'method' has no len()`

Crash point was `StreamExecutor.invoke_stream` when emitting debug payload lengths.
Root cause is structural:

1. `AIStreamEvent` dataclass defined fields `chunk` and `reasoning`.
2. The same class also defined classmethods `chunk()` and `reasoning()`.
3. Dataclass default resolution and class attribute overwrite created callable leakage risk for default field values in non-text events (`COMPLETE`, `ERROR`).
4. Stream debug path assumed text-like values and executed `len(...)` directly.

This combination made runtime fragile and produced provider-dependent failures.

## Decision

1. Rename event factories to collision-safe names:
   - `AIStreamEvent.chunk_event(...)`
   - `AIStreamEvent.reasoning_event(...)`
2. Replace all internal call sites that used old names.
3. Add defensive length helper in stream executor:
   - `_safe_text_length(value)` only counts `str|bytes`, otherwise `0`.
4. Add regression tests to lock contract and runtime guard behavior.

## Consequences

Positive:

1. Removes dataclass field/method naming collision at source.
2. Stream debug instrumentation becomes non-fatal for unexpected value types.
3. Future refactors can rely on explicit, unambiguous event factory names.

Trade-offs:

1. Internal API rename requires synchronized call-site migration.
2. External consumers using deprecated names must migrate to new factories.

## Verification

1. `pytest polaris/kernelone/llm/toolkit/tests/test_stream_event_contract.py -q`
2. `pytest polaris/kernelone/llm/toolkit/tests/test_llm_convergence.py -q`
3. `python -c "from polaris.kernelone.llm.engine.stream_executor import StreamExecutor; from polaris.cells.llm.evaluation.internal.runner import EvaluationRunner; print('ok')"`

## Follow-up

1. If any external plugin still references `AIStreamEvent.chunk()`/`reasoning()`, provide a migration note in release docs.
2. Add static lint rule to block dataclass field and factory name collisions in core runtime contracts.
