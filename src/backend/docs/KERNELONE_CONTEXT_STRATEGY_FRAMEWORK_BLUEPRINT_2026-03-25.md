# KernelOne Agent Foundation / Work Strategy Framework Blueprint

Status: Draft  
Date: 2026-03-25  
Scope: `polaris/kernelone/context/`, `polaris/cells/roles/session/`, `polaris/cells/roles/runtime/`, `polaris/cells/context/catalog/`, `polaris/cells/workspace/integrity/`

> This document is a blueprint, not graph truth.
> Current truth still follows `AGENTS.md`, `docs/graph/**`, `docs/FINAL_SPEC.md`, and cell manifests.
> This blueprint upgrades the earlier "canonical exploration policy" idea into a reusable, measurable KernelOne framework.
> The first implementation target is not role polishing.
> Phase 1 is to strengthen shared Agent foundation capabilities first, then layer role-specific behavior later.
>
> Execution note:
> the concrete rollout order is defined in
> `docs/KERNELONE_CONTEXT_STRATEGY_IMPLEMENTATION_MASTER_PLAN_2026-03-25.md`.

---

## 1. Decision

Polaris should not freeze code exploration, session continuity, history assembly, and compaction behavior into one hardcoded policy, nor bind the first major investment to a single role such as Director.

The better direction is:

`KernelOne Agent Foundation / Work Strategy Framework + Phase 1 shared foundation + Quantitative Evaluation Harness`

Meaning:

1. `KernelOne` owns the generic strategy framework, contracts, cache, receipts, replay, and evaluation.
2. `roles.session` remains the canonical owner of session state and continuity inputs.
3. `RoleRuntimeService` remains the canonical runtime entrypoint for role turns.
4. The current canonical MAP -> SEARCH -> SLICE -> EXPAND -> NEAR-LIMIT COMPACT behavior becomes the Phase 1 code-domain default profile, not the only profile.
5. Role-specific tuning is a later overlay on top of the common foundation, not the starting point.
6. Strategy quality must be measured continuously, not argued from intuition.

This yields three things the current stopgap does not provide:

1. Flexibility: switch or tune strategies without rewriting the runtime.
2. Auditability: every decision path emits structured receipts.
3. Evolvability: future strategy changes can be compared against a baseline with real metrics.

---

## 2. Why a framework is better than a single policy

If Polaris only ships a single hardcoded canonical policy, the short-term behavior may improve, but the system remains weak in four ways:

1. Strategy changes become code edits in core runtime instead of data/config/profile changes.
2. Different workloads cannot be tuned independently.
3. "This feels better" cannot be converted into evidence.
4. Regression analysis becomes anecdotal because there is no strategy identity or baseline.

The user requirement here is not just "pick the best policy today".
The real requirement is:

1. allow multiple policies,
2. compare them safely,
3. keep the canonical one as default,
4. make later evolution evidence-driven.

That is a framework problem, not a one-off policy problem.

---

## 3. Architectural position

This framework belongs in `KernelOne`, not in a single role cell.

Reason:

1. Exploration, history assembly, budget gating, and compaction are cross-role runtime capabilities.
2. The logic is Agent/AI operating substrate logic, not Polaris business semantics.
3. The same machinery should be reusable by `director`, `architect`, `chief_engineer`, `pm`, `qa`, future Scout-style readers, and future non-code creators.

Ownership split:

1. `KernelOne`
   - strategy contracts
   - strategy registry and profile resolution
   - working-set assembly
   - budget gate
   - hot cache
   - decision receipts
   - replay harness
   - strategy evaluation metrics

2. `roles.session`
   - session rows
   - continuity state inputs
   - recent message window
   - stable facts / open loops persistence

3. `roles.runtime`
   - runtime turn orchestration
   - strategy selection handshake
   - streaming execution integration

4. `context.catalog` and `workspace.integrity`
   - candidate generation
   - repo map
   - symbol evidence
   - slice/read/search capability inputs

### 3.1 Layering model

The framework should be understood as three layers:

1. shared Agent foundation
   - continuity
   - working set assembly
   - budget gate
   - cache
   - receipts / metrics / replay

