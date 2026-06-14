# Single-Source-of-Truth Hardening Blueprint: Collapsing the Three Dual-Truth Defects Behind the Weak-Model Empty-Output Wall

**Date:** 2026-06-13 · **Status:** Approved for staged implementation · **Context:** I3 market-fission weak-model loop (L2-12), runs r16→r18.

> Architecture-first per CLAUDE.md §4.1. This blueprint supersedes three surgical patches (F4/F5/F7) by **generalizing** them into canonical single-source paths. No deletion; resolve by refactor. Bindings (Director=local qwen, PM/CE/QA=MiniMax-M3) are never touched.

## 1. Problem Statement

The weak-model "empty Director/CE output" wall (live r18: Director 0/28 empty vs 69–74% before) was closed by three **surgical patches** — F4 (CE env `llm_max_tokens=16000`), F5 (scheduler field swap to `budget_plan.model_context_window`), F7 (provider_helpers reasoning-channel recovery). Each repaired exactly **one** site and **proved** the root-cause diagnosis, but left the architectural defect in place.

**All three defects are the same shape: a single logical value has TWO sources of truth, where one source is silently wrong or missing, and only some consumers route through the correct one.**

### DEFECT 1 — ContextOS dual window source of truth
"The model's real context window" exists as **(A)** `policy.context_window.model_context_window` (default `128_000`, `policies.py:44`) and **(B)** the ModelCatalog-resolved value via `StateFirstContextOS.resolved_context_window` (`engine.py:394-444`) → `BudgetPlan.model_context_window` (`stages.py:863`). The gateway builds ContextOS **without `policy=`** (`gateway.py:249-255`), so (A) stays frozen at 128k while (B) carries the truth (e.g. 16k qwen).

**WORSE — F5 was mis-targeted.** F5 retargeted `scheduler._collect_active_window` (`scheduler.py:604-610`), but that path is reached only via `_rebuild_prompt_view → get_state()` — an **introspection/read API**, NOT the live projection. The **live** `WindowCollector.process` (`stages.py:1003-1004`) still uses the OLD inline formula `max(512, min(budget_plan.soft_limit, int(budget_plan.input_budget * active_window_ratio)))` with **no small-window escalation**. r18 worked because the live `BudgetPlanner` (`stages.py:830`) already consumes `resolved_context_window`, shrinking `input_budget` so the old ratio coincidentally fits — and because F7 (silent INFO) carried the recovery. The live behavior never depended on the F5 edit. There is also a **second live copy** of the formula in `attention_aware_stages.py:194`.

### DEFECT 2 — Reasoning finalization duplicated per-provider/per-path
A reasoning model can return `content:null` with the answer in `reasoning_content` + `finish_reason:length`. Recovery logic is reimplemented in 3+ places (provider_helpers F7, minimax stream self-heal, minimax non-stream) and **missing/inconsistent** in others (ollama invoke, openai/anthropic/kimi `invoke_stream`, engine stream finalize). Two twin parsers exist: canonical `LLMResponseParser` (`response_parser.py`) and `ResponseNormalizer` (`normalizer.py:15`, content-only). F7 fixed only the non-stream openai-family funnel.

### DEFECT 3 — Output-token budget is a magic constant decoupled from capability
Role-caller default `max_tokens=4000` (`invoker.py:157`); the live decision path (`decision_caller.py:40`) omits `max_tokens`, silently binding 4000 for **every** role. `ModelSpec` (`shared_contracts.py:70-83`) has no `is_reasoning_model`/`reasoning_reserve_tokens`/`default_output_tokens`. Budget is only ever **clamped down** (`_executor_base.py:251-276`) or **reactively doubled** in one provider (`minimax_provider.py:684-702`). Nothing **derives** it upward from capability. MiniMax-M3 needs ~9.7k thinking + ~2k answer but got 4000 → `finish_reason=length` inside `<think>`, empty content. F4 hand-fed 16000 for CE only.

## 2. Architecture Principles

