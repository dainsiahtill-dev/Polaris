# Director Runtime

## Kind

`workflow`

## Purpose

Intended to own code/patch application, file application, existence gate, and the
repair loop for Director tasks, plus the KernelOne tool chain execution capability
(`polaris/kernelone/tools/`).

## Migration Status

⚠️ **SKELETON ONLY — MIGRATION NOT COMPLETED** (status 2026-06-07)

`runtime/internal/` currently contains only `__init__.py` (an empty skeleton); this
Cell owns no real implementation yet. The actual code still lives elsewhere:

- File application / repair: `polaris/cells/director/tasking/internal/file_apply_service.py`,
  `polaris/cells/director/tasking/internal/repair_service.py`
- Patch application / existence gate: module-level functions in
  `polaris/cells/director/tasking/internal/patch_apply_engine.py` and
  `polaris/cells/director/tasking/internal/existence_gate.py` (there are no
  `PatchApplyEngine` / `ExistenceGate` classes).
- The original source also remains in `polaris/cells/director/execution/internal/`.
