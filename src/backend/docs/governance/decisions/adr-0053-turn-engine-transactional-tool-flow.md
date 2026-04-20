# ADR-0053: TurnEngine Transactional Tool Flow

Date: 2026-03-26

## Status

Accepted

## Context

`TurnEngine` currently mixes:

1. visible stream rendering
2. reasoning/thinking handling
3. tool-call discovery
4. tool execution
5. continuation loop orchestration

This creates a structural ambiguity:

1. a tool intent can appear in native provider events
2. the same intent can appear again in textual wrappers
3. the same turn can be reparsed after stream completion

As a result, the system struggles to answer three core questions cleanly:

1. Can thinking trigger tools?
2. Should tool execution automatically trigger another LLM call?
3. Where should async or multi-step exploration actually live?

## Decision

Adopt a transactional TurnEngine model.

### Decision rules

1. `thinking` is never executable.
2. A turn has a single executable commit point: `TurnDecision`.
3. Tool execution is performed as one explicit batch, not as an implicit continuation trigger.
4. After tools finish, the default is **no additional LLM request**.
5. If summarization is still needed, allow at most one explicit finalization request:
   - `finalize_mode=llm_once`
   - `tool_choice=none`
   - any further tool request is a protocol violation
6. Multi-step exploration, async waiting, and repeated file-reading loops move to workflow/runtime orchestration above TurnEngine.
7. Domain policy affects default finalization strategy:
   - `document` roles prefer `final_answer` or `tool_batch + llm_once`
   - `code` roles prefer `tool_batch + none/local`
   - any deeper read-analyze-read chain becomes `handoff_workflow`, not a hidden turn-internal loop

## Consequences

Positive:

1. Removes ambiguity around whether thinking or intermediate text can execute tools.
2. Gives the system one authoritative execution source per turn.
3. Separates single-turn transactions from multi-step agent workflows.
4. Makes stream/run parity simpler because both modes share the same turn state machine.
5. Reduces accidental “tool once in reasoning, tool once in final output” duplication.

Trade-offs:

1. Some current transcript-driven continuation behavior must move out of `TurnEngine`.
2. Workflow/runtime responsibilities become more explicit and must absorb exploration logic.
3. A new canonical contract set is required (`TurnDecision`, `ToolBatchPlan`, `BatchReceipt`, `FinalizationPolicy`).
4. CLI/telemetry must show `decision -> tool_batch -> optional finalization` as explicit phases so users can distinguish one valid synthesis call from an unintended hidden retry.

## Verification

Architecture acceptance criteria:

1. A single turn never authorizes tool execution from both thinking and visible output.
2. A single turn never executes tools from both native and textual channels as independent sources.
3. Tool completion does not automatically imply another LLM request.
4. If `finalize_mode=llm_once` is used, that request cannot issue tools.
5. Async tool waiting is represented as workflow handoff rather than turn-internal blocking.

## Follow-up

1. Introduce typed turn contracts and state machine tests before refactoring runtime code.
2. Migrate transcript-driven continuation semantics into workflow/runtime orchestration.
3. Keep current graph truth unchanged until implementation phases land.
