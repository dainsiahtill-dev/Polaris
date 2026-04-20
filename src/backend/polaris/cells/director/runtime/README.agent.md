# Director Runtime

## Kind

`workflow`

## Purpose

Code/patch application, file application, existence gate, and repair loop for Director tasks.
Owns PatchApplyEngine, FileApplyService, ExistenceGate, RepairService, and the
KernelOne tool chain execution capability (`polaris/kernelone/tools/`).

## Migration Status

✅ **MIGRATION COMPLETED** (2026-04-09)

Implementation migrated from `polaris/cells/director/execution/internal/`.
Public contracts and directory structure are established; migration tracked
in the parent `director.execution` Cell task.
