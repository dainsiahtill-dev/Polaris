# KernelOne Context OS Attention Runtime Improvement Plan

- Status: Proposed
- Date: 2026-03-27
- Scope: `polaris/kernelone/context/**`, `polaris/cells/roles/session/**`, `polaris/cells/roles/kernel/**`
- Trigger: follow-up confirmation drift observed in real multi-turn interaction

> This plan is a follow-on hardening track after `docs/KERNELONE_CONTEXT_OS_COGNITIVE_RUNTIME_HARDENING_PLAN_2026-03-27.md`.
> It focuses on conversational attention stability, not on replacing the current State-First Context OS architecture.
> It must remain consistent with `docs/cognitive_runtime_architecture.md`,
> `docs/KERNELONE_STATE_FIRST_CONTEXT_OS_BLUEPRINT_2026-03-26.md`, and `AGENTS.md`.

---

## 1. Problem Statement

The recent bug was not only a keyword classification failure.

It exposed a broader weakness in how the current runtime carries conversational intent across turns:

1. a short user reply such as `需要` can be semantically critical but textually small
2. assistant follow-up questions are not yet modeled as first-class pending actions
3. recent-turn protection is still partly shaped by history window heuristics
4. `working_state`, `run_card`, `active_window`, and continuity projection can drift in focus

The system therefore risks snapping back to an older task even when the newest exchange clearly redirected or confirmed the current action.

---

## 2. Objectives

This improvement line must guarantee:

1. the latest user intent cannot be silently overwritten by an older task
2. short confirmations, denials, pauses, and redirects are treated as high-value dialog acts, not low-signal text
3. active-window assembly and state patching agree on what the current focus is
4. episode sealing never closes history that still has unresolved conversational intent
5. the solution remains generic to multi-role / multi-domain agent work, while allowing stronger code-domain behavior through domain adapters

---

## 3. Architecture Position

This plan does **not** introduce a new truth owner.

The layering remains:

1. `roles.session`
   - raw conversation truth owner
2. `KernelOne State-First Context OS`
   - working-set assembly
   - state patching
   - artifact offload
   - episode sealing
   - retrieval and budget control
3. `Cognitive Runtime`
   - scope / validation / receipt / handoff authority

This plan strengthens layer 2.

More precisely:

- `Context OS` should evolve from a `working set manager` into a `working set manager + intent tracker + attention scheduler`
- `Cognitive Runtime` should consume the improved outputs, not re-implement the logic

---

## 4. Root Cause Decomposition

### 4.1 Intent Carryover Failure

Current extraction logic is still too content-local.
It can read the current message, but it does not consistently model the conversational dependency:

`assistant question -> user confirmation/denial -> state mutation`

### 4.2 Recent-Turn Protection Gap

The current runtime already pins a recent slice, but the policy is still too close to a tuned sliding window.
Critical latest turns need a stronger invariant:

`the newest conversational turns are roots, not optional recency filler`

### 4.3 Run-Card Semantics Are Too Thin

`run_card.current_goal` and `run_card.next_action_hint` are useful, but they do not yet explicitly represent:

1. latest user intent
2. pending assistant follow-up action
3. whether a follow-up has been confirmed, denied, paused, or redirected

### 4.4 Seal Timing Is Too Narrative-Oriented

An episode can be sealed because the closed-event bundle looks complete enough.
But conversationally it may still be open if the user just responded with:

- `需要`
- `不用`
- `先别`
- `改成另外一个`

### 4.5 Continuity Alignment Is Incomplete

`SessionContinuityEngine` now consumes Context OS projections, but it still needs stronger guarantees that prompt-facing continuity reflects the newest conversational decision, not only the strongest historical state.

---

## 5. Design Principles

### 5.1 Latest Intent Is a Root

The newest explicit user intent is a root object in prompt assembly.
It may be short, but it is never low priority.

### 5.2 Conversation Semantics Precede Compression

Dialog-act resolution must happen before low-signal clearing and before episode sealing.

### 5.3 Pending Follow-Up Must Be Typed State

Follow-up interactions cannot remain implicit metadata forever.
They need a proper typed object with lifecycle semantics.

### 5.4 Active Window Uses Liveness, Not Just Recency

The latest 2-3 messages are hard-pinned.
Beyond that, keep or evict by liveness and dependency reachability.

### 5.5 Generic First, Domain-Aware Second

The base solution should remain generic to:

- coder roles
- writer roles
- planner roles
- multi-role handoff scenarios

Code-domain strengthening should remain an adapter-layer enhancement, not the only runtime assumption.

---

## 6. Target Runtime Additions

### 6.1 Dialog Act Layer

Introduce a deterministic dialog-act classifier before routing compaction:

Suggested acts:

1. `affirm`
2. `deny`
3. `pause`
4. `redirect`
5. `clarify`
6. `commit`
7. `cancel`
8. `status_ack`
9. `noise`

This layer should be:

1. deterministic-first
2. metadata-emitting
3. domain-agnostic in the base implementation

Target area:

- `polaris/kernelone/context/context_os/runtime.py`
- `polaris/kernelone/context/context_os/domain_adapters/generic.py`

### 6.2 Pending Follow-Up State Object

Add a first-class state object:

```text
pending_followup:
  action: str
  source_event_id: str
  source_sequence: int
  status: pending|confirmed|denied|paused|redirected|expired
  updated_at: str
```

Rules:

1. assistant may open a pending follow-up
2. the next user turn may resolve it
3. unresolved pending follow-up prevents premature sealing
4. resolved follow-up patches `task_state` and `run_card`

### 6.3 Run Card v2

