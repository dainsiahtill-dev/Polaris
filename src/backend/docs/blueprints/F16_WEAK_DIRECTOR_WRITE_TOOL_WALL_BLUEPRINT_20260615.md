# F16 — Weak-Director write-tool wall: early forced-write for from-scratch creates

> Blueprint (§4.1). Root cause located 2026-06-15 from batch b1 + b2 (solo control) forensics.
> Cell: `roles.kernel` (transaction retry path). ADR-0090 escalation ladder tuning — no new mechanism.

## 1. 现象 (Symptom)

Held-out L2-12 (brick-breaker) finishes at **0 runnable products** under BOTH ~6 req/backend
(b1) and ~1 req/backend (b2 solo). The dominant failure in both is
`single_batch_contract_violation` (b1 #1=64, b2 #1=10). **Saturation is an amplifier, not the
floor — the write-tool wall is the floor, and even solo cannot clear it.**

## 2. 根因 (Root cause — proven by the raw LLM tool calls)

The weak qwen Director, in implementing phase, **explores instead of writing**, and the
single-batch mutation contract correctly forbids that. The escalation ladder then burns up to
**4 retry LLM calls** before it forces a write — and that budget burn trips the circuit breaker
on harder steps → dead-letter. Verbatim from `L2-12_market_b2.log` (step-2-css, step-3-readme):

```
attempt=1  raw_native_tools=['execute_command','execute_command','execute_command']  → REJECTED
attempt=2  raw_native_tools=['execute_command','execute_command','execute_command']  → REJECTED
attempt=3  escalate tools=['execute_command','write_file'] tool_choice=auto
           raw_native_tools=['execute_command','execute_command','execute_command']  → REJECTED  (still picks explore!)
attempt=4  escalate tool_choice={function: write_file}  ← FORCED BY NAME
           raw_native_tools=['write_file']  ✓ finally emits
```

Two compounding gaps in `resolve_retry_escalation` (`retry_orchestrator.py`):

- **G1 — forced-write gated to the LAST attempt only** (`attempt_index >= max_retry_attempts - 1`,
  line 311). For a from-scratch *create* the model never self-emits the write tool, so attempts
  1–3 are pure waste.
- **G2 — `execute_command` is offered in the "write-only" escalated set** (it is the verification
  tool, kept by `include_verification_tools`), so the auto rung (attempt 3) still lets the model
  pick the exact tool the contract guard then rejects.

`select_retry_forced_write_tool_name` **already computes `creation_mode`** (any target file missing
→ forces `write_file`, line 564) but discards that signal — it only decides *which* tool to force,
never *when*.

A second, downstream wall (**Wall 2**, `director_no_materialized_changes`, b2=4) is when the forced
write emits but the content is empty/non-changing. Out of scope for F16 (separate fix); F16 only
removes Wall 1 and frees budget for the existing bootstrap-followup / shrink-gate machinery to
handle Wall 2 with calls to spare.

## 3. 修复 (Fix — ADR-0090 ladder tuning, behaviour-preserving for non-creates)

Thread the already-computed `creation_mode` signal into the escalation schedule:

- New pure helper `detect_creation_mode(workspace, target_files) -> bool` (extracted from the inline
  block in `select_retry_forced_write_tool_name`, which now reuses it — DRY, no behaviour change).
- `resolve_retry_escalation(..., force_write_immediately: bool = False)`:
  - creation: escalation phase starts at **index 1** (keep retry attempt 1 / index 0 as the free
    exploration shot) and the named write tool is **forced from index 1** onward.
  - non-creation: unchanged (`_ESCALATION_START_ATTEMPT_INDEX = 2`; force only at
    `max_retry_attempts - 1`).
- `resolve_retry_temperature_override(..., force_write_immediately: bool = False)`: same forward
  shift so the low-temp "transcription" phase stays aligned with the forced phase.
- Call site (`retry_tool_batch_after_contract_violation`): compute `from_scratch_create =
  detect_creation_mode(workspace, target_files)` once and pass it to both resolvers.

**Effect:** worst-case retry LLM calls for a from-scratch create drop 5 → 3 (main + free retry +
forced retry), keeping `single_batch_contract_violation` per turn at ≤1 — below the circuit-breaker
trip threshold that was dead-lettering the step.

## 4. 正确性与边界 (Correctness / boundaries)

- **Non-create steps (edits to existing files) are byte-for-byte unchanged** — `force_write_immediately`
  defaults False; every existing `resolve_retry_escalation` test passes unmodified.
- Forcing `write_file` by name for a create is safe: the destructive-shrink gate + `no_materialized`
  detection already guard a bad/empty write (Wall 2).
- Keeping retry attempt 1 (index 0) free preserves one genuine exploration shot (the model
  occasionally emits a correct write_file there, avoiding the low-temp forced path entirely).
- Does NOT touch the market, consumers, providers, or the single-commit transaction invariant.

## 5. 验证 (Verification)

- Unit: extend `test_retry_api_escalation.py` — `force_write_immediately=True` forces the named tool
  at index 1 and keeps index 0 free; non-creation schedule unchanged; `detect_creation_mode`
  missing/existing/empty-target cases. Gate: ruff + mypy + pytest (fail-closed).
- Live: re-run L2-12 solo on the real chain; expect `single_batch_contract_violation` per-turn ≤1,
  no circuit-breaker trip on the css/readme steps, and the step to reach the write (then Wall 2 is
  the next target). Forensics via `market_forensics.py`.