1. **Unifying principle:** every bug is "a value with two sources of truth where one is silently wrong". The fix shape is identical: collapse each value to **one canonical source every consumer routes through**, so divergence is structurally impossible — not patched per-site.
2. **SSoT by construction-time convergence** (DEFECT 1): where two fields must coexist for wire reasons, force them **equal at construction** (resolve-then-freeze) so it no longer matters which channel a reader picked.
3. **SSoT by funnel** (DEFECT 2 & 3): one canonical implementation every path routes through (`LLMResponseParser.finalize_response`; `derive_output_budget`). Forgetting the recovery/derivation becomes impossible because there is no second path.
4. **Derive up before clamp down:** capability-derived budgets computed from `ModelSpec` **before** window/prompt clamps; clamps **floor at the reasoning reserve** — never silently drop a reasoning model below its thinking need (fail-closed with a clear error, not an empty turn).
5. **Reuse canonical kernel capabilities:** `LLMResponseParser` (not a 4th parser), `resolved_context_window` (not a new field), `ModelSpec` (extend additively). CLAUDE.md §7.
6. **Generalize the patch into the canonical path; never a 4th special case.** F4/F5/F7 each become subsumed by, or the single retained instance of, the canonical mechanism. Default-safe + env-tunable so non-reasoning models stay byte-identical.
7. **Wiring is the test contract.** The originals survived because unit tests hand-injected the resolved value (false green). Every fix ships an integration/wiring test that constructs the object the way **production** does (gateway without `policy=`, live `decision_caller` without `max_tokens`, the live pipeline path) and asserts what the **live** path computes.

## 3. Target Architecture

### DEFECT 1 — canonical SSoT
`resolved_context_window` is THE single window source. At `StateFirstContextOS` construction the resolved value is computed once and written **back into the policy**, so `policy.context_window.model_context_window == resolved_context_window == BudgetPlan.model_context_window` for the life of the instance. The active-window token-budget formula has exactly **one** implementation, shared by the live `WindowCollector`, `attention_aware_stages`, and the introspection scheduler.

- **Resolve-then-freeze** in `engine.py:__init__`: after capturing provider/model/fallback, eagerly read `resolved_context_window` and re-bind policy via `dataclasses.replace` (NOT `validated_replace` — policies are `@dataclass(frozen=True, slots=True)`), guarded by the existing try/except degrade, **before** policy-derived init (`engine.py:89`).
- **Lift the helper:** move `_active_window_token_budget` + `_SMALL_CONTEXT_WINDOW_TOKENS` + `_SMALL_WINDOW_ACTIVE_RATIO` from `scheduler.py:57-85` into `context_os/budget_math.py`; import from `scheduler.py:604`, the live `WindowCollector.process` (`stages.py:1004`), and `attention_aware_stages.py:194`.
- **Harden other live constructors** that silently inherit 128k: `cognitive_runtime/service.py:194`, `context_memory_service.py:28` (mirror `gateway.py:254`). Emit a telemetry/debug event on unresolved-binding degrade.
- (Optional, larger) collapse the three budget-planner copies (`state.py:_plan_budget`, `stages.py:BudgetPlanner`, `phase_budget_planner.py`) onto one pure `plan_budget(...)`.

**F5** is generalized & subsumed (kept but no longer load-bearing alone).

### DEFECT 2 — canonical SSoT
`LLMResponseParser.finalize_response(payload)` is the ONE finalizer, encoding the branch table once: visible text ok → `ok=True`; reasoning present AND `is_length_finish_reason` → fail-closed (`ok=False`, `thinking=reasoning`, descriptive error); reasoning present (empty, not length) → recover `output=reasoning`, `ok=True`, `thinking=reasoning`; else empty. `ResponseNormalizer.extract_text` delegates to `LLMResponseParser` (re-export, not deletion). Route every provider non-stream + stream path through it. MiniMax stream `max_tokens` doubling stays as a **separate** empty-visible heal hook (defense-in-depth). Recovery stays strictly gated on **empty** visible output (CoT-leak guard).

**F7** is generalized into the finalizer and kept as one caller.

### DEFECT 3 — canonical SSoT
`ModelSpec` capability + ONE `derive_output_budget(model_spec, task_class, requested_override)` resolver. `ModelSpec` gains 3 additive default-safe fields (`is_reasoning_model=False`, `reasoning_reserve_tokens=0`, `default_output_tokens=0`) resolved by `ModelCatalog` from `llm_config.json` (conservative keyword fallback: minimax/qwen3.x/think-family → True, explicit config always wins). `derive_output_budget` becomes the source (replacing `resolve_requested_output_tokens` as source, keeping it as ceiling guard): `base = override else default_output_tokens or env answer_floor (~2000)`, `reserve = reasoning_reserve if reasoning else 0`, `derived = min(base+reserve, max_output_tokens)`, task_class scales base via env-tunable multiplier. Flip role-caller `max_tokens` default `4000 → None` (None = "let capability decide"); `answer_floor >= 4000` for non-reasoning so the only change is reasoning models getting **more, never less**. Clamps become reserve-aware (floor at `reserve+answer_floor`, else fail-closed). Demote provider self-heal to read `model_spec.max_output_tokens` (no hardcoded 8192/32768).

