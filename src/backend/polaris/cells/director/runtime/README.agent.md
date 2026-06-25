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

The production deterministic repair strategies are still migrated through
`roles.adapters/internal/director/deterministic_repairs/` during cutover. That
directory is a legacy strategy host only: it must not own a repair kernel,
strategy catalog, policy gate, receipt contract, or AGI advisory contract.

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

AGI/resident advisory is intentionally not part of the Director repair execution
path. Future AGI integration may consume repair diagnostics and receipts to emit
non-authoritative advisory notes only; it must not write files, select success,
override policy gates, or become Run Ledger / ReceiptStore truth.
