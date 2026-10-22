# Native visual working set implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement the single integrated task below. ROOT owns Git integration and acceptance.

**Goal:** Let Director and independent QA continue useful visual engineering without resending every previously consumed screenshot or losing access to historical comparisons.

**Architecture:** Existing roles.runtime owns capture provenance and reinspection; existing KernelOne owns resolved-model input/output admission. Select optional derived image sets in request projections only. Preserve the single durable engineering journal, all native text/call/result pairs, original pixels, host permissions, and current own-role image acceptance.

**Tech stack:** Existing Python roles.runtime, KernelOne LLM engine, SQLite engineering sessions, KFS, native provider capture, process/browser sandbox.

**Spec:** The sealed `builder-check.json` and ten-file manifest under `/home/dains/Documents/polaris-bench-projects/original120-20261023-v4/L1-01/.polaris/audits/root-qa-context-growth/`. That report is design/evidence, never runtime authority. Source baseline: `fd6e56b6846013726d0eabed56d5aa56fc1efea0`.

## Global constraints

- Work on main; no branches/worktrees. No generated Bench target edits, dependency installs, substitute checks or receipts. No main/NATS restart.
- Every shell segment uses RTK; CodeGraph first; UTF-8 text; temporary/cache/audit files in the owning project's `.polaris` through KFS.
- Keep the current native/history failure immutable. Last actual body had 71 images; failed preparation had 73, with no physical send established. Actual vision tokens and billed cost remain UNKNOWN.
- Keep configured per-model output ceilings and all existing hard budget/safety/deadline settings. No arbitrary image ceiling, retry/timeout increase, base64-to-zero accounting or native history trimming.
- No new Cell/controller/ledger/authority; no engineering_session schema migration. Long-term memory excluded.
- Preserve latest eligible current own-role images, unseen images not already proven stale by the existing complete binding rule, explicit pins/reinspection selections, original user/reference/unattributed images. Select whole timed capture sets. Unknown sends do not prove delivery. Existing T21 may first omit proven-stale pixels; explicit pins/reinspection override that omission while preserving original freshness.
- Reinspection uses exact original bytes and bindings from this session/workspace/role. It must not manufacture a capture, freshness, or approval. Policy still applies.

## Review focus

1. Auxiliary preparation or failed/unknown sends must not mark images delivered: test actual versus auxiliary evidence, cold reconstruction and unknown no-replay.
2. Three or more nonadjacent captures must remain recoverable together: test exact-byte reinspection after omission, not fresh recapture.
3. Conflicting reinspection/capture arguments and corrupt/cross-role references must fail without browser/process effects or new candidate facts.
4. Input selection and output clamp must use the same admitted payload basis in invoke and streaming; real visual cost stays unknown.
5. Current-image approval must remain role/frame/candidate-bound after reinspection, verification and cold restart.

## Task 1: One integrated optional-image projection and readback boundary

**Files (exclusive implementer ownership):**

- `src/backend/polaris/cells/roles/runtime/internal/engineering_visual.py`
- `src/backend/polaris/cells/roles/runtime/internal/engineering_loop.py`
- `src/backend/polaris/cells/roles/runtime/internal/engineering_roles.py`
- `src/backend/polaris/cells/roles/runtime/public/engineering_service.py`
- `src/backend/polaris/kernelone/llm/engine/prompt_budget.py`
- `src/backend/polaris/kernelone/llm/engine/_executor_base.py`
- `src/backend/polaris/kernelone/llm/engine/executor.py`
- `src/backend/polaris/kernelone/llm/engine/stream/executor.py`
- Conditional minimal plumbing only: existing `native_request_capture.py`, `engineering_tools.py`.
- Existing visual projection/service/ordering tests plus new `test_engineering_visual_reinspection.py`; existing K1 prompt-budget/native-visual/output-ceiling/Anthropic-parity tests. One cohesive helper module in the existing owner is allowed if it keeps these existing large files focused; disclose its exact interface before integration.

**Interfaces:** Existing `build_visual_messages` classifies trusted origins. Existing `browser_observe` accepts mutually exclusive `revisit_call_ids: array[string]`. Host-only selection metadata must not leak into the provider schema. Existing K1 model resolution and `TokenBudgetManager` supply budget; one pure selection/cost helper is shared by input admission and output clamp. No role-owned token budget or model-maintained business IDs.

