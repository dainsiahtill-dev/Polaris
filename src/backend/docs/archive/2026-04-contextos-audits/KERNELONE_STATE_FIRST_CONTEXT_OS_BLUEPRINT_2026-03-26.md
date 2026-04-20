# KernelOne State-First Context OS Blueprint

- Status: Implemented Core / Living Blueprint
- Date: 2026-03-26
- Scope: `polaris/kernelone/context/**`

> This document is a technical blueprint, not graph truth.
> It refines the working-memory runtime missing between `Cell IR` and the future `Cognitive Runtime`.

> Implementation note as of 2026-03-26:
> the core runtime described here has been landed under `polaris/kernelone/context/context_os/`
> and consumed through `SessionContinuityEngine` as a compatibility facade.
> `run_card` / `context_slice_plan` are now also surfaced through continuity consumers
> and prompt/debug observability in `roles.kernel` and `delivery.cli`.

---

## 1. Core Judgment

Polaris needs a context operating system, not another summary helper.

The defining principle is:

`Preserve immutable truth, rebuild working views, and only compact closed derived context.`

This blueprint therefore rejects the following patterns:

1. replacing old history with one opaque summary
2. treating transcript and prompt-working-set as the same thing
3. keeping large tool/file/search payloads inline with prompt history
4. using fixed token cliffs as the main lifecycle boundary

---

## 2. Position in the Architecture

The intended stack is:

```text
Cell IR / Graph Truth
  -> long-term boundary truth

KernelOne State-First Context OS
  -> working-memory runtime

Cognitive Runtime
  -> cross-role authority / lease / validate / handoff / promote

Role Runtime / Director / PM / QA / Resident consumers
  -> domain and workflow execution
```

Rules:

1. `context.engine` remains the public Context Plane facade
2. `roles.session` remains raw session owner
3. `resident.autonomy` remains long-term identity and goal owner
4. `Context OS` owns working-set assembly and memory projection
5. `Cognitive Runtime` may consume Context OS but may not replace it

---

## 2.1 Domain Split

The runtime should not assume that all future workloads are code.

The correct split is:

1. generic runtime kernel
2. domain adapters
3. role overlays

### Generic runtime kernel

These remain domain-agnostic:

1. transcript events
2. artifact records
3. state entries and supersedes semantics
4. episode cards
5. run cards
6. context slice plan
7. budget planning
8. liveness GC
9. prompt composition order

### Code-domain adapter

These are code-domain specific and should be isolated from the core:

1. file paths
2. symbol names
3. repo-map evidence
4. dependency-graph reachability
5. diff / patch / test-failure semantics
6. file excerpt previews
7. code-tool output shaping

### Future non-code adapters

Examples:

1. `document`
   - section / citation / outline / source-note references
2. `fiction`
   - scene / character / arc / chapter / draft references
3. `research`
   - claim / paper / quote / experiment / dataset references

### Practical decision for now

Phase 1 should still be validated primarily in the `code` domain, because the current Polaris pressure is there.

But the kernel contracts must be written so that:

1. code-specific extraction rules can be replaced
2. future adapters can plug into retrieval and artifact preview without rewriting the core runtime

---

## 3. First-Class Runtime Objects

### 3.1 TranscriptEvent

Immutable event in append-only order.

```json
{
  "event_id": "evt_001",
  "turn_id": "t41",
  "kind": "user_turn",
  "role": "user",
  "content": "...",
  "artifacts": [],
  "metadata": {},
  "created_at": "2026-03-26T12:00:00Z"
}
```

Kinds should include:

1. `user_turn`
2. `assistant_turn`
3. `tool_call`
4. `tool_result`
5. `file_reference`
6. `retrieval_result`
7. `state_patch`
8. `episode_sealed`

### 3.2 ArtifactRecord

Typed offloaded payload with preview and restore path.

```json
{
  "artifact_id": "art_029",
  "type": "tool_result",
  "mime": "application/json",
  "token_estimate": 8240,
  "preview": "web search results about context editing",
  "restore_method": "read_artifact",
  "keys": ["items", "next_page"],
  "source_turns": ["t41"]
}
```

### 3.3 StateEntry

Versioned state patch.

```json
{
  "entry_id": "fact_17",
  "path": "task_state.current_goal",
  "value": "stabilize context runtime",
  "source_turns": ["t41", "t42"],
  "confidence": 0.97,
  "updated_at": "2026-03-26T12:00:00Z",
  "supersedes": "fact_12"
}
```

### 3.4 DecisionEntry

Decision ledger item.

```json
{
  "decision_id": "dec_04",
  "kind": "accepted_plan",
  "value": "Context OS first, Cognitive Runtime second",
  "source_turns": ["t42"],
  "updated_at": "2026-03-26T12:05:00Z",
  "supersedes": ""
}
```