2. domain adapters
   - code
   - document
   - fiction
   - research

3. role overlays
   - governance roles
   - execution-family roles
   - future creator/research overlays

The first implementation goal is layer 1.
The first high-difficulty proving domain is `code`.
Role overlays are postponed until the common foundation is stable.

### 3.2 Role taxonomy

Polaris should not treat every future capability name as a new top-level role.

The role taxonomy should be split into two families:

1. governance / steering roles
   - `PM`
   - `Architect`
   - `ChiefEngineer`
   - `QA`

2. execution-family roles
   - `Director`
   - future `Coder`
   - future `Writer`
   - other execution-specialized creator roles

The intended distinction is:

1. `PM`
   - plans work
   - decomposes tasks
   - does not own final execution

2. `Architect`
   - sets target direction and structural approach
   - does not own final execution

3. `ChiefEngineer`
   - produces technical blueprint / engineering judgment / design constraints
   - does not own final execution

4. `Director`
   - owns delivery and execution
   - is not code-only
   - belongs to the final landing layer
   - can orchestrate specialized execution overlays or subagents

5. `Coder` / `Writer` / similar future roles
   - should be modeled as `Director`-line execution overlays or subagent specializations
   - not as replacements for `Director`
   - not as new top-level governance roles

This matters because Polaris's common foundation should primarily serve the execution layer first, while still remaining reusable by governance roles.

---

## 4. Framework shape

The framework should expose a strategy bundle instead of one monolithic policy object.

### 4.1 Strategy bundle

Each bundle contains independently swappable sub-strategies:

1. `ExplorationStrategy`
   - controls MAP / SEARCH / SLICE / EXPAND / READ_FULL progression

2. `ReadEscalationStrategy`
   - decides when slice is enough and when full-file read is allowed

3. `HistoryMaterializationStrategy`
   - decides how tool receipts and prior turns enter prompt history

4. `SessionContinuityStrategy`
   - decides how summary, stable facts, open loops, and recent turns are assembled

5. `CompactionStrategy`
   - decides when compaction is triggered and which assets get compacted first

6. `CacheStrategy`
   - decides cache lookup order, TTLs, invalidation, and reuse thresholds

7. `EvaluationStrategy`
   - decides how a run is scored for offline replay or online shadow comparison

This decomposition matters because the best exploration policy is not always the best compaction or history policy.

### 4.2 Domain adapters and role overlays

The strategy framework must not assume every task is code.

Instead, domain-specific capability should arrive through adapters:

1. `code`
   - repo map
   - symbol index
   - slices
   - diff / patch / test evidence

2. `document`
   - outline
   - section graph
   - citations / references
   - revision history

3. `fiction`
   - world bible
   - character sheets
   - plot arcs
   - scene inventory

4. `research`
   - source cards
   - claim graph
   - note bundles
   - evidence chain

Role-specific behavior should then be modeled as overlays on top of a shared foundation plus a chosen domain adapter.

For Polaris's execution stack, the preferred model is:

1. `Director` as the parent execution role
2. `Coder`, `Writer`, and similar future execution specialists as overlays or subagents beneath `Director`
3. governance roles remaining separate from the execution-family lineage

### 4.3 Strategy resolution

Strategy resolution order should be:

1. explicit session override
2. workspace/runtime policy
3. domain default profile
4. role overlay profile
5. global canonical foundation profile

This keeps canonical behavior stable while allowing controlled overrides.

### 4.4 Strategy identity

Every run must carry:

1. `strategy_bundle_id`
2. `strategy_bundle_version`
3. `strategy_profile_id`
4. `strategy_profile_hash`

Without stable identity, later comparison is not trustworthy.

---

## 5. Canonical built-in profiles

Polaris should ship multiple built-in profiles from day one, but they should be treated as foundation profiles first.
Role-specific polish comes later as overlays or constrained variants.

### 5.1 `canonical_balanced`

Default foundation profile.

Characteristics:

1. MAP first
2. SEARCH before read
3. range-first for medium and large files
4. near-limit compaction only
5. aggressive tool-receipt micro-compaction
6. hot-slice cache enabled

