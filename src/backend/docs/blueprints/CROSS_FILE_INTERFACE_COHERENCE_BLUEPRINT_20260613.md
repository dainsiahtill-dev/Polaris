# Cross-File Interface Coherence (组合律 operationalized) — 20260613

## Result
Close the dominant r14 root cause: **cross-parent / cross-file interface drift**.
Two PM tasks that legitimately share files (a base task + an enhancement task)
are fissioned by the CE in isolation, so each invents its own interface
identifiers for the same artifact. Downstream this manifests as QA collisions,
edit-reluctance deaths, and — worst — a **non-running product** whose steps all
"resolved".

## r14 forensic evidence (L2-12, classic brick-breaker, one game)
PM decomposition (correct): `PM-0001-1` = bootstrap+core game (index.html,
style.css, main.js, readme.md); `PM-0001-2` = levels+restart+docs (main.js,
readme.md, index.html), `depends_on=[PM-0001-1]`.

CE fissioned the two parents independently:
- `1-S1` index.html declares `id=game/score/lives/message/hud`.
- `2-S1` index.html declares `id=gameCanvas/restartBtn` (different names, same DOM).
- `1-S3` main.js calls `getElementById('game')`.

Observed in the final archived artifacts (`L2-12_runs/r14/artifacts`):
- index.html ends with 2-S1's ids (`gameCanvas`, `restartBtn`); 1-S1's
  `game/score/lives/message` are **gone** → 2-S1's edit clobbered 1-S1.
- main.js still calls `getElementById('game')` while index.html exposes
  `id="gameCanvas"` → the canvas lookup returns null. **The game does not run.**

Death chain: 2-S1 rewrites index.html → 1-S1's QA verify (`grep id="game"`)
fails at acceptance → bounce to exec → file already exists with "wrong" content
→ edit-reluctance → `EXEC_NO_EVIDENCE` → dead_letter; parents reconciled to
dead_letter. Local per-file `grep` verify is structurally **blind** to the
interface between files — every step passed its own clauses; the composition
was broken.

Secondary timeline fact: `PM-0001-2` claimed `pending_design` and fissioned
*before* `PM-0001-1`, because the design-stage claim ignores parent
`depends_on` (the readiness gate only fires at `pending_exec`). So even a
populated ledger would have been read in the wrong order.

## Theory mapping
- 组合律 (assume-guarantee / composition): a shared artifact needs ONE interface
  contract; independent fission breaks the assume-guarantee between producer and
  consumer steps. The fix establishes that contract as a frozen, reused ledger.
- 预防律 (syntactic prevention over prompt): hand the weak executor a coherent
  blueprint so a wrong-name edit is never asked for, rather than detecting drift
  after it lands and thrashing on the bounce.
- "良好的组织架构" reframe: the laborer (Director) cannot be blamed for an
  incoherent blueprint; the organization (CE fission + market ordering) must
  hand down a coherent interface so the laborer simply follows it.

## Fix (three changes)

### F1 — design-stage parent ordering (`task_market/internal/service.py`)
New `_design_claim_ready(item, items)` mirroring `_exec_claim_ready`: a
`pending_design` parent is not claimable while any `depends_on` parent is still
in `pending_design`/`in_design`. Composed into `_select_claim_candidate`'s
candidate filter alongside `_exec_claim_ready`. Both gates are stage-scoped
(return True off their stage), so they compose. Terminal/orphan deps do not
block (no hang). Guarantees a producer parent fissions before a consumer parent
reads the ledger.

### F2 — interface ledger (`kernelone/quality/interface_ledger.py`, NEW)
Workspace-scoped, language-agnostic ledger of declared interfaces per file,
persisted at `runtime/contracts/interface_ledger.json`:
- `record_declared_interfaces(workspace, cache_root, steps)` — accumulate each
  step's `target_file → {identifiers (=interface_names), signatures, declared_by}`
  (first-writer-wins per name; new names append).
- `read_declared_interfaces(workspace, cache_root, target_files)` — union of
  declared identifiers/signatures for a parent's target files.
- `render_assume_contract(declared)` — Chinese instruction block injected into
  the CE fission prompt: "these files already expose these public identifiers;
  REUSE them exactly, add new elements with new names, never rename existing
  ones."

Wired into `ce_consumer.CEConsumer`:
- `_run_step_fission` reads the ledger for the parent's target_files and appends
  the assume-contract to the fission message before invoking the CE role.
- `_claim_and_process_one` records the parent's fissioned steps into the ledger
  after the CE step gate passes (before publish).

Language-agnostic by construction: the ledger only ever carries the CE's own
declared `interface_names`/`signatures` (no HTML/JS-specific parsing), honoring
the "no business code in Polaris" rule.

### F3 — clause-diagnosis cap (`kernelone/quality/step_verify.py`)
`_MAX_DIAGNOSABLE_CLAUSES` 12 → 24. r14 `1-S3` carried 15 cheap `grep`/`test -f`
obligations on a single ≤120-line file and silently lost clause-level
punch-list/teaching. 24 matches `_MAX_STEPS_PER_TASK`; per-clause re-runs stay
bounded by the existing 10 s clause timeout.

## Data flow
```
PM publish parents (with depends_on)
   │
   ▼  pending_design  ── _design_claim_ready gate (F1) ──┐ producer first
CE claim parent ─ read_declared_interfaces (F2) ─ inject assume-contract
   │                                                      │
   ▼  CE role fission (cloud) → steps                     │
   ▼  step gate passes → record_declared_interfaces (F2) ─┘ ledger grows
   ▼  publish leaf steps → pending_exec  (existing _exec_claim_ready)
```

## Risks & boundaries
- Ledger is read-modify-write JSON; safe under the inline sequential consumer
  model used by the market chain. A concurrent multi-worker CE pool would need
  file locking — noted, not implemented (out of scope; mainline-full is inline).
- F1 only orders parents that carry an explicit `depends_on` edge. Two parents
  sharing a file with NO edge remain a PM-planning race (future safeguard: synth
  an order from creation time or flag at publish). r14's parents have the edge.
- Ledger reuse is prompt-level guidance to a weak cloud CE; it raises coherence
  probability, it is not a grammar-level guarantee. QA/punch-list remain the
  backstop.

## Verification
- New unit tests for the ledger (record/read/merge/render) and the design gate.
- `_MAX_DIAGNOSABLE_CLAUSES` regression in `test_step_verify.py`.
- ruff + mypy + full pytest green.
- Held-out generalization: re-run L2-11 (no L2-11-specific change) and confirm
  the fixes reduce interface-drift deaths on an unseen project (H2).