### 3.5 EpisodeCard

Closed-history memory unit.

```json
{
  "episode_id": "ep_12",
  "intent": "redesign context engine",
  "outcome": "shifted from summary-first to state-first",
  "digest_64": "...",
  "digest_256": "...",
  "narrative_1k": "...",
  "artifact_refs": ["art_029"],
  "source_spans": ["t31:t38"],
  "reopen_conditions": ["user asks to revisit provider-native compaction"]
}
```

### 3.6 RunCard

The smallest always-fresh working-memory object.

```json
{
  "current_goal": "finish context runtime blueprint",
  "hard_constraints": [
    "do not replace context.engine",
    "do not move truth ownership out of roles.session"
  ],
  "open_loops": [
    "write implementation plan",
    "write blueprint"
  ],
  "active_entities": [
    "context.engine",
    "roles.session",
    "resident.autonomy",
    "cognitive_runtime"
  ],
  "active_artifacts": [],
  "recent_decisions": [
    "Context OS precedes Cognitive Runtime"
  ],
  "next_action_hint": "publish blueprint and update boundary doc"
}
```

### 3.7 ContextSlicePlan

Explains prompt composition.

```json
{
  "plan_id": "slice_101",
  "budget_tokens": 24000,
  "roots": ["latest_user_turn", "current_goal", "hard_constraints", "open_loops"],
  "included": [
    {"type": "state", "ref": "task_state.current_goal", "reason": "root"},
    {"type": "artifact", "ref": "art_029", "reason": "active_artifact"},
    {"type": "episode", "ref": "ep_12", "reason": "semantic recall"}
  ],
  "excluded": [
    {"ref": "evt_003", "reason": "low_signal"},
    {"ref": "art_011", "reason": "closed_and_unreachable"}
  ]
}
```

---

## 4. Layer Model

### 4.1 Truth Layer

Responsibilities:

1. append all new events to immutable transcript
2. never destructively summarize the truth source
3. keep stable ids for events, artifacts, and episodes

### 4.2 Routing Layer

Each new item is classified into one of four lanes:

1. `clear`
   - drop noise or replace with tiny stub
2. `patch`
   - extract structured state deltas
3. `archive`
   - offload large exact payloads to artifact store
4. `summarize`
   - create narrative digest only when truly needed

Only the narrative lane should depend on learned summarization.

### 4.3 Working State Layer

Suggested top-level schema:

```json
{
  "user_profile": {
    "preferences": [],
    "style": [],
    "persistent_facts": []
  },
  "task_state": {
    "current_goal": "",
    "accepted_plan": [],
    "open_loops": [],
    "blocked_on": [],
    "deliverables": []
  },
  "decision_log": [],
  "active_entities": [],
  "active_artifacts": [],
  "temporal_facts": []
}
```

### 4.4 Artifact Store

Offload targets include:

1. large tool results
2. long file excerpts
3. search result sets
4. big JSON / HTML / XML / Markdown blocks
5. tables and extracted documents

Prompt view keeps only artifact stubs.

### 4.5 Episode Store

Seal history at semantic closure boundaries:

1. tool-use cycle closed
2. subtask completed
3. topic shift
4. accepted decision
5. code change closed-loop

### 4.6 Hybrid Memory Index

Retrieval should blend:

1. semantic similarity
2. lexical / sparse match
3. entity and time filters
4. dependency or graph reachability
5. recency

In the generic kernel, these are abstract signals.
In the `code` domain, they currently mean:

1. semantic similarity over engineering conversation and repo descriptors
2. lexical match over file names, symbols, errors, test names, and task terms
3. entity/time match over files, modules, symbols, tasks, and temporal updates
4. dependency reachability over repo intelligence / code graph
5. recency over recent engineering work

For non-code domains, only the scoring frame should remain.
The concrete entity model must come from the domain adapter.

Suggested score:

```text
score =
0.35 * semantic
+ 0.20 * lexical
+ 0.20 * entity_time_match
+ 0.15 * dependency_reachability
+ 0.10 * recency
```

### 4.7 Liveness GC

Always pin:

1. system / tool schema prefix
2. latest user turn
3. current goal
4. hard constraints
5. open loops
6. active artifacts
7. recent accepted decisions
8. unclosed tool cycles

Eligible for eviction:

1. consumed old tool results
2. closed episodes
3. stale assistant explanations
4. unreachable historical turns

### 4.8 Budget Controller

Given provider context size `C`:

```text
output_reserve = min(max_expected_output, 0.18C)
tool_reserve = 0.10C
safety_margin = max(2048, 0.05C)
input_budget = C - output_reserve - tool_reserve - safety_margin
```

Trigger bands:

1. normal
   - canonicalize + patch state only
2. soft pressure
   - clear stale tool results + evict closed reachable content
3. hard pressure
   - compact only closed history
4. emergency
   - static prefix + run card + state json + retrieved memory + latest raw turns only

---

## 5. Prompt Composition Contract

Canonical order:

```text
[Static cacheable prefix]
1. system prompt
2. tool schemas
3. safety and formatting policy
4. durable user profile

[Dynamic working set]
5. head anchor / run card
6. structured task state
7. recalled memories / source spans
8. active raw turns
9. tail anchor
10. latest user message
```

This order must satisfy two goals:

1. exact-prefix caching friendliness
2. attention bias toward the beginning and end of the prompt

---

## 6. Runtime Flow

The per-turn lifecycle should be:

```python
def build_context(new_items, session, model_ctx):
    raw_log.append(new_items)

    routed = [canonicalize_and_offload(x) for x in new_items]

    state_deltas = extract_state_deltas(routed)
    working_state.apply_patches(state_deltas)

    if episode_closed(routed, working_state):
        episode_store.seal(...)

    budget = budget_controller.plan(...)

    live_set = liveness_gc.collect(...)

    if token_count(live_set) > budget.soft_limit:
        live_set = clear_and_evict_closed_content(live_set)

    recalled = retriever.hybrid_search(...)

    prompt = compose(
        static_prefix=...,
        head_anchor=run_card,
        task_state_json=...,
        recalled_memories=recalled,
        active_window=live_set,
        tail_anchor=...,
        latest_user=...
    )

    if token_count(prompt) > budget.hard_limit:
        prompt = compact_closed_history_only(prompt, working_state)

    return prompt
```

The key rule is:

`Never replace the truth source. Only rebuild the next working view.`

---

## 7. Public Kernel-Level Operations

The Context OS should expose these runtime operations internally:

1. `project(...)`
   - build prompt-facing projection from raw session truth
2. `search_memory(query, type?, entity?, time_range?)`
3. `read_artifact(artifact_id, span?)`
4. `read_episode(episode_id)`
5. `get_state(path?)`

These are not public product APIs yet.
They are kernel-level runtime operations for role and future cognitive consumers.

---

## 8. Compatibility Rules

### 8.1 With `SessionContinuityEngine`

`SessionContinuityEngine` should become a projection adapter over Context OS, not a rival subsystem.

That means:

1. preserve compatibility where needed
2. change internals from summary-first to state-first
3. keep continuity pack as a derived asset

### 8.2 With `context.engine`

`context.engine` remains the public facade.

It should:

1. query Context OS
2. receive a context projection
3. not own transcript truth or episode truth directly

### 8.3 With `roles.session`

`roles.session` continues to own:

1. raw session rows
2. raw message rows
3. session-level override config

Context OS consumes these inputs and emits projections.

### 8.4 With `resident.autonomy`

Resident owns:

1. identity
2. agenda
3. goals
4. learning / improvement records

Context OS must not introduce a second resident-style long-term mind.

### 8.5 With `Cognitive Runtime`

`Cognitive Runtime` should consume:

1. `RunCard`
2. `ContextSlicePlan`
3. `RuntimeReceipt`
4. handoff packs derived from Context OS

It should not create its own independent working-memory substrate.

---

## 8.6 With code-domain infrastructure

The `code` domain should initially consume:

1. `repo_intelligence`
2. `working_set`
3. canonical read tools
4. code-oriented history materialization rules

But those are consumers and enrichers, not the core runtime itself.

The boundary must stay:

1. `Context OS` owns memory mechanics
2. `code` adapter owns code evidence semantics

This keeps later `writer` / `research` / `document` adapters possible without kernel rewrites.

---

## 9. Phase-1 Implementation Slice

The smallest valid implementation slice is:

1. complete `context_os/models.py`
2. complete `context_os/runtime.py`
3. refit `session_continuity.py` as Context OS projection
4. add artifact stub support to history materialization
5. add unit and integration tests

Phase 1 does not require:

1. a new graph cell
2. a new public delivery endpoint
3. a production-wired `Cognitive Runtime`

---

## 10. Exit Criteria

This blueprint is considered successfully implemented when:

1. transcript truth is immutable
2. state is patch-based and versioned
3. large payloads are offloaded with restore paths
4. closed history can be sealed into episodes
5. active window is liveness-based
6. budget planning is explicit and provider-aware
7. prompt assembly is explainable by `ContextSlicePlan`
8. continuity is a projection, not destructive summary replacement

---

## 11. One-Sentence Summary

`Cell IR` gives Polaris long-term truth organization, `State-First Context OS` gives it working-memory mechanics, and `Cognitive Runtime` gives it cross-role execution authority.
