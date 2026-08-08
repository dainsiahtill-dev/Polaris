# Blueprint: M03 Tool-Denial Delivery-Fatal (control_plane:tool_result_failed structural)

Date: 2026-08-07
Status: Proposed (Blueprint First, per AGENTS.md §10.1; structural per §8.6)
Author: Claude (autonomous round m03-r16)
Attribution: `M03_tool_batch_deo` / `control_plane:tool_result_failed` / `DELIVERY_FAILED`
Supersedes: none. Relates to in-flight WIP R192 (same-path collapse), R182 (attribution clarity), R193 (edit_file search-miss no-wipe).

## 1. Problem (machine-measured)

L1-01 isolated bench (m03-r16, factory_783bf9cf824a, 2026-08-07):
`0/1 passed`, `chain_exit=1`, `canonical_execution=FAIL run_ledger_integrity_failed`,
`real_run_gate=FAIL build_test_lint_ran`, `delivery_status=DELIVERY_FAILED`.

Top-level residual signature **stable r15→r16** (`control_plane:tool_result_failed`),
but the **sub-failure mode drifted inside M03**:

| Round | Specific denial | Emitter |
|-------|-----------------|---------|
| r15 | `deo_director_policy_denied` ("Command blocked: matches dangerous pattern") | `execution_tools.py:1090` via `is_command_blocked` `_SHELL_META_RE` on `node --version && ls … \| head -40` |
| r16 | `deo_tool_normalization_failed` + dropped tool call | `directed_effect_policy_snapshot.py:1165/1239` (WIP-touched); edit_file search-miss / no-content (R193 deliberate no-wipe) |

## 2. Root Cause (structural)

Any **single** Director tool call that is denied (policy or normalization) is projected
by the Run Ledger tool-lifecycle as `TOOL_RESULT_FAILED`, which breaks
`canonical_execution` (run-ledger integrity), which forces `DELIVERY_FAILED`.
There is no isolation between a single per-tool denial and the whole delivery outcome,
and no corrective re-ask for arg-shape/normalization denials.

Concretely the denial classes that currently kill the delivery:
1. Compound verification commands blocked by `_SHELL_META_RE` (`&&`/`|`/`>`/`<`).
2. `edit_file` search-miss / no-content-body → `deo_tool_normalization_failed` (R193, deliberate to prevent empty-wipe; explicitly tested).
3. Any future per-tool denial in `_validate_write_policy` `except` → `deo_tool_normalization_failed`.

The Run Ledger thereby **mis-attributes model-quality issues** (bad command shape, bad
search string) as **control-plane integrity failures**. This violates the goal's
"Run Ledger 投影必须区分 missing/failed modalities; 不要把 failed 写成 missing"
spirit extended to: do not let a per-tool model error break control-plane integrity.

## 3. Architecture (text diagram)

```
Director LLM -> native tool call (e.g. edit_file)
   |
   v
ToolBatchExecutor (M03) -> DEO policy snapshot (directed_effect_policy_snapshot.py)
   |                          |-> _validate_write_policy: edit_file search-miss
   |                          |     -> raise ValueError  (R193, prevents wipe)        [FATAL]
   |                          |-> except -> deo_tool_normalization_failed             [FATAL]
   |                          |-> command tools: is_command_blocked _SHELL_META_RE    [FATAL]
   |                          v
   |                     snapshot denial (allowed=False, error_code=...)
   v
Run Ledger tool-lifecycle (M08) -> TOOL_RESULT_FAILED
   v
canonical_execution gate -> run_ledger_integrity_failed
   v
DELIVERY_FAILED  (one bad tool call killed the whole delivery)
```

Desired (target-state):

```
per-tool denial
   |
   v  (case A: arg-shape / search-miss / recoverable)
corrective re-ask within turn (return tool_error to model; model retries)  -- stays out of ledger
   |
   v  (case B: genuinely unrecoverable / exhausted re-ask budget)
record per-call failed tool result (M08) WITHOUT breaking canonical_execution integrity
   v
real_run_gate / product-quality gate catches the actual product defect (separate plane)
```

## 4. Module Responsibilities (ownership-respecting)

- `roles.kernel.internal.transaction.tool_batch_*` (M03, WIP area): decide per-tool
  recoverable-vs-fatal; route recoverable arg-shape denials to corrective re-ask.
- `roles.adapters.internal.director.directed_effect_policy_snapshot` (M03, WIP area):
  keep R193 no-wipe guarantee; distinguish "search-miss (recoverable)" from
  "malformed args (recoverable)" vs "genuine policy denial (deny)".
