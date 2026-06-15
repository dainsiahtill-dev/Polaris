# Deterministic file-assembly protocol (skeleton + constrained fills + merger)

> Blueprint (§4.1). Direction set by codex review 2026-06-15: for a single file
> built across N steps (skeleton + fills), make global coherence a SYSTEM-level
> merge guarantee, not a weak-model memory burden. **Do NOT globally raise
> `_MAX_STEP_LINES`** — that is a capacity escape hatch, not a correctness fix
> (it just moves the wall to 500/800-line files and re-bets on a one-shot large
> write). Keep cap=120; >120 files decompose, but via a VERIFIABLE protocol.

## 1. 问题 (Problem)

A code file > `_MAX_STEP_LINES (120)` is fissioned into `skeleton + fill1..fillN`
(step_splitter). The weak Director writes each fill in a SEPARATE turn and must
re-remember the file's interface every turn — so the fills disagree (different
imports/exports, renamed functions, drifted DOM ids), the file fails `verify`,
and the whole cluster dead-letters (`INTERFACE CONFLICTS` — live L2-11 app.js
skel+fill1..fill6, L2-08 across parents). order-1's suppression now keeps
≤120-line sole-writer files whole; this blueprint fixes the >120 path.

## 2. 解法 (Solution — codex 5-point protocol)

Turn skel/fill from a prompt-level convention into a **verifiable file-assembly
protocol** where the skeleton's interface is LAW and fills are constrained
patches a deterministic merger applies:

1. **Cap stays 120.** ≤120 sole-writer single-file leaf = one coherent step
   (already landed). >120 enters decomposition — but no free-text skel/fill.
2. **Skeleton = interface law.** The skel step must emit the COMPLETE file shell:
   imports, exports, DOM/API contract, global-state structure, every function
   signature as a stub, and an ANCHOR per fillable region.
3. **Fills = constrained patches, not whole-file writes.** Each fill carries
   `target_file + anchor_id / function_name + expected_signature + allowed_region`.
   A fill may ONLY fill its anchor's body. Forbidden: changing imports/exports,
   function signatures, public constants, DOM ids, or event-binding interfaces.
   An interface-changing fill → fail/re-ask, NEVER a silent dead-letter pile-up.
4. **Deterministic merger.** The skel file is the baseline; fills write ONLY
   through the merger, which rejects out-of-bounds edits, duplicate anchors,
   missing anchors, and interface drift. The weak model does LOCAL implementation
   only; the SYSTEM owns global consistency.
5. **One-shot (A) is a gated escape hatch ONLY** — never a global cap raise.
   Allowed only when ContextOS/Scheduler judges window + output budget + model
   capability all sufficient AND the file is low-interface-complexity. Default
   path is B.

## 3. 三处联动 (3-way coordination)

- **`step_splitter.py` (the contract):** `_split_one` emits, per fill,
  `anchor_id` (the function symbol), `expected_signature` (the skel's declared
  signature), and `allowed_region` (anchor marker). The skel step gets a
  `file_shell_required` flag + the full signature/interface set.
- **CE fission contract / context gateway (the skel directive):** render a hard
  skel directive — "emit the COMPLETE file shell: all imports/exports, signatures
  as stubs, and a `// @anchor:<name>` marker per function body; implement NOTHING".
  Render a fill directive — "implement ONLY the body between `@anchor:<name>`
  markers; do not touch any signature/import/export/DOM-id".
- **Director materialization (the merger):** before applying a fill, validate it
  touches only its `allowed_region` and its `expected_signature` is unchanged;
  apply via the merger against the skel baseline; after applying, run a
  lightweight AST/regex contract verify; on interface drift → re-ask (forced,
  scoped), not dead-letter.

## 4. 增量 (Phased increments — land + gate each)

- **P1 — contract data (step_splitter):** emit `anchor_id` / `expected_signature`
  / `allowed_region` per fill + `file_shell_required` on the skel. Pure +
  testable; no behaviour change until P2/P3 consume it. ← START HERE.
- **P2 — skel/fill directives (context gateway):** render the shell-required skel
  directive + the anchor-scoped fill directive from the P1 contract.
- **P3 — deterministic merger + interface validator (Director materialization):**
  the load-bearing piece — anchor-region merge + drift rejection + forced re-ask.
- **P4 — A escape hatch (gated):** ContextOS/Scheduler-gated one-shot for
  low-interface-complexity large files; default stays B.

## 5. 验证 (Verification)

Per increment: ruff + mypy + pytest (fail-closed). Live: replay the L2-11 app.js
fill chain (the canonical >120 incoherence case) — expect 0 `INTERFACE CONFLICTS`,
app.js materializes coherently, the file `verify` passes. Forensics via
`market_forensics.py --log`.

## 6. 边界 (Out of scope / separate)

- Cross-PARENT conflicts (multiple PM tasks writing the same file — L2-08
  PM-0001-1/-2/-3) are the file-ownership ledger's job (F8), not this protocol.
- F15 abandoned-daemon-thread shutdown crash (`_enter_buffered_busy`) is a
  separate harness-cleanup fix.