Extend `RunCard` to explicitly expose:

1. `latest_user_intent`
2. `pending_followup_action`
3. `pending_followup_status`
4. `last_turn_outcome`

This avoids overloading `current_goal` and `next_action_hint` with all conversational semantics.

### 6.4 Active Window Root Policy v2

Current root set should be expanded to:

1. latest user turn
2. latest assistant turn
3. latest `N` recent turns, default `3`
4. current goal
5. unresolved open loops
6. pending follow-up
7. active artifacts
8. recent committed decisions

This is a mark-and-sweep attention policy, not a pure sliding window.

### 6.5 Episode Seal Guard

Do not seal if any of the following is true:

1. `pending_followup.status == pending`
2. latest user intent is unresolved
3. latest 3 turns still belong to the same open conversational dependency
4. a redirect/cancel/deny turn has not yet patched state

### 6.6 Continuity Projection Alignment

`SessionContinuityEngine` should explicitly consume:

1. `latest_user_intent`
2. `pending_followup`
3. the latest open-loop resolution result

Continuity output must not regress to an older focus when Context OS has already recognized a newer conversational decision.

### 6.7 Attention Observability

Add debug-facing structured fields:

1. `intent_classification`
2. `pending_followup`
3. `attention_roots`
4. `forced_recent_sequences`
5. `seal_blockers`
6. `focus_resolution_path`

This must appear in final debug receipts, not as duplicated intermediate spam.

---

## 7. Generic vs Code Domain

This work must remain generic at the core.

### 7.1 Generic Core

Belongs in the generic runtime:

1. dialog-act recognition
2. follow-up lifecycle
3. recent-turn pinning
4. active-window root policy
5. run-card attention semantics
6. seal blocking rules
7. continuity alignment

### 7.2 Code-Domain Enhancement

Belongs in code-domain adapters only:

1. recognizing file/symbol-oriented follow-up actions
2. stronger promotion of code-fix / test / patch / read-file intents
3. code-specific artifact weighting
4. code search / patch workflow hints

This preserves the long-term requirement that Polaris support not only coding, but also writing, planning, and other multi-role creation flows.

---

## 8. Execution Phases

### A1: Dialog Act Baseline

Outputs:

1. deterministic dialog-act classification
2. metadata propagation on transcript events
3. tests for `affirm/deny/pause/redirect`

Acceptance:

1. short replies no longer disappear into low-signal routing

### A2: Pending Follow-Up First-Class State

Outputs:

1. state model for pending follow-up
2. lifecycle transitions
3. prompt-facing run-card projection

Acceptance:

1. assistant question + user short reply reliably updates current focus

### A3: Active Window Root Hardening

Outputs:

1. hard-pinned recent-turn floor
2. pending-followup root promotion
3. active/excluded mutual exclusion checks

Acceptance:

1. latest 2-3 messages are always preserved unless explicitly invalid

### A4: Seal Guard + Continuity Convergence

Outputs:

1. episode sealing guardrails
2. continuity projection alignment
3. regression tests for reopen / redirect / deny flows

Acceptance:

1. history does not close around unresolved conversational state

### A5: Evaluation + Rollout Gate

Outputs:

1. attention-specific metrics
2. benchmark fixtures
3. rollout thresholds

Acceptance:

1. attention stability becomes measurable and promotion-gated

---

## 9. Suggested Metrics

Add metrics beyond token and compaction quality:

1. `intent_carryover_accuracy`
2. `latest_turn_retention_rate`
3. `focus_regression_rate`
4. `false_clear_rate`
5. `pending_followup_resolution_rate`
6. `seal_while_pending_rate`
7. `continuity_focus_alignment_rate`

---

## 10. Code Touch Map

Primary implementation areas:

1. `polaris/kernelone/context/context_os/models.py`
2. `polaris/kernelone/context/context_os/runtime.py`
3. `polaris/kernelone/context/context_os/domain_adapters/generic.py`
4. `polaris/kernelone/context/context_os/domain_adapters/code.py`
5. `polaris/kernelone/context/session_continuity.py`
6. `polaris/kernelone/context/context_os/evaluation.py`
7. `polaris/kernelone/context/tests/**`

Possible downstream consumers:

1. `polaris/cells/roles/session/**`
2. `polaris/cells/roles/kernel/**`
3. `polaris/kernelone/context/chunks/**`

---

## 11. Rollout Strategy

Introduce feature switches so this line can be hardened safely:

1. `context_os_enable_dialog_act`
2. `context_os_enable_pending_followup_state`
3. `context_os_min_recent_messages_pinned`
4. `context_os_prevent_seal_on_pending_followup`
5. `context_os_debug_attention_trace`

Recommended default path:

1. enable `dialog_act`
2. enable `pending_followup_state`
3. keep recent pin floor at `3`
4. enable seal prevention on pending follow-up
5. gate stronger rollout by metrics

---

## 12. Definition of Done

This line is complete only when:

1. the newest user intent cannot be silently replaced by an older task
2. assistant follow-up questions create typed pending state
3. short confirmations and denials are modeled as intent, not noise
4. active-window roots and continuity projections agree on current focus
5. episode sealing respects unresolved conversation state
6. regression tests cover real multi-turn conversational carryover
7. rollout is metrics-gated, not confidence-gated

---

## 13. Final Position

The correct long-term direction is:

`Context OS = working set manager + intent tracker + attention scheduler`

not merely:

`Context OS = summary / compaction layer`

That is the minimum architecture needed if Polaris is going to support robust multi-role, multi-turn, cross-domain agent work instead of only surviving isolated code-edit prompts.
