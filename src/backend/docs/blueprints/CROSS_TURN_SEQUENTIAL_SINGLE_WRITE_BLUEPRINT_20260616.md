# Cross-Turn Sequential Single-Write (2A) — Blueprint

- Date: 2026-06-16
- Status: DESIGN (implementation held — see §9 Dependencies)
- Owner role: Director execution layer (`cells/roles/adapters/internal/director`)
- Related: `l4-multifile-megabatch-wall`, `write-convergence-multimodal`, `batch-b1-intrafile-overfission`,
  ADR-0071 (TransactionKernel single-commit), `REPAIR_MODE_CROSSFILE_COHERENCE_BLUEPRINT_20260616` (#54)

---

## 0) One-paragraph thesis

A complex L4+ task is sometimes planned as **one step that must materialize many files in a single
tool batch**. The weak Director (qwen3.6-27b-int4) writes file #1 cleanly, then its attention/format
fidelity decays across the long batch; the batch arrives truncated/malformed, parse yields **0 valid
writes**, and the step dead-letters with **0 files on disk** (factory-bench L4-19: 0 files ×3
dead-letter). The fix is **not** to make CE re-plan and **not** to rebuild the executor: it is to let
the Director satisfy a multi-file step **one complete file per turn across turns**, using the
filesystem as external memory. This already happens cleanly for well-decomposed steps (L5-28 landed
9 files, L5-29 landed 14, Director 3/3). 2A extends that proven path to the mega-batch step on the
**failure path only**, so the L2 floor (single-file steps) is inert.

---

## 1) Why this is architecturally correct (not a workaround)

ADR-0071 / `src/backend/CLAUDE.md §8.2` already fixes the turn contract:

```
per turn:  len(TurnDecisions) == 1   AND   len(ToolBatches) <= 1   AND   hidden_continuation == 0
```

The platform therefore **forbids** a hidden in-turn loop that secretly streams 5 files; the *only*
sanctioned way to produce N files is **N turns, one batch each**. The mega-batch ask
("emit all 5 write_file calls now") fights this contract by cramming N files into one batch and
betting the weak model holds format coherence to the end. 2A makes the Director's behavior match the
contract instead of straining against it. This is the load-bearing floor-safety argument: 2A moves
toward the canonical model, not away from it.

---

## 2) Scope discriminator — the two opposite granularity walls

There are **two** granularity failures and 2A must help one without feeding the other:

| Wall | Symptom | Unit | 2A relationship |
|------|---------|------|-----------------|
| **Too coarse** — mega-batch (L4-19) | one step → many files in one batch → 0 files | whole declared file | **2A target** |
| **Too fine** — intra-file over-fission (b1) | CE splits ONE file into skel+fill1..fillN steps → weak Director can't assemble one file across turns | sub-file fragment | 2A must **not** reinforce |

**Hard design constraint:** 2A's unit of work is **exactly one complete declared target file per
turn**. It keys on `_missing_declared_target_files(task, workspace)` (whole files named by the task
contract), never on sub-file fragments. If a step's "targets" are fragments of a single file (b1),
2A must detect single-file-multi-fragment and decline (that case is task #49 territory:
skel-law + constrained-fill + merger, not 2A).

---

## 3) Where it lives (insertion point)

Primary surface: `cells/roles/adapters/internal/director/execute_method.py`, the existing
progress-aware repair loop:

- `requires_fresh_materialization = _task_requires_fresh_materialization(task)`  (~L710)
- repair loop: `for repair_attempt in range(1, _QUALITY_REPAIR_ATTEMPT_HARD_CAP + 1):`  (~L1354)
- gap source of truth: `_missing_declared_target_files(task, _adapter_workspace)`  (L5085)

Today the loop drives `_apply_deterministic_materialization_quality_repairs` — **deterministic
template synthesis** (only repairs template-able file types; this is also where the §8
project-specific synthesizers lived and were removed). For a mega-batch step that produced 0 files,
deterministic synth cannot author the non-templatable files, so the step still dead-letters.

2A adds, **between** the main LLM flow and deterministic repair, a **single-file LLM re-ask driver**:

```
main LLM flow  →  [2A: while missing declared targets AND step is multi-FILE:
                       re-ask Director for EXACTLY ONE complete file = next missing target,
                       commit it (one batch / one turn), refresh workspace state]
               →  deterministic repair (unchanged backstop)
```

This is one-file-per-turn "look at blueprint → write one file → breathe → write next", scoped to the
repair path.

---

## 4) Core data flow

```
task (declares targets: A.py, B.py, C.py, D.py, E.py)
  │
  ├─ main flow: pin [mode:materialize] (F31), ask for materialization
  │     └─ weak Director emits mega-batch → truncated → 0 valid writes → 0 files
  │
  ├─ 2A driver (NEW, failure-path-only):
  │     missing = _missing_declared_target_files(task, ws)   # [A,B,C,D,E]
  │     guard: len(missing) >= 2  AND  missing are distinct FILES (not fragments of one)
  │     for target in missing (bounded by a per-step turn cap):
  │         re-ask LLM: "Write EXACTLY ONE complete file now: {target}.
  │                      Other files already on disk: {materialized}. Do not rewrite them."
  │         → single write_file → one batch / one turn → commit → refresh workspace
  │         re-evaluate missing; stop when empty or no-progress
  │
  └─ deterministic repair (unchanged) for any residual template-able gaps
```

Each iteration is a real turn obeying `ToolBatches <= 1`. The filesystem carries state between
iterations (external memory), so the model never holds >1 file in working attention.

---

## 5) Module responsibilities

- **`_missing_declared_target_files`** (exists): ground-truth gap list. Reused as-is.
- **`_single_file_materialization_reask` (NEW)**: build a one-file instruction for `target`, invoke the
  standard LLM flow with a single-target contract, return the write result. Reuses the existing
  forced-write/`_execute_standard_llm_flow` plumbing — does **not** introduce a new tool path
  (CLAUDE.md §7.1: no new tool integration).
- **`_step_targets_are_distinct_files` (NEW)**: the b1 guard — returns False when the declared targets
  collapse to a single physical file (fragments), so 2A declines and defers to task #49.
- **repair loop (modified)**: when `requires_fresh_materialization` and `len(missing) >= 2` and
  `_step_targets_are_distinct_files`, drive the single-file re-ask **before** deterministic synth.

---

## 6) Floor-safe argument (must hold before keep — HARD RULE)

1. **Failure-path only.** 2A activates only when, after the main flow, `len(missing) >= 2`. On the L2
   floor every step has 0–1 declared target files → guard never fires → byte-identical behavior.
2. **Multi-distinct-file only.** The b1 guard (`_step_targets_are_distinct_files`) prevents 2A from
   firing on single-file-multi-fragment steps, so it cannot worsen the over-fission wall.
3. **Architecturally convergent.** Each 2A iteration is one batch / one turn (ADR-0071 §8.2). It
   reduces hidden_continuation pressure rather than adding it.
4. **Bounded.** A per-step turn cap (reuse `_QUALITY_REPAIR_ATTEMPT_HARD_CAP` semantics, e.g. cap at
   `min(missing, N)`) plus the existing no-progress break prevents runaway re-asks / budget burn.
5. **No deterministic answer-baking.** The re-ask is LLM-authored per file; 2A adds **no** templated
   file content (stays §8-clean, unlike the removed synthesizers).

**Keep/revert rule:** like F21/F22/F25, this is a core execution-path change → it is **provisional
until it passes an L2-floor regression (L2-07..12 standard int4 binding, expect ≥ the current 4/6↔6/6
band with no new dead-letter / budget / symbol-drift audit findings)**. If the floor regresses, revert.

---

## 7) Acceptance commands

```bash
# Unit (new + touched)
pytest src/backend/polaris/cells/roles/adapters/tests/ -q -k "single_file or sequential or declared_target"

# Gates (fail-closed)
ruff check src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py --fix
ruff format src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py
mypy src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py

# L2 floor regression (MANDATORY before keep) — standard int4 binding, L2 held-out set
python src/backend/scripts/factory_bench/run_factory_bench.py \
  --project-ids L2-07 L2-08 L2-09 L2-10 L2-11 L2-12 --work-dir <ws>
#   PASS criterion: no regression vs current floor (≥ same qa_passed band, 0 new audit root causes)

# Target validation — the mega-batch case 2A is for
python src/backend/scripts/factory_bench/run_factory_bench.py --project-ids L4-19 --work-dir <ws>
#   SUCCESS signal: L4-19 materializes its declared files (was 0 ×3 dead-letter) — even if QA still
#   fails on functional quality, file-landing going 0 → N is the 2A win.
```

---

## 8) Known gotchas

- **Diagnostics:** use `logger.warning`, NEVER `print()` — factory-bench swallows stdout (F30 lesson).
- **Workspace measurability:** the gap check needs a real workspace, not `"."`. F32
  (`_resolve_materialization_workspace`) already fixes this; 2A must reuse the resolved workspace, not
  raw `config.workspace`.
- **Case-insensitive existence:** `_missing_declared_target_files` already matches case-insensitively
  (F26/F19); do not re-introduce a case-sensitive `.is_file()` check or 2A will loop on
  `readme.md` vs `README.md`.
- **b1 collision:** if `_step_targets_are_distinct_files` is wrong (treats fragments as files), 2A will
  fight the merger path. Bias the guard conservative — when uncertain, decline (return False).
- **Stochasticity:** the write-convergence wall is multi-modal + random (same project dies differently
  per run). A single green L4-19 run is necessary but not sufficient; require the L2 floor to hold and
  ≥2 L4-19 runs landing files before claiming the wall is down.
- **§6.6 canonical gate:** 2A must not rewrite raw tool names; it only adds an instruction + reuses the
  existing write path.

---

## 9) Dependencies / sequencing (why implementation is held)

1. **`execute_method.py` is contended** — a concurrent agent has uncommitted #54 W1
   (`_missing_unresolved_relative_import_target_files`) in the working tree. 2A edits the same file.
   **Wait for that commit, then implement on top** (2A and W1 are complementary: W1 fills
   import-referenced missing files; 2A fills declared-target missing files one-per-turn).
2. **Backends are contended** — concurrent live L2-11 bench. The mandatory L2-floor regression (§7)
   needs the int4 backends free. Implement when the shared run completes.
3. **task #49** (skel-law / constrained-fill / merger) owns the single-file-multi-fragment case; 2A
   explicitly defers to it via the b1 guard rather than overlapping.

---

## 10) Relationship to Gemini's "2A / segmented punch-card" framing

Gemini's mechanism (mega-batch suffocation → file-by-file across turns, filesystem as external
memory) is correct and is exactly the L4-multifile-megabatch wall. Two refinements from live data:

- Cross-turn multi-file **already works** when steps are small (L5-28: 9 files, L5-29: 14). 2A is
  therefore a **narrow failure-path driver**, not a whole-executor rebuild.
- The discriminator is per-**step** file count, not per-**project**; and the opposite wall (b1
  over-fission) means "one file per turn" must mean one *complete* file, never one *fragment*.

---

## 11) Ruled-out alternative — batch-level salvage (proven moot, 2026-06-16)

A tempting non-contended fix would be: when a mega-batch contains `[valid write_A, prose/no-op write_B]`,
commit A and reject only B instead of failing the whole batch (this would live in the *uncontended*
`tool_batch_executor.py` / `contract_guards.py`, not `execute_method.py`). **It does not apply.**
Verified at `tool_batch_executor.py:942`:

```python
if requires_mutation and not tool_batch_has_authoritative_write_invocation(invocations):
    raise RuntimeError("single_batch_contract_violation: mutation requested but no write tool invocation ...")
```

L4-19's signature is exactly this branch — the batch has **zero** authoritative write invocations
(the weak Director emitted reads/prose under mega-batch pressure), so there is nothing valid to
salvage. The lever is therefore unavoidably upstream: make the Director **emit one valid write**,
which is the message/instruction/forced-write path in `execute_method.py` (the 2A-1 single-file
re-ask). Conclusion: **2A-1 is the sole lever; there is no floor-safe non-contended shortcut.**
Implementation stays held on the concurrent #54 W1 commit + the mandatory L2-floor gate.
```
