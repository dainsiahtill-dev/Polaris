# Director Delivery

## Kind

`capability`

## Purpose

Director CLI and terminal console transport surface. Owns the standalone Director CLI
entry point under `polaris/delivery/cli/director/**`.

## Migration Status

⚠️ **THIN ENTRYPOINT ONLY — MIGRATION PARTIAL** (status 2026-06-30)

The supported Director CLI entry point is
`polaris/delivery/cli/director/cli_thin.py`.
Do not restore the old execution-internal CLI shim. Internal tasking CLI helpers may exist under
`polaris/cells/director/tasking/internal/`, but product and script entrypoints
must route through delivery CLI or the public Director execution contracts.
