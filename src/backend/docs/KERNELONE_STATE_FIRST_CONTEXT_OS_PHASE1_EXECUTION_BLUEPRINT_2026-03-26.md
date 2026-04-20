# KernelOne State-First Context OS Phase 1 Execution Blueprint

- Status: Landed / Verification Ongoing
- Date: 2026-03-26
- Scope: `polaris/kernelone/context/**` and continuity consumers

> This document is a file-level execution blueprint for Phase 1.
> It translates the State-First Context OS master plan and blueprint into a concrete construction order.

> Implementation note as of 2026-03-26:
> the Phase 1 foundation is now landed in code:
> immutable transcript projection, artifact offload, structured working state,
> episode sealing, budget planning, liveness-based active window, and continuity facade compatibility.
> Prompt-facing continuity consumers now render canonical `run_card` / `context_slice_plan`
> data directly, rather than relying only on legacy anchor fields.

---

## 1. Phase 1 Goal

Phase 1 exists to land the first irreversible architectural correction:

`Stop treating continuity as destructive summary replacement, and start treating it as immutable transcript + state patch + artifact offload.`

Phase 1 does **not** try to complete the full Context OS.
It only establishes the minimum runtime foundation required to safely continue.

---

## 2. Current Code Reality

At the time of this blueprint:

1. `polaris/kernelone/context/context_os/models.py` already exists and contains the initial object family.
2. `polaris/kernelone/context/context_os/runtime.py` already exists but was not yet complete when this blueprint was written.
3. `polaris/kernelone/context/session_continuity.py` is still the canonical continuity entry and must remain compatibility-safe.
4. `polaris/kernelone/context/history_materialization.py` has now been upgraded to emit materialized payloads and artifact-aware receipt stubs while preserving the compatibility facade.
5. Existing consumers already depend on `SessionContinuityEngine`, especially:
   - `polaris/cells/roles/runtime/public/service.py`
   - `polaris/delivery/cli/director/console_host.py`
   - `polaris/cells/roles/kernel/internal/context_gateway.py`
   - `polaris/delivery/http/routers/agent.py`
   - `polaris/delivery/http/routers/role_session.py`
6. HTTP restore consumers are now landed for both `agent.py` and `role_session.py`, covering:
   - `memory/search`
   - `memory/artifacts/{artifact_id}`
   - `memory/episodes/{episode_id}`
   - `memory/state`

Therefore Phase 1 must preserve the external continuity contract while changing the internals.

---

## 2.1 Code-Domain Bias Decision

This Phase 1 rollout is being proven first in the `code` domain.
That is correct pragmatically, but dangerous architecturally if not made explicit.

The rule for Phase 1 is:

1. the runtime core must stay generic
2. the first adapter and benchmark suite may be code-first

Implementation note:

1. adapters should live under `polaris/kernelone/context/context_os/domain_adapters/`
2. the generic adapter remains the default continuity substrate
3. the `code` adapter is the first enhancement path, not a kernel hard-dependency

### Generic core in this phase

These can be implemented directly in the core:

1. transcript truth
2. artifact offload protocol
3. state patching
4. episode sealing
5. budget planning
6. liveness GC
7. prompt composition

### Code-biased logic that must be isolated

These must stay in adapter-facing or heuristic modules, not hardwired into the runtime substrate:

1. path and symbol extraction
2. repo intelligence references
3. dependency reachability over code graph
4. code-tool receipt normalization
5. diff / patch / failing-test artifact semantics
6. code-specific open-loop phrases

If implementation pressure forces one of these into the runtime temporarily, it must be marked as code-domain debt in comments and tests.

---

## 3. File-by-File Construction Plan

### 3.1 `polaris/kernelone/context/context_os/models.py`

Status:

1. already created
2. should be treated as the canonical object-contract file

Required actions:

1. verify all fields needed by Phase 1 are present and stable
2. keep dataclasses immutable where practical
3. ensure mapping serialization helpers are complete and symmetric
4. verify `ContextOSSnapshot`, `ContextOSProjection`, `WorkingState`, `ArtifactRecord`, `EpisodeCard`, `StateEntry`, `DecisionEntry`, `BudgetPlan`, `StateFirstContextOSPolicy`

Phase 1 must guarantee:

1. no hidden required fields
2. `from_mapping()` paths are defensive
3. snapshot payloads can round-trip cleanly

Do not do yet:

1. provider-specific optimization fields beyond current needs
2. business semantics

### 3.2 `polaris/kernelone/context/context_os/runtime.py`

Status:

1. already created
2. currently partial and must be completed before integration

Required actions:

1. complete transcript merge logic
2. complete canonicalize/offload routing
3. complete working-state patch flow
4. complete budget planning
5. complete active-window collection
6. complete episode sealing
7. complete artifact selection for prompt projection
8. complete episode selection for prompt projection
9. complete head/tail anchor builders

Required private methods:

1. `_merge_transcript`
2. `_canonicalize_and_offload`
3. `_patch_working_state`
4. `_plan_budget`
5. `_collect_active_window`
6. `_seal_closed_episodes`
7. `_select_artifacts_for_prompt`
8. `_select_episodes_for_prompt`
9. `_build_head_anchor`
10. `_build_tail_anchor`

Phase 1 behavior target:

1. new events become append-only transcript items
2. large evidence becomes artifact stubs
3. prompt projection is rebuilt from state + active window, not from destructive replacement

Do not do yet:

1. full semantic retrieval scoring
2. advanced dependency graph recall
3. provider-native compaction integration
4. permanent code-domain heuristics in the kernel core

### 3.3 `polaris/kernelone/context/session_continuity.py`

Status:

1. current canonical continuity API
2. many downstream callers depend on its shape

Required actions:

1. preserve `SessionContinuityEngine` as the compatibility facade
2. rebase internals to use `StateFirstContextOS`
3. keep `SessionContinuityProjection` and `SessionContinuityPack` as derived projection shapes
4. persist `context_os` snapshot separately from prompt-facing `session_continuity`
5. ensure short sessions still degrade gracefully

Phase 1 design rule:

1. `session_continuity` becomes a projection
2. `context_os` becomes the runtime substrate
3. transcript truth is never replaced by summary text
4. prompt-facing `state_first_context_os` should be a bounded derived view
5. persisted `state_first_context_os` must not duplicate `transcript_log` or full archived payload content

Likely changes:

1. no external continuity contract break is required for Phase 1
2. `persisted_context_config["state_first_context_os"]`
3. compatibility-preserving `persisted_context_config["session_continuity"]`

Refined persistence rule for the current implementation:

1. use `persisted_context_config["state_first_context_os"]`
2. persist adapter id, working state, artifact metadata, episode cards, and budget plan
3. do not persist the full raw transcript there
4. do not rely on `state_first_context_os` as a second source-of-truth for raw turns

### 3.4 `polaris/kernelone/context/history_materialization.py`

Status:

1. continuity-aware and compatibility-safe
2. artifact-aware receipt stubs landed
3. materialized payload fields landed for strategy consumers

Required actions:

1. keep artifact stubs aligned with prompt assembly consumers
2. preserve compatibility for history strategies and overlays
3. avoid destructive inline replay of oversized tool payloads

Phase 1 behavior target:

1. history assembly should carry references, not just truncated blobs
2. old materialization contract remains usable by strategy system
3. code-specific artifact shaping should remain separable from the generic stub protocol

### 3.5 `polaris/kernelone/context/__init__.py`

Required actions:

1. export `context_os` public kernel-level types and runtime
2. avoid circular import regression
3. keep backward compatibility for existing continuity imports

### 3.6 `polaris/cells/roles/runtime/public/service.py`

Required actions:

1. keep using `SessionContinuityEngine`
2. verify runtime projection still lands in the expected prompt context fields
3. ensure no call-site assumptions break if `context_os` snapshot is now persisted in config

Goal:

1. runtime entry stays stable while internals improve

### 3.7 `polaris/delivery/cli/director/console_host.py`

Required actions:

1. verify host-level continuity projection still behaves the same externally
2. ensure host does not become a second state owner
3. ensure reserved/internal keys remain filtered

### 3.8 `polaris/cells/roles/kernel/internal/context_gateway.py`

Required actions:

1. verify gateway consumption of `session_continuity` remains compatible
2. do not let gateway reach into raw `context_os` internals directly
3. keep gateway as consumer of projection, not owner of continuity logic

### 3.9 Optional hold line: HTTP continuity consumers

Files:

1. `polaris/delivery/http/routers/agent.py`
2. `polaris/delivery/http/routers/role_session.py`

Landed action:

1. expose HTTP restore/search endpoints on top of canonical `roles.session` memory service
2. verify no behavioral regression if continuity projection payload changes internally
3. cover both routers with SQLite-backed canonical tests

Current result:

1. both routers now reuse `RoleSessionContextMemoryService`
2. no second session memory owner was introduced
3. restore flows remain projection-based, not raw-transcript duplication

---

## 4. Test Construction Plan

### 4.1 New test file

Add:

1. `polaris/kernelone/context/tests/test_context_os.py`

It should cover:

1. transcript append behavior
2. artifact offload and stub generation
3. state patch extraction
4. supersedes semantics
5. episode sealing on closure
6. budget planning thresholds
7. active window liveness rules

It should also contain at least one test proving that generic runtime objects do not require code-specific fields such as file paths or symbols.