- [x] Write RED tests for prior delivered image growth, exact nonadjacent reinspection, cold restore, mandatory overflow, failed/unknown delivery, current visual rejection and invoke/stream consistency. Record the precise failures against the unchanged baseline.
- [x] Preserve the immutable recorded 73-image frontier as read-only evidence. A bounded offline reconstruction must keep text/native pairs and original fact hashes unchanged. Do not fabricate successful journal events or rerun the old native run.
- [x] Implement required/optional whole-set classification from exact existing durable facts. Delivery requires matching later same-role actual native request hashes and successful request outcome. Preserve current original freshness rules.
- [x] Implement deterministic post-resolution K1 selection, preferring recent optional sets within existing hard allowance. Optional soft room also respects existing configured output ceiling/window/overhead/reserve. Mandatory content may use existing output clamp; required-only overflow still fails explicitly.
- [x] Separate text estimate, image serialization proxy and UNKNOWN actual vision cost. Keep the existing conservative proxy initially, label its limits, and use one selected-payload basis for admission and output. Removing eligible pixels is the only image-cost reduction in this repair.
- [x] Implement exact read-only same-session/role `browser_observe` reinspection, with mutually exclusive capture arguments, original integrity checks, current policy and no browser spawn. Persist only references through existing tool/journal records; do not copy original image bytes into another fact store. Pending selections remain required until proven delivered; cold restore reconstructs them.
- [x] Make original capture IDs/frames/entry/actions/times and omission/reinspection reasons discoverable without fabricated visual descriptions. Keep original reference blocks and all tool result text/pair ordering.
- [x] GREEN: finite actual service/SQLite/browser/sandbox plus loopback scripted-provider fixture. Capture multiple distinct states, force optional-budget selection, request nonadjacent old sets and inspect exact bytes in the actual native body. Restore in a new process; retain current independent QA and unknown no-replay controls. Scripted model is a fixture and does not prove semantic quality.
- [x] Run affected native/visual/budget/recovery checks, Ruff/format/mypy on changed scope. Record commands, exits, frozen sources, RED/GREEN, original-data immutability and remaining limits in `.polaris/audits/native-visual-working-set-20261023/implementation/report.json`.
- [x] Independent scoped review with diff and evidence; fix relevant blockers. ROOT verifies diff and runs combined relevant checks plus existing required KernelOne release gates. ROOT owns staging/commit; implementer never commits.

## Stop and return to Bench

Stop mechanism work when eligible optional payload alone crosses the recorded frontier, exact multi-state reinspection/cold restoration works, native pairs/mandatory images/approval/no-replay controls remain intact, affected checks and finite review pass. Then freeze a new candidate and start a fresh original catalog case through the formal isolated entry; never hot-patch or rewrite v4. ROOT personally evaluates real project quality and resource closure before the next serial case. These module results alone do not constitute a delivery.

## ROOT decisions and remaining program

The user explicitly authorized autonomous reversible implementation and rejected repeated approval questions; implement directly on main after this finite design check. Choosing budget-driven optional selection rather than a fixed latest-two cap preserves arbitrary comparisons. A conservative serialization proxy can still overreject; it is not actual visual usage or a proven bound.

ROOT ruling during implementation: preserve T21's existing complete-binding stale omission before classifying unseen mandatory images. Initial blanket wording conflicted with unchanged T21 synthetic and production semantics. Stale omission does not prove delivery; original facts/text remain, and explicit pins or exact reinspection restore old pixels without refreshing their binding. Cost if wrong: a model may first encounter metadata rather than an unseen but already stale image and must request reinspection to compare it. Current71-image same-candidate failure is unaffected. Keep old T21 assertions, and check unseen-current/unknown-binding retention plus stale explicit reinspection/pinning.

The full original120 remains the product objective. Evaluate original planning/CE blueprints and task dispatch/claim/dependency semantics, Scout/consultation, ContextOS, continuous repair, durability/recovery, visual independent QA, and resource/model-cost observability against actual project failures. Existing storage/legacy migration work remains queued; none becomes an unrelated prerequisite for the next Bench.

## Scoped implementation qualification

The frozen12-file implementation passed244 affected tests,11-file strict typing and12-file Ruff/format. ROOT independently ran96 adjacent consumer tests and18 required architecture tests; KernelOne ALL passed all12 stages,368 tests passed and1 existing skip. Independent spec/quality review found no blockers. Actual scripted-provider fixtures exercise service/SQLite/browser/sandbox/native HTTP/SSE and new-process restoration; they do not establish remote-model comprehension or Bench delivery.

At the immutable73-image failure frontier, the final selection retained42 images and omitted14 optional complete capture sets. Final admission proxy591998 is below existing soft604440 and hard947464;584704 is an earlier intermediate result retained separately. Original tool text/native pairs and all ten sealed evidence hashes remained unchanged. Actual vision tokens and billed cost stay UNKNOWN.

ROOT source/review/check evidence is in main `.polaris/audits/native-visual-working-set-20261023/`. No new Cell, state owner, real-time channel or persistent schema was added, so graph/catalog/descriptor generation is not required for this scoped implementation. The existing browser tool gains read-only historical reinspection; its schema, dispatcher, projection, budget and cold consumer were migrated together. Existing shared storage/sandbox migration debt remains outside this fix.

Component qualification applies to this reviewed implementation only. Fresh original120 native evidence is required for delivery; prior v4 interrupted/quality-failed facts remain immutable.
