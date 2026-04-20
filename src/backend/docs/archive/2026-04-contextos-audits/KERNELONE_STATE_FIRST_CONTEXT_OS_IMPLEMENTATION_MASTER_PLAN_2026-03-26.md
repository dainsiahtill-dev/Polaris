# KernelOne State-First Context OS Implementation Master Plan

- Status: Completed (Phase 0-6 Landed, Switchable + Default On)
- Date: 2026-03-26
- Scope: `polaris/kernelone/context/`, `polaris/cells/roles/session/`, `polaris/cells/roles/runtime/`, `polaris/cells/context/engine/`, `polaris/delivery/cli/`, `polaris/docs/`

> This document is an implementation plan, not graph truth.
> Current truth still follows `AGENTS.md`, `docs/graph/**`, `docs/FINAL_SPEC.md`, and cell manifests.
> This plan does not create a second context engine, a second session owner, or a second resident mind.

> Implementation note as of 2026-03-26:
> `context_os/` object model, artifact offload, working state, episode sealing, budget planning,
> liveness-based active window, `RunCard`, `ContextSlicePlan`, explainable retrieval,
> quality evaluation harness, `SessionContinuityEngine` projection compatibility,
> `roles.session` restore tools, HTTP restore consumers (`agent` / `role_session`),
> `context.engine` public-service overlay consumption,
> and CLI/runtime consumers have been landed in code and covered by tests.
> Unified runtime switches are now wired and default-on:
> `POLARIS_CONTEXT_OS_ENABLED` (default: enabled) and
> `POLARIS_COGNITIVE_RUNTIME_MODE` (default: `shadow`).
> Remaining phases in this plan: none.
> Post-landing hardening work is tracked separately in:
> `docs/KERNELONE_CONTEXT_OS_COGNITIVE_RUNTIME_HARDENING_PLAN_2026-03-27.md`.

---

## 1. Decision

Polaris should upgrade context management into a `State-First Context OS` under `KernelOne`.

The target split is:

1. `Cell IR / Graph truth`
   - owns long-term architectural truth, legal boundaries, contracts, projection, and back-mapping
2. `KernelOne State-First Context OS`
   - owns working-set assembly, state patches, artifact offload, episode sealing, retrieval, and token budget control
3. `Cognitive Runtime`
   - consumes the Context OS and adds cross-role authority such as scope lease, change-set validation, handoff, and promotion / rejection

This means:

1. `context.engine` remains the public Context Plane facade
2. `roles.session` remains the owner of raw session / message truth
3. `resident.autonomy` remains the owner of long-lived identity / agenda / goal truth
4. `KernelOne` becomes the owner of runtime context operating mechanics
5. `Cognitive Runtime` becomes a future authority layer on top of that operating substrate

---

## 2. Problem Statement

The current continuity stack solves only part of the problem:

1. it can generate deterministic continuity projections
2. it can trim recent windows
3. it can suppress some low-signal chatter

But it still lacks the stronger runtime model needed for stable long conversations:

1. no immutable truth-layer transcript abstraction
2. no typed routing of new content into noise / state / evidence / narrative
3. no structured working-state object with version semantics
4. no typed artifact offload / restore path
5. no episode sealing based on subtask closure
6. no liveness-based active window manager
7. no budget controller that plans for input, retrieval, tools, and output together
8. no hybrid retrieval contract across semantic, lexical, entity/time, and dependency signals

The result is that continuity quality still depends too much on ad-hoc prompt assembly and message truncation.

---

## 3. Hard Architectural Constraints

The rollout must obey these rules:

1. Do not replace `context.engine` as the public entrypoint.
2. Do not move session source-of-truth out of `roles.session`.
3. Do not create a second long-term identity / agenda / goal ledger parallel to `resident.autonomy`.
4. Do not let `KernelOne` absorb Polaris business policy.
5. Do not let `Cognitive Runtime` become a second context truth owner.
6. Do not claim new graph truth until graph assets are explicitly updated.

The core sentence is:

`Context OS owns the working set, not the truth set.`

---

## 4. Relation to Existing Specs

This plan is intentionally layered on top of existing documents:

1. `docs/CELL_EVOLUTION_ARCHITECTURE_SPEC.md`
   - defines `Cell` as long-term internal architecture IR
2. `polaris/docs/cognitive_runtime_architecture.md`
   - defines the future cross-role authority layer