- `kernelone.tool_execution.security` (KernelOne primitive; consumed by M03):
  `_SHELL_META_RE` blanket-blocks compound commands. Either (a) prompt-side: Director
  verification contract forbids compound commands (one command per call), or
  (b) execution-side: safe compound-command support (split `&&`/`;`, sandboxed).
  NOTE: this primitive is shared (broad blast radius); option (a) preferred.
- `cells.control_plane.run_ledger` (M08, FORBIDDEN this round): tool-lifecycle
  projection must distinguish "single tool failed (per-call, recoverable/product)" from
  "integrity broken (fatal)". Requires its own module gate round.
- `factory.pipeline.internal.bench_gates` (measure only): unchanged — must NOT be
  altered to mask the failure.

## 5. Fix Design (two layers, gated sequencing)

**Layer 1 — Recoverable per-tool denial isolation (M03, primary fix).**
A single tool-call denial with a recoverable shape (edit_file search-miss, arg-shape
normalization, command-shape) must NOT break canonical_execution integrity. Mechanism:
corrective re-ask (extend ADR-0090 re-ask machinery) feeding a precise tool_error back to
the model within the turn budget; only after re-ask budget exhaustion does the per-call
result land in the ledger as a single failed tool (not integrity-breaking).

**Layer 2 — Compound-command policy (decide prompt-side vs execution-side).**
Prompt-side preferred: Director verification/delivery contract forbids compound shell
commands (one executable per execute_command). Keeps the security primitive intact.

**Non-goal:** revert R193. R193's no-wipe guarantee is correct and tested; the fix
changes the *consequence* of the denial (recoverable, not fatal), not the no-wipe guard.

## 6. Verification Plan (TDD, per superpowers:test-driven-development)

RED (failing tests, before any production change):
1. `test_edit_file_search_miss_does_not_break_canonical_execution`: a turn whose only
   tool call is an edit_file search-miss yields a per-call recoverable outcome
   (re-ask or single failed tool), NOT `canonical_execution=run_ledger_integrity_failed`,
   NOT delivery-fatal. (Current WIP: fails → DELIVERY_FAILED.)
2. `test_compound_verification_command_recoverable`: Director verification command with
   `&&`/`|` is handled (prompt-forbids OR safely split) without delivery death.

GREEN: implement Layer 1 (re-ask for recoverable denials) + Layer 2 (prompt contract).
REFACTOR: keep R193 no-wipe tests green; update R193 assertion from
"deo_tool_normalization_failed fatal" to "recoverable, file preserved".

Gate sequence: ruff/mypy -> M03 module gate -> cascade (M01-M10) -> one isolated L1-01 bench.

## 7. Risks & Boundaries

- **Spans M03 + M08.** M08 (run-ledger integrity projection) is the actual canonical_execution
  decider and is FORBIDDEN in the m03-r16 attribution. Layer 1 must be implemented so a
  recoverable denial stays OUT of the ledger (re-ask) to avoid touching M08 this round;
  else this needs a dedicated M08 round.
- **Active WIP conflict.** R192/R193/R182 (Hermes/openhands) are in flight in
  `tool_batch_executor.py`, `directed_effect_policy_snapshot.py`. Coordination required;
  WIP stale >20h at m03-r16 (no live concurrent edit observed).
- **Blast radius.** `kernelone.tool_execution.security._SHELL_META_RE` and
  `kernelone.tool_execution.security.is_command_blocked` are shared primitives; relax
  only via option (a) prompt-side, or a sandboxed execution-side path with security review.
- **Model ceiling.** Even with the control-plane fix, L1-01 COMPLETED_VERIFIED requires the
  Director to emit building TypeScript (r16 product had TS1110 etc.). M3 milestone
  (COMPLETED_VERIFIED xN>=3) is multi-round and may touch model capability, not just platform.

## 8. Sequencing / Authorization

This blueprint + Verification Card `vc-20260807-m03-tool-denial-delivery-fatal.yaml`
authorize the next round(s):
- Round A (M03): implement Layer 1 recoverable-denial isolation (re-ask) + Layer 2 prompt
  contract; TDD; module gate + cascade.
- Round B (M08, separate attribution): if Layer 1 alone insufficient, make run-ledger
  tool-lifecycle distinguish per-call-failed from integrity-broken.
- Round C: isolated L1-01 bench; re-attribute; confirm control-plane no longer dies on a
  single tool denial. Product TS quality addressed in parallel (separate bucket, M10/model).

Per goal discipline: seal unaffected; no thrash of sealed surfaces; bench remains measure-only.