Target:

1. general asset-intensive work
2. Phase 1 primary target: coding sessions
3. best overall balance of quality, cost, and latency

### 5.2 `speed_first`

Characteristics:

1. lighter MAP
2. fewer expansion passes
3. tighter prompt budget
4. more cache reuse
5. minimal continuity payload

Target:

1. quick debugging
2. short CLI turns
3. low-latency environments

### 5.3 `deep_research`

Characteristics:

1. stronger repo map and symbol graph usage
2. more aggressive neighbor expansion
3. larger working set before compaction
4. slower but deeper evidence collection

Target:

1. root-cause analysis
2. architectural investigations
3. broad refactors

### 5.4 `cost_guarded`

Characteristics:

1. strict budget gate
2. early micro-compaction of tool receipts
3. conservative full-read escalation
4. cache-first behavior

Target:

1. cost-sensitive deployments
2. smaller-context local models

### 5.5 `claude_like_dynamic`

Reference profile, not product emulation.

Characteristics:

1. dynamic search-first exploration
2. implicit map by search evidence
3. small-file direct read allowed
4. large-file slice-first
5. near-limit trimming and continuity fallback

Target:

1. benchmark comparison against a common industry reading pattern
2. validating whether Polaris's canonical profile is actually better

---

## 6. Execution lifecycle

The framework should run inside the canonical session/runtime path:

`roles.session -> RoleRuntimeService -> KernelOne Agent Foundation / Work Strategy Framework -> domain adapters -> LLM turn`

Per turn lifecycle:

1. `roles.session` loads session state and continuity inputs.
2. `RoleRuntimeService` resolves the effective strategy bundle/profile.
3. `KernelOne` creates a `StrategyRunContext`.
4. `SessionContinuityStrategy` materializes continuity assets.
5. the selected domain adapter provides the candidate surface for the turn.
6. `ReadEscalationStrategy` gates `read_file` upgrades.
7. `HistoryMaterializationStrategy` decides what goes into the prompt and in what form.
8. `CompactionStrategy` triggers only when budget gates require it.
9. `CacheStrategy` handles hot assets and reuse.
10. `EvaluationStrategy` writes receipts and scores the run.

The important point is that roles do not invent their own base logic anymore.
They execute within a governed foundation envelope, then add role-specific behavior later.

---

## 7. Metrics that must be collected

If the framework cannot quantify results, it will decay back into intuition and prompt folklore.

Metrics should be grouped into five families.

### 7.1 Effectiveness

1. task success rate
2. acceptance test pass rate
3. user correction rate
4. invalid file/path/tool call rate
5. repeated identical read loop rate

### 7.2 Efficiency

1. first relevant slice latency
2. total tool calls per turn
3. full-file read ratio
4. slice read ratio
5. average expansion depth
6. cache hit rate by tier

### 7.3 Context quality

1. prompt input tokens
2. tool receipt tokens kept vs compacted
3. continuity tokens vs exploration tokens
4. wasted-context ratio
5. compaction trigger utilization ratio

### 7.4 UX / streaming

1. TTFT
2. real first content delta timestamp
3. model-finished to UI-finished lag
4. pseudo-stream lag
5. visible update batch size distribution

### 7.5 Cost and stability

1. input token cost
2. output token cost
3. total turn wall time
4. provider timeout rate
5. fallback path rate

These metrics must be written as structured receipts, not inferred later from logs only.

---

## 8. Evaluation harness

Polaris should not compare strategies by ad-hoc manual trials.
It should ship a first-class evaluation harness.

### 8.1 Offline replay

Use recorded or synthetic benchmark cases:

1. summarize a project
2. locate a bug root cause
3. edit a targeted symbol
4. perform a cross-file refactor
5. resume a long-running session

Each replay case must include:

1. user prompt
2. workspace snapshot or fixture
3. expected evidence path
4. expected edit or answer shape
5. budget/window conditions

### 8.2 Shadow mode

In live traffic, the canonical strategy can execute normally while another strategy runs in shadow:

1. primary result is shown to the user
2. shadow result writes receipts only
3. diffs are compared offline

This allows safe evolution without user-facing instability.