3. `docs/KERNELONE_CONTEXT_STRATEGY_FRAMEWORK_BLUEPRINT_2026-03-25.md`
   - defines shared Agent foundation and multi-strategy runtime direction
4. `docs/SESSION_CONTINUITY_ENGINE_BLUEPRINT_2026-03-25.md`
   - defines the current continuity convergence path

This plan adds the missing middle layer:

`Cell IR for long-term truth -> Context OS for working memory -> Cognitive Runtime for authority`

---

## 4.1 Domain Boundary Decision

The `State-First Context OS` must be split into:

1. domain-agnostic kernel runtime
2. domain adapters

Current judgment:

1. the runtime model itself is generic
2. the current strongest proving ground is the `code` domain
3. several heuristics that feel "natural" today are actually code-biased and must not be frozen into the kernel core

### Kernel-generic responsibilities

The following belong to the generic Context OS:

1. append-only transcript truth
2. routing into `clear / patch / archive / summarize`
3. structured working-state objects
4. artifact offload / restore protocol
5. episode sealing
6. budget controller
7. liveness-based active window
8. prompt composition contract
9. generic memory lookup interfaces

### Code-domain responsibilities

The following should be treated as `code` domain adapters or overlays:

1. file path and symbol extraction
2. repo map and repo intelligence
3. dependency reachability over source graph
4. line-range / file-slice evidence reconstruction
5. code tool-result normalization
6. diff / patch / test-failure aware artifact previews
7. code-centric open-loop heuristics such as `fix`, `refactor`, `patch`, `run tests`

### Why this matters

If these code-biased signals are left in the kernel core:

1. future writer / document / research roles inherit the wrong object priorities
2. the working-state schema stays generic in name but code-biased in behavior
3. Polaris will be forced to unwind kernel internals later instead of swapping adapters

The construction rule is:

`KernelOne owns the context runtime mechanics; domains own retrieval evidence semantics.`

### Landed compatibility rule

The first rollout should not force all current consumers to know about adapter internals.

Therefore:

1. `SessionContinuityEngine` remains the compatibility facade
2. `StateFirstContextOS` becomes the runtime substrate under that facade
3. `code` is the first strengthened adapter, but not the default truth for all future roles
4. the runtime may expose `state_first_context_os` as a prompt-facing derived view
5. persisted `state_first_context_os` data must remain a derived runtime view, not a duplicate raw transcript store

The hard boundary is:

`roles.session` keeps raw turn truth; `state_first_context_os` only keeps a rebuildable working-memory view`

---

## 5. Canonical Runtime Objects

Phase 1 must standardize the following objects before deep integration:

1. `TranscriptEvent`
   - immutable turn / tool / artifact / retrieval event in append-only order
2. `ArtifactRecord`
   - large payload offload unit with preview and restore path
3. `StateEntry`
   - versioned state patch with `source_turns`, `confidence`, `updated_at`, `supersedes`
4. `DecisionEntry`
   - accepted / rejected / superseded decision ledger item
5. `EpisodeCard`
   - sealed closed-history card with digest, outcome, decisions, artifacts, and reopen conditions
6. `RunCard`
   - current working-memory view for the next turn
7. `ContextSlicePlan`
   - auditable explanation of what enters prompt and why
8. `BudgetPlan`
   - provider-aware prompt budget plan
9. `ContextOSSnapshot`
   - persisted runtime projection of the Context OS state

For current Polaris integration, the persisted projection must exclude raw transcript duplication whenever possible.
The canonical raw turn history still belongs to `roles.session`.

The main architectural correction is:

1. transcript is immutable
2. state is patchable and versioned
3. artifacts are offloaded and restorable
4. episodes are sealable and retrievable
5. prompt assembly is always rebuilt from state plus retrieval, never from destructive history replacement

---

## 6. Target Subsystems

The `State-First Context OS` should be decomposed into these subsystems:

1. `Truth Layer`
   - append-only transcript events
2. `Routing Layer`
   - classify content into `clear`, `patch`, `archive`, `summarize`
3. `Working State`
   - structured user/task/decision/entity/artifact state
4. `Artifact Store`
   - typed large-payload offload and restore
5. `Episode Store`
   - closure-based sealed history cards
6. `Hybrid Memory Index`
   - retrieval orchestration across multiple signals
7. `Liveness GC / Active Window Manager`
   - root-based prompt residency
8. `Budget Controller`
   - soft / hard / emergency budget planning
9. `Prompt Composer`
   - stable prefix + working state + recalled memory + active raw turns

---

## 7. Rollout Phases

Current rollout status:

1. `Phase 0` - landed
2. `Phase 1` - landed
3. `Phase 2` - landed
4. `Phase 3` - landed
5. `Phase 4` - landed
6. `Phase 5` - landed in deterministic form
7. `Phase 6` - landed, including `roles.runtime` / `roles.session` / `delivery.cli` / `roles.kernel` compatibility consumers and session-aware `context.engine` adoption

### Phase 0: Spec and Contract Freeze

Goal:

1. finalize object names, boundaries, and ownership
2. prevent `Cognitive Runtime` and `Context OS` from collapsing into one vague layer

Outputs:

1. this plan
2. a detailed blueprint
3. updated `polaris/docs/cognitive_runtime_architecture.md`
4. `docs/KERNELONE_STATE_FIRST_CONTEXT_OS_PHASE1_EXECUTION_BLUEPRINT_2026-03-26.md`

Acceptance:

1. object model is explicit
2. boundary sentence is explicit
3. compatibility with `CELL_EVOLUTION_ARCHITECTURE_SPEC` is explicit

### Phase 1: Truth Layer + Routing + Artifact Offload

Goal:

1. stop destructive continuity-by-replacement
2. make large payloads typed and restorable

Implementation targets:

1. `polaris/kernelone/context/context_os/models.py`
2. `polaris/kernelone/context/context_os/runtime.py`
3. `polaris/kernelone/context/history_materialization.py`

Deliverables:

1. append-only `TranscriptEvent` model
2. routing classes: `noise`, `state`, `evidence`, `narrative`
3. `ArtifactRecord` with preview and restore path
4. `canonicalize_and_offload()` pipeline

Acceptance:

1. tool/file/search large payloads no longer need to stay inline in working prompt history
2. every offloaded payload has a typed stub
3. no loss of restore path

### Phase 2: Working State + Version Semantics

Goal:

1. replace single large continuity summary with structured state

Implementation targets:

1. `polaris/kernelone/context/context_os/models.py`
2. `polaris/kernelone/context/session_continuity.py`

Deliverables:

1. `user_profile`
2. `task_state`
3. `decision_log`
4. `active_entities`
5. `active_artifacts`
6. `temporal_facts`
7. `supersedes` semantics for updates

Acceptance:

1. old and new versions of facts can be distinguished
2. state patches are auditable
3. continuity projection becomes a derived view of structured state

### Phase 3: Episode Store + Closure Semantics

Goal:

1. stop compressing only by token cliffs
2. seal completed work into retrievable units

Implementation targets:

1. `polaris/kernelone/context/context_os/runtime.py`
2. `polaris/kernelone/context/session_continuity.py`

Deliverables:

1. episode closure heuristics
2. multi-resolution digests
3. sealed episode persistence in `ContextOSSnapshot`

Acceptance:

1. closed subtasks are sealable without destroying transcript truth
2. reopened work can reference previous episodes explicitly

### Phase 4: Budget Controller + Liveness-Based Active Window

Goal:

1. replace fixed threshold compaction
2. make prompt residency depend on liveness, not recency alone

Implementation targets:

1. `polaris/kernelone/context/context_os/runtime.py`
2. `polaris/kernelone/context/session_continuity.py`
3. `polaris/cells/roles/runtime/public/service.py`

Deliverables:

1. provider-aware `BudgetPlan`
2. soft / hard / emergency thresholds
3. liveness roots and sweep rules
4. `ContextSlicePlan`

Acceptance:

1. prompt assembly becomes explainable
2. active raw turns are chosen by roots, not only FIFO
3. compaction only touches closed history, not the entire truth set

### Phase 5: Hybrid Retrieval + Verification Harness

Goal:

1. make recalled memory measurable
2. stop relying on vector-only recall

Implementation targets:

1. `polaris/kernelone/context/context_os/runtime.py`
2. `polaris/kernelone/context/repo_intelligence/`
3. `polaris/kernelone/context/chunks/`
4. context tests

Deliverables:

1. retrieval scoring contract
2. search helpers for state, artifacts, episodes
3. evaluation metrics for exact fact recovery, open-loop continuity, artifact restore precision, temporal update correctness, abstention, compaction regret

Acceptance:

1. retrieval can explain why an item was recalled
2. quality is measurable against baseline scenarios

### Phase 6: Consumer Integration

Goal:

1. integrate the Context OS as a shared foundation without changing public truth ownership

Consumers:

1. `roles.runtime`
2. `delivery.cli`
3. `context.engine`
4. future `Cognitive Runtime`

Current landed scope:

1. `roles.runtime` continuity projection compatibility
2. `delivery.cli` / `roles.kernel` debug and prompt-facing continuity rendering
3. `agent` / `role_session` HTTP restore consumers on top of canonical `roles.session`
4. `context.engine` public-service continuity overlay support
5. `context.engine` session-aware continuity/context_os overlay hydration via `session_id`
6. unified enable switches (default-on): `POLARIS_CONTEXT_OS_ENABLED`, `POLARIS_COGNITIVE_RUNTIME_MODE`

Acceptance:

1. `context.engine` remains the facade
2. `roles.session` remains raw-truth owner
3. `Cognitive Runtime` can later consume Context OS without rewriting it

---

## 8. Module Placement

Canonical placement should be:

```text
polaris/kernelone/context/
  context_os/
    __init__.py
    models.py
    runtime.py
  session_continuity.py
  history_materialization.py