**F4** is generalized: the context-override channel is kept as the explicit escape hatch but no longer the only way to a non-4000 budget.

## 4. Sequenced Plan (smallest blast radius first)

- **STEP 0** (no code): land this blueprint + ADR. Config-load validation: `reasoning_reserve + answer_floor <= max_output_tokens`, fail-closed.
- **STEP 1** (DEFECT 1 live-path fix): lift helper into `budget_math.py`; import from scheduler + **live `WindowCollector`** + `attention_aware_stages`. *The single change that moves the r18 fix onto the production path.*
- **STEP 2** (DEFECT 1 SSoT): resolve-then-freeze in `engine.__init__`.
- **STEP 3** (DEFECT 1 constructor hardening): inject fallback/provider/model into the other live constructors; unresolved-binding telemetry.
- **STEP 4** (DEFECT 2 core): add `finalize_response`/`finalize_stream`; route provider_helpers (F7 block) through it; `ResponseNormalizer` delegates.
- **STEP 5** (DEFECT 2 coverage): route ollama/minimax-non-stream/openai-anthropic-kimi stream/engine-stream-finalize through finalizer.
- **STEP 6** (DEFECT 3 fields): add 3 `ModelSpec` fields + catalog resolvers + keyword fallback + config-load validation.
- **STEP 7** (DEFECT 3 derivation): add `derive_output_budget`; make clamps reserve-aware (depends on STEP 2).
- **STEP 8** (DEFECT 3 default flip): `4000 → None`; reconcile double `resolve_max_tokens`; demote self-heal. Broadest — ship last.

## 5. Test Strategy (wiring tests that would have caught the originals)

- **DEFECT 1 wiring:** construct `StateFirstContextOS` as the gateway does (provider+model+fallback, **no `policy=`**) for 16k, run the **live async `project()`** (not `get_state`), assert `WindowCollector` uses the small-window ratio.
- **DEFECT 1 invariant:** `resolved_context_window == policy.context_window.model_context_window == BudgetPlan.model_context_window` for resolvable AND unresolvable bindings.
- **DEFECT 1 constructor coverage:** parametrize every live constructor; none silently inherits 128k for a known small binding.
- **DEFECT 2 cross-provider contract:** `{minimax, openai_compat, ollama, anthropic, kimi} × {stream, non-stream}` over `{content:null, reasoning_content, finish_reason∈(stop,length)}` → same finalize outcome (recover when ≠length, fail-closed when length).
- **DEFECT 2 parser equivalence:** `ResponseNormalizer.extract_text == LLMResponseParser.extract_text` on a reasoning payload.
- **DEFECT 2 CE-salvage regression:** fail-closed length branch still leaves `result.thinking` for CE salvage.
- **DEFECT 3 derivation:** drive the **live `decision_caller.call()` without `max_tokens`** for MiniMax-M3, assert `max_tokens == derive_output_budget` (not 4000).
- **DEFECT 3 clamp-floor:** small-window reasoning model never drops below `reserve+answer_floor`; compress prompt or fail-closed.
- **Non-regression:** non-reasoning models byte-identical; `answer_floor >= 4000`.
- **End-to-end re-baseline:** re-run L2 (2-backend) with F4 env override **removed**; empty-output stays ≈0 — proving the canonical path, not the band-aids, carries it.

## 6. Risks & Mitigations
- Live token-allocation shift (small-window ratio now applies to all ≤32k models) → re-baseline full suite; keep env knobs.
- Frozen+slots mutation → `dataclasses.replace` only; rebind before policy-derived init; unit-test it.
- Eager resolution cost in `__init__` → keep memoization + try/except degrade.
- `session_continuity.py:689` keeps 128k by design (generic, no binding) — documented, not a regression.
- Stream finalize needs `last_finish_reason` captured first.
- CoT leakage → recovery gated on empty visible output.
- Ceiling collision (M3 8192) → validate `reserve+floor <= ceiling` at config load.
- Window starvation → `derive_output_budget` is window-aware; ties to DEFECT 1 real window.
- Keyword misclassification → explicit config wins.
- 4000→None breadth → `answer_floor>=4000`; non-regression test.
- Self-heal kept as defense-in-depth (only stop hardcoding 8192/32768).

## 7. Out of Scope
`max_active_window_messages` dual-source (follow-up); gemini/codex tag-regex reasoning; F6 telemetry run_id collision; per-task_class multiplier tuning; persisted-snapshot migration; any binding change.