### 8.3 A/B and canary

When confidence is sufficient:

1. canary a new strategy to a small percentage of sessions
2. compare against the canonical baseline
3. promote only if the score delta is positive and regressions stay below threshold

### 8.4 Scorecard

Each strategy run should emit a normalized scorecard such as:

1. `quality_score`
2. `efficiency_score`
3. `context_score`
4. `latency_score`
5. `cost_score`
6. `overall_score`

The weighting model must be explicit and versioned.

---

## 9. Receipts and storage

This framework needs explicit runtime evidence.

Suggested derived assets:

1. `workspace/.polaris/runtime/strategy_runs/*.json`
2. `workspace/.polaris/runtime/strategy_metrics/*.jsonl`
3. `workspace/.polaris/runtime/strategy_benchmarks/*.json`
4. `workspace/.polaris/runtime/context_cache/*`

Each turn receipt should contain:

1. strategy identity
2. selected profile
3. model/provider context window
4. budget gate decisions
5. tool sequence
6. read escalation decisions
7. compaction decisions
8. cache hits/misses
9. prompt assembly breakdown
10. final evaluation metrics

These are derived runtime assets, not graph truth.

---

## 10. Guardrails

The framework must enforce several non-negotiable rules.

1. Session continuity is not code exploration.
2. Tool receipts do not enter long-term history unbounded.
3. Full-file read is a governed escalation, not the default.
4. Compaction is near-limit behavior, not per-turn ritual.
5. Strategy switching cannot bypass graph boundaries or workspace guard.
6. Cache cannot become source-of-truth.
7. Evaluation cannot mutate runtime truth.

---

## 11. Recommended implementation split

### 11.1 `KernelOne`

Add or converge:

1. `strategy_contracts.py`
2. `strategy_registry.py`
3. `strategy_profiles.py`
4. `strategy_runner.py`
5. `strategy_receipts.py`
6. `strategy_benchmark.py`

These can sit under `polaris/kernelone/context/` because this is context/runtime substrate work.

### 11.2 `roles.session`

Use `roles.session` only for:

1. session persistence
2. continuity inputs and stable session truth
3. session-level strategy override storage if needed

### 11.3 `RoleRuntimeService`

Use `RoleRuntimeService` as the only canonical integration entrypoint for:

1. resolving the effective strategy bundle
2. creating the per-turn strategy run context
3. wiring receipts into runtime events

### 11.4 `context.catalog` and `workspace.integrity`

Keep them as providers/candidate generators.
Do not move strategy ownership into those cells.

---

## 12. Rollout plan

### Phase 1

Shared Agent foundation and contracts.

1. define strategy bundle interfaces
2. define profile schema
3. define scorecard schema
4. define receipt schema
5. define domain adapter seam

### Phase 2

Runtime wiring and Phase 1 code-domain landing.

1. route through `roles.session + RoleRuntimeService`
2. move current canonical logic under `canonical_balanced`
3. keep behavior stable
4. treat code exploration as the first domain adapter, not the universal core

### Phase 3

Evaluation and proof.

1. replay harness
2. benchmark fixtures
3. offline scorecards

### Phase 4

Role overlays and alternative foundation profiles.

1. speed-first
2. deep-research
3. cost-guarded
4. dynamic search-first reference profile
5. only after this phase should role-specific attributes be tuned on top

### Phase 5

Governance.

1. CI checks for score regression
2. shadow-mode dashboards
3. promote/demote strategy profiles based on evidence

---

## 13. Final recommendation

The right answer is not:

1. hardcode a single canonical strategy forever, or
2. leave behavior to per-role prompt heuristics.

The right answer is:

`KernelOne framework first, canonical default strategy second, measurement everywhere`

In short:

1. build the mechanism in `KernelOne`
2. strengthen shared Agent foundation first
3. keep `canonical_balanced` as the default Phase 1 foundation profile
4. route execution through `roles.session + RoleRuntimeService`
5. attach receipts, benchmarks, and scorecards to every strategy run
6. evolve by evidence, not by taste

That gives Polaris the flexibility you want and the quantified reference data needed for future evolution.