```

Consumer touchpoints:

```text
polaris/cells/roles/runtime/public/service.py
polaris/delivery/cli/director/console_host.py
polaris/cells/roles/kernel/internal/context_gateway.py
polaris/cells/context/engine/**
```

Future consumer layer:

```text
polaris/application/cognitive_runtime/
polaris/cells/factory/cognitive_runtime/
```

---

## 9. Testing and Evaluation Plan

At minimum, the implementation must be verified at three levels:

### Unit

1. routing classification
2. artifact stub generation
3. state patch supersede semantics
4. episode sealing
5. budget planning
6. liveness root selection

### Integration

1. `SessionContinuityEngine` compatibility
2. `RoleRuntimeService` projection compatibility
3. CLI host continuity persistence compatibility

### Quality / Benchmark

1. exact fact recovery
2. decision preservation
3. open-loop continuity
4. artifact restore precision
5. temporal update correctness
6. abstention when no memory exists
7. compaction regret
8. cache hit rate
9. token / latency / tool-count deltas

---

## 10. Risks and Mitigations

### Risk 1: Context OS grows into a second business runtime

Mitigation:

1. keep it inside `KernelOne`
2. keep it strictly business-agnostic
3. keep business authority in consuming cells

### Risk 2: Cognitive Runtime duplicates Context OS

Mitigation:

1. state clearly that `Cognitive Runtime` consumes Context OS
2. do not define a second working-memory store there

### Risk 3: Graph truth and recalled memory drift apart

Mitigation:

1. keep graph as truth
2. make retrieval advisory, not truth-mutating

### Risk 4: Continuity compatibility regressions

Mitigation:

1. preserve current `SessionContinuityEngine` compatibility shape
2. upgrade internals first, public shape second

---

## 11. Final Implementation Order

The recommended order is:

1. freeze the blueprint and contracts
2. finish `context_os` object model and runtime internals
3. refit `SessionContinuityEngine` as a projection layer over Context OS
4. refit history materialization around artifact offload
5. add tests and quality harness
6. only then wire future `Cognitive Runtime` consumers

The short version:

`Do not build Cognitive Runtime first. Build Context OS first, then let Cognitive Runtime consume it.`

---

## 12. Completion Closure (2026-03-26)

This plan is considered complete with the following closure facts:

1. `Phase 0` through `Phase 6` are landed.
2. Context OS and Cognitive Runtime are runtime-switchable.
3. Default behavior is enabled:
   - `POLARIS_CONTEXT_OS_ENABLED` -> enabled by default
   - `POLARIS_COGNITIVE_RUNTIME_MODE` -> `shadow` by default
4. `roles.session` remains canonical raw-truth owner.
5. `context.engine` remains public facade.
6. `Cognitive Runtime` remains non-blocking sidecar authority unless explicitly promoted.

Out of scope for this specific plan (next-cycle work, not phase debt):

1. broader cross-subsystem adoption outside the declared consumer set
2. production hardening and performance tuning beyond acceptance gates
3. future mainline-gating policy rollout for Cognitive Runtime
