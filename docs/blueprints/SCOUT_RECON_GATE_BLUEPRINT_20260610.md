# Scout Recon-Gate Blueprint (2026-06-10)

**Status:** PROPOSED (structural — touches the single-commit transaction kernel, ADR-0071).
Requires a Verification Card before execution per `src/backend/AGENTS.md §8.6`.

## 1. Problem

The transaction kernel guarantees the **write side** of grounded action: a
`MATERIALIZE_CHANGES` turn cannot finalize a `FINAL_ANSWER` without a write
receipt (`_handle_final_answer`, `turn_transaction_controller.py:2068`, the
`must_materialize` block). There is **no symmetric read-side guarantee**: a
read-only reconnaissance role (`scout`, `context_policy.recon_mode: true`) can
emit a final answer having executed **zero** read/search tools — i.e. an
*ungrounded recon answer*.

Observed: `scout_l2_code_search_read` and several detective cases scored on
answers produced with `observed_tools=none`. The benchmark already fails these
via the critical `scout_min_recon` validator, and two cases were fixed at the
case/prompt/fixture level (see `scout-benchmark-matrix` memory). But nothing in
the **engine** prevents an ungrounded recon answer in production scout use; the
guarantee currently lives only in the test fixtures.

## 2. Goal

Make "a recon-mode role must actually perform recon before answering" a
**structural engine invariant**, mirroring the proven `must_materialize` block —
not a per-case judge check.

## 3. Architecture (text)

```
TurnDecision == FINAL_ANSWER
        │
        ▼
_handle_final_answer(decision, state_machine, ledger)         turn_transaction_controller.py:2055
        │
        ├─ [EXISTING] must_materialize gate (write side) ......:2068
        │     if delivery_contract.must_materialize
        │        and not mutation_obligation.mutation_satisfied
        │        and not is_refusal:
        │            BLOCK → NO_WRITE_TOOL_AVAILABLE
        │
        └─ [NEW] recon gate (read side) ......................:after 2068
              if delivery_contract.recon_required
                 and not _recon_satisfied(ledger)
                 and not is_refusal:
                     # invariant-safe DRIVE, not a hidden continuation
                     bootstrap = retry_orchestrator.execute_read_bootstrap_batch(...)   # :977/:986
                     if bootstrap performed ≥1 recon tool:
                         re-enter finalize with the recon receipts in context
                     else:
                         BLOCK → NO_RECON_PERFORMED   (new BlockedReason)
```

### 3.1 Recon-satisfied predicate

`_recon_satisfied(ledger)` = any entry in `ledger.tool_executions` whose
canonical tool name ∈ the recon set
`{repo_rg, repo_tree, repo_read_head, repo_read_slice, repo_read_tail,
repo_read_around, repo_symbols_index, read_file, scout_probe}` executed
**successfully**. Reuse the same recon-tool set already defined in
`unified_judge._SCOUT_RECON_TOOLS` / `_SCOUT_READ_FILE_TOOLS` (lift to a shared
constant so judge and kernel agree — single source of truth).

### 3.2 Threading `recon_required` into the contract

`recon_required` is derived once, where the `DeliveryContract` is built
(`turn_transaction_controller.py:1261/1366`), from the active role profile's
`context_policy.recon_mode` (already on `RoleContextPolicy`, schema.py) — the
same signal `RoleContextGateway._recon_mode_active()` reads. No new config
surface; one new boolean field on `DeliveryContract`, defaulting `False` so
every non-recon role is byte-for-byte unchanged.

## 4. Invariant safety (ADR-0071)

- The gate adds **no** second `TurnDecision` and **no** hidden continuation.
  `execute_read_bootstrap_batch` is the *existing* read-bootstrap primitive
  (`:977`) already used inside the single-commit envelope; the bootstrap batch
  is a `ToolBatch` (`len(ToolBatches) <= 1` preserved — the recon turn that
  reaches the gate ran zero batches, so the bootstrap is the first and only one).
- The block path is a literal mirror of `must_materialize`: transition to
  `COMPLETED`, `ledger.finalize()`, emit a failed `CompletionEvent`, return a
  `no_recon_performed` turn result. One-shot — guard with a ledger flag so the
  gate fires at most once per turn (no loop).
- **Refusal exemption** identical to the write gate (`REFUSAL_MARKERS`): a role
  legitimately refusing ("不能/拒绝…") is not forced to recon.

## 5. Blast radius

- **Scoped to `recon_mode` roles only** (today: `scout`). `recon_required`
  defaults `False`; pm/architect/chief_engineer/director/qa contracts are
  unchanged. Env override `KERNELONE_SCOUT_RECON_MODE` already exists for
  gray-rollout (gateway.py).
- Files: `turn_transaction_controller.py` (gate + contract field),
  `transaction/*constants*` (new `BlockedReason.NO_RECON_PERFORMED`,
  shared recon-tool set), `DeliveryContract` definition. No judge/case changes.

## 6. Verification

1. Unit: `_recon_satisfied` truth table over `ledger.tool_executions`
   (zero tools → False; failed-only recon tool → False; one successful
   `repo_rg` → True; non-recon tool only e.g. `edit_blocks` → False).
2. Unit: non-recon role contract has `recon_required is False` (parity — pm/ce/
   director/qa turns take the identical pre-change path).
3. Kernel: a `recon_mode` turn that finalizes with zero recon either gets a
   bootstrap recon batch injected, or is blocked `NO_RECON_PERFORMED`; a turn
   that already ran `repo_rg` finalizes normally.
4. Matrix (CLI, isolated k≥3): scout scorecard stability class for the flaky
   detective/map cases does not regress; no previously-passing case flips to
   blocked.
5. Gates: `ruff`/`mypy`/`pytest` on touched kernel modules; the
   single-commit-invariant architecture tests stay green.

## 7. Why this is the keystone (not the only fix)

The two `stable_fail` cases were resolved without this gate (prompt de-leak +
fixture multi-line signature + director recon nudge). The remaining 3 `flaky`
cases are model variance at the 0.7 threshold — the **pass-rate/median**
scorecard (already built) is their correct treatment, not the gate. This gate's
value is a **production correctness floor**: scout can never emit an ungrounded
recon answer, independent of any benchmark. It is defense-in-depth, symmetric
with `must_materialize`, and gated to zero blast radius on other roles.
