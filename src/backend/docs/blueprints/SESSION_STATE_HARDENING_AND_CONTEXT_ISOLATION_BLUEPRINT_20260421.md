# Session Orchestrator Reliability Blueprint (2026-04-21)

**Status**: Implemented and targeted regressions passed  
**Scope**: `roles.runtime` + `roles.kernel` session orchestration reliability hardening  
**Primary Files**: `polaris/cells/roles/runtime/internal/session_orchestrator.py`, `polaris/cells/roles/runtime/internal/continuation_policy.py`, `polaris/cells/roles/kernel/internal/transaction/phase_manager.py`

## 1. Objective

This blueprint hardens `RoleSessionOrchestrator` from a patch-accumulated multi-turn loop into a deterministic session runtime with:

1. canonical turn history as the only session-level execution fact source;
2. a single envelope adapter between kernel completion events and orchestrator control flow;
3. single-terminal semantics for waiting, handoff, failure, and completion;
4. atomic checkpoint persistence with recoverable schema evolution;
5. `PhaseManager` promoted from ad hoc replay helper to session-level progression authority.

The implementation must preserve the external orchestrator contract while removing internal state drift, duplicated completion events, prompt-memory inconsistencies, and dead-code phase reconstruction.

## 2. Problem Statement

Current code exhibits structural reliability gaps:

- `CompletionEvent -> TurnOutcomeEnvelope` mapping is incomplete and inconsistent, especially around `failure_class`, `artifacts_to_persist`, and `speculative_hints`.
- `execute_stream()` can emit conflicting terminal signals, for example `SessionWaitingHumanEvent` followed by a trailing `SessionCompletedEvent`.
- `turn_history` is not canonical; downstream code attempts to replay phase from fields that are never persisted.
- checkpoint persistence is non-atomic and schema drift is already visible in tests.
- phase progression mixes string heuristics, local mutations, and partial `PhaseManager` replay instead of consuming authoritative tool-side effects.
- session state mutations are scattered across the loop, making invariants and recovery difficult to audit.

## 3. Architecture

### 3.1 Text Architecture Diagram

```text
User / Host
  -> RoleSessionOrchestrator
     -> CompletionEnvelopeAdapter
     -> SessionStateReducer
        -> SessionInvariantGuard
        -> PhaseManager (session-scoped)
        -> CanonicalTurnHistory
     -> ContinuationPolicy
     -> SessionCheckpointStore
     -> SessionArtifactStore
     -> TurnTransactionController
        -> Tool execution / receipts / completion events
```

### 3.2 Module Responsibilities

#### `session_orchestrator.py`

Owns orchestration only:

- bootstrap one session loop;
- translate kernel stream into session control flow;
- call reducer with canonical turn records;
- persist checkpoints and derived findings;
- manage terminal event semantics and resource cleanup.

It must stop directly mutating scattered state fields outside reducer-owned entrypoints.

#### `continuation_policy.py`

Owns continuation decisions only:

- hard-stop conditions (`contract_violation`, policy stop, durability failure, max-turns, repeated failure);
- soft-stop / recovery conditions (`stagnation`, low-value speculation);
- deterministic response contract: `(allow_continue, stop_reason)`.

It must consume canonical turn history and reducer state, not infer from prompt text.

#### `phase_manager.py`

Owns session phase progression only:

- derive phase from successful tool-side facts;
- serialize / deserialize phase state into checkpoint;
- reject invalid backward transitions;
- support timeout detection for a phase held too long.

It must not depend on string matching over prompts or free-form model explanations.

#### `SessionStateReducer` (new, in `session_orchestrator.py` initially)

Owns all state mutation:

- apply canonical turn record;
- update `goal`, `original_goal`, `delivery_mode`, `task_progress`, `structured_findings`, `read_files`, `last_failure`, `turn_history`, and `phase_manager`;
- centralize `continue_multi_turn` / final-answer guardrails;
- expose a stable snapshot for prompt projection and checkpoint persistence.

#### `SessionCheckpointStore` (implemented inside orchestrator first)

Owns checkpoint I/O only:

- write `tmp -> flush -> replace` atomically;
- load schema v2/v3 with migration shims;
- reject corrupt payloads without poisoning active state.

## 4. Core Data Flow

### Turn execution

1. Host sends prompt into orchestrator.
2. Orchestrator builds prompt projection from reducer snapshot.
3. Kernel emits stream events and eventually a `CompletionEvent`.
4. `CompletionEnvelopeAdapter` converts that event into a fully-populated `TurnOutcomeEnvelope`.
5. Orchestrator builds a canonical turn record from envelope + receipts + stop reason.
6. `SessionStateReducer.apply_turn_outcome(...)` updates the session snapshot and advances `PhaseManager` from real tool results.
7. `ContinuationPolicy` decides whether to continue, wait, hand off, or stop.
8. Checkpoint store atomically persists session state.
9. Orchestrator emits exactly one terminal session event for the path taken.