### 4.2 Existing tests to update

Must update:

1. `polaris/kernelone/context/tests/test_continuity.py`
2. `polaris/kernelone/context/tests/test_history_materialization.py`
3. `polaris/kernelone/tests/test_session_continuity_engine.py`
4. `polaris/cells/roles/runtime/tests/test_host_session_continuity.py`

Likely update:

1. `polaris/delivery/cli/director/tests/test_stream_protocol.py`

Optional watch tests:

1. `polaris/kernelone/context/tests/test_context_subsystem.py`
2. `polaris/cells/roles/kernel/tests/test_canonical_exploration_e2e.py`

### 4.3 Required new assertions

The suite must explicitly prove:

1. transcript truth is not destructively replaced
2. `session_continuity` is still emitted as a projection
3. `context_os` snapshot can be persisted and restored
4. large payloads become typed stubs with restore path
5. state entries can supersede older entries
6. active window is not plain FIFO
7. generic runtime behavior works even when no code-domain entities are present

---

## 5. Construction Order

Follow this order exactly:

### Step 1

Stabilize `models.py`

Done when:

1. all Phase 1 objects round-trip
2. no missing fields block runtime implementation

### Step 2

Finish `runtime.py`

Done when:

1. the runtime can build a full `ContextOSProjection`
2. there are no placeholder private methods left

### Step 3

Refit `session_continuity.py`

Done when:

1. external call sites still use `SessionContinuityEngine`
2. projection is derived from Context OS internals

### Step 4

Refit `history_materialization.py`

Done when:

1. artifact-aware history stubs exist
2. no large payload path silently bypasses offload behavior

### Step 5

Update exports and consumers

Done when:

1. `__init__.py` exports are coherent
2. runtime/host/gateway compatibility tests pass

### Step 6

Add tests and run verification

Done when:

1. all new and updated tests pass
2. no continuity contract regression remains

---

## 6. Acceptance Gates

Phase 1 is accepted only if all gates pass.

### Gate A: Truth Layer Integrity

Must prove:

1. transcript events are append-only
2. old turns remain reconstructible
3. no one-summary replacement path remains as the primary model

### Gate B: Artifact Offload Integrity

Must prove:

1. long tool/file/search payloads become `ArtifactRecord`
2. prompt-facing history uses typed stubs
3. restore path is present

### Gate C: Working-State Integrity

Must prove:

1. structured state patches exist
2. `supersedes` semantics work
3. projection can reconstruct continuity view from state

### Gate D: Compatibility

Must prove:

1. `SessionContinuityEngine` callers still work
2. CLI host and role runtime behavior do not regress
3. kernel/context import surface remains stable

### Gate E: Prompt Assembly Discipline

Must prove:

1. active window depends on liveness, not only recency
2. closed history can be excluded without losing truth
3. hard-pressure mode only compacts closed history

---

## 7. Verification Command Set

At minimum, Phase 1 verification should run:

```bash
python -m pytest -q \
  polaris/kernelone/context/tests/test_context_os.py \
  polaris/kernelone/context/tests/test_continuity.py \
  polaris/kernelone/context/tests/test_history_materialization.py \
  polaris/kernelone/tests/test_session_continuity_engine.py \
  polaris/cells/roles/runtime/tests/test_host_session_continuity.py
```

Recommended broader regression:

```bash
python -m pytest -q \
  polaris/kernelone/context/tests \
  polaris/kernelone/tests/test_session_continuity_engine.py \
  polaris/cells/roles/runtime/tests/test_host_session_continuity.py \
  polaris/delivery/cli/director/tests/test_stream_protocol.py
```

If failures appear in exploration or chunk assembly:

```bash
python -m pytest -q \
  polaris/cells/roles/kernel/tests/test_canonical_exploration_e2e.py \
  polaris/kernelone/context/tests/test_context_subsystem.py
```

---

## 8. Explicit Non-Goals for Phase 1

Phase 1 does not include:

1. full hybrid retrieval scoring implementation
2. provider-native compaction / caching integration
3. a new public `cognitive_runtime` cell
4. production-wired authority lattice
5. resident integration changes

If a change request drags one of those in, it belongs to a later phase.

---

## 9. Definition of Done

Phase 1 is done when:

1. `context_os/runtime.py` is complete and internally coherent
2. `SessionContinuityEngine` becomes a projection layer over Context OS
3. history materialization emits artifact-aware stubs
4. new tests cover transcript/state/artifact/episode/budget/liveness behavior
5. existing continuity consumers stay green
6. no second truth owner is introduced

---

## 10. One-Line Construction Rule

`Refactor internals radically, preserve the continuity facade, and prove every new runtime object through tests.`
