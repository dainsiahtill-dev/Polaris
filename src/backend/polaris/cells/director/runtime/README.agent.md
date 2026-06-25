# Director Runtime

## Kind

`workflow`

## Purpose

Intended to own code/patch application, file application, existence gate, and the
repair loop for Director tasks, plus the KernelOne tool chain execution capability
(`polaris/kernelone/tools/`).

## Migration Status

⚠️ **PARTIAL IMPLEMENTATION — MIGRATION NOT COMPLETED** (status 2026-06-25)

`runtime/internal/repair_kernel/` is the cell-private implementation for Director
Repair Kernel contracts, diagnostic normalization, patch composition, policy
gating, transactional execution shell, repair receipts, and non-authoritative
advisory overlay model. Cross-cell callers must use `director.runtime.public`
or `director.runtime.public.service`; they must not import
`director.runtime.internal.repair_kernel`.

The first rule-discovery layer is also owned here. `repair_kernel/registry.py`
defines typed rule metadata and read-only coverage reports so diagnostics can
surface `known_rule_matched=false` as an auditable platform gap before QA timeout
or manual log inspection. Cross-cell callers must use
`query_director_repair_coverage`; coverage reporting is read-only and must not
write files or register new rules implicitly.

Convergence scheduling is owned here as well. `repair_kernel/scheduler.py`
models plan ordering, `priority`, `depends_on`, `round_number`, `max_rounds`,
cycle detection, and post-round verifier snapshots. Language-specific post
repair functions must not hide their own unbounded convergence loops; they must
be migrated behind this scheduler contract.

Repair receipts must close the loop with revalidation evidence. A receipt that
claims an applied deterministic repair should be able to point at the verifier
command, exit code, before/after diagnostics, resolved/residual diagnostic ids,
and errors_before/errors_after/net_error_reduction. This evidence is
authoritative receipt data; AGI advisory notes are projection-only.

Dark-launch comparison is read-only. Use
`compare_director_repair_shadow_run` to compare legacy `tool_results` against
new kernel receipts by changed files and source tools before cutting over a
legacy path. The comparison must not write files or register rules.

The production deterministic repair strategies are still migrated through
`roles.adapters/internal/director/deterministic_repairs/` during cutover. That
directory is a legacy strategy host only: it must not own a repair kernel,
strategy catalog, policy gate, receipt contract, or AGI advisory contract.

Future language coverage must grow through this kernel, not by adding more
branches to `execute_method.py`. Bench agents running L1-L12 or other project
sets may add language-specific strategies for TypeScript, Go, Rust, C++, Java,
Python, shell, SQL, or future scripting languages, but each new repair must
declare a stable `source_tool`, language/archetype metadata, coverage behavior,
receipt projection, and verifier evidence. If a diagnostic is not yet covered,
surface it as `known_rule_matched=false` first; do not add speculative rules
without bench evidence.
Use `query_director_repair_language_slots` to inspect reserved extension slots;
these slots are scaffolding only and do not mean the language has an
authoritative deterministic rule.

Remaining code still lives elsewhere:

- File application / repair: `polaris/cells/director/tasking/internal/file_apply_service.py`,
  `polaris/cells/director/tasking/internal/repair_service.py`
- Patch application / existence gate: module-level functions in
  `polaris/cells/director/tasking/internal/patch_apply_engine.py` and
  `polaris/cells/director/tasking/internal/existence_gate.py` (there are no
  `PatchApplyEngine` / `ExistenceGate` classes).
- The original source also remains in `polaris/cells/director/execution/internal/`.
- Legacy deterministic repair strategy functions remain in
  `polaris/cells/roles/adapters/internal/director/deterministic_repairs/`; those
  modules must remain thin migration shims or strategy hosts until the runtime
  cutover is complete. Any new deterministic repair planning, patch composition,
  receipt projection, strategy catalog, or future AGI advisory contract must be
  added to `polaris/cells/director/runtime/` first and exposed through public
  service functions before legacy callers can consume it.
- The post-execution language path must stay behind
  `roles.adapters/internal/director/post_execution_repair_bridge.py` while
  cutover is incomplete. `execute_method.py`, Factory, QA, and bench harnesses
  must not import language-specific repair functions directly.

AGI/resident advisory is intentionally not part of the Director repair execution
path. Future AGI integration may consume repair diagnostics and receipts to emit
non-authoritative advisory notes or suggested rule patterns only; it must not
write files, register rules, select success, override policy gates, or become
Run Ledger / ReceiptStore truth. Cross-cell callers can inspect the read-only
advisory boundary through `query_director_repair_advisory_policy`.