### Phase progression

1. Tool receipts are normalized into `ToolResult` objects.
2. `PhaseManager.transition(...)` consumes those tool results.
3. Reducer mirrors the authoritative phase into session-visible `task_progress`.
4. Prompt projection reads reducer phase, not ad hoc replay logic.

### Recovery / resume

1. Startup loads checkpoint payload.
2. Checkpoint migration reconstructs reducer snapshot and `PhaseManager` state.
3. Orchestrator resumes from canonical session state, not from synthesized prompt text.

## 5. Technical Decisions

| Decision | Reason |
|----------|--------|
| canonical turn history first | all later controls, prompt projection, and phase replay depend on one authoritative history shape |
| reducer-centered state updates | removes scattered mutations and makes invariants testable |
| `PhaseManager` inside reducer | phase becomes fact-driven and session-scoped instead of prompt-driven |
| atomic checkpoint writes | prevents torn session state and enables safe resume |
| single envelope adapter | isolates kernel/orchestrator contract drift to one place |
| terminal event single-exit | prevents contradictory session end states |

## 6. Implementation Plan

### Day 1: Stabilization

1. define canonical `turn_history` record and retrofit all turn writes;
2. harden `CompletionEvent -> TurnOutcomeEnvelope` adapter with full defaults;
3. fix terminal semantics in `execute_stream()`;
4. add top-level `try/except/finally` for checkpoint + cleanup;
5. replace raw checkpoint overwrite with atomic persistence.

### Day 2: State Model Upgrade

1. introduce `SessionStateReducer`;
2. route all session mutations through reducer;
3. move `PhaseManager` into reducer-owned state;
4. replace write-tool / progression heuristics with reducer + phase-driven logic.

### Day 3: Verification and Governance Alignment

1. update tests to canonical turn history / reducer semantics;
2. align `context.pack.json` and runtime context assets with actual ownership and dependencies;
3. run Ruff, format, mypy, and targeted pytest suites;
4. record assumptions and evidence in verification assets.

## 7. Validation Strategy

Targeted suites:

- `polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py`
- `polaris/cells/roles/runtime/internal/tests/test_continuation_policy.py`
- `polaris/cells/roles/runtime/internal/tests/test_session_artifact_store.py`

Additional coverage required:

- single terminal event for each session exit mode;
- incomplete completion-event fields do not crash adapter or policy;
- checkpoint corruption and schema migration;
- `PhaseManager` replay from canonical turn history;
- reducer-driven `MATERIALIZE_CHANGES` / exploration guardrails.

## 8. Risks and Boundaries

- The reducer introduction is structural but should remain external-behavior preserving.
- `PhaseManager` promotion must not invent a second truth source; persisted phase stays derived from canonical turn history and tool receipts.
- `context.pack.json` currently does not reflect actual runtime ownership and must be treated as stale governance data until updated.
- Existing compatibility paths in other hosts are out of scope unless directly broken by orchestrator changes.

## 9. Deliverables

1. updated blueprint in `docs/blueprints/SESSION_STATE_HARDENING_AND_CONTEXT_ISOLATION_BLUEPRINT_20260421.md`;
2. verification card for this structural fix;
3. orchestrator / continuation policy / phase manager code changes;
4. updated runtime context asset;
5. green quality-gate evidence for the targeted scope.

## 10. Verification Outcome

Executed and passed:

- `python -m pytest -q polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py polaris/cells/roles/runtime/internal/tests/test_continuation_policy.py polaris/cells/roles/runtime/internal/tests/test_session_artifact_store.py`
- `python -m pytest -q polaris/cells/roles/runtime/tests/test_host_session_continuity.py`
- `python -m pytest -q polaris/delivery/cli/director/tests/test_console_host_orchestrator.py`
- `python -m pytest -q polaris/delivery/cli/director/tests/test_orchestrator_e2e_integration.py`
- `python -m ruff check polaris/cells/roles/runtime/internal/session_orchestrator.py polaris/cells/roles/runtime/internal/continuation_policy.py polaris/cells/roles/runtime/internal/session_artifact_store.py polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py --fix`
- `python -m ruff format polaris/cells/roles/runtime/internal/session_orchestrator.py polaris/cells/roles/runtime/internal/continuation_policy.py polaris/cells/roles/runtime/internal/session_artifact_store.py polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py`
- `python -m mypy polaris/cells/roles/runtime/internal/session_orchestrator.py polaris/cells/roles/runtime/internal/continuation_policy.py polaris/cells/roles/runtime/internal/session_artifact_store.py polaris/cells/roles/kernel/internal/transaction/phase_manager.py`
