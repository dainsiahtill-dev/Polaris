# Director Delivery

## Kind

`capability`

## Purpose

Director CLI and terminal console transport surface. Owns the standalone Director CLI
entry point under `polaris/delivery/cli/director/**`.

## Migration Status

⚠️ **SKELETON ONLY — MIGRATION NOT COMPLETED** (status 2026-06-07)

`director_cli.py` has NOT been migrated here. `polaris/delivery/cli/director/`
does not contain a `director_cli.py`; the file still exists in both
`polaris/cells/director/execution/internal/director_cli.py` and
`polaris/cells/director/tasking/internal/director_cli.py`. The current Director
CLI entry point is `polaris/delivery/cli/director/cli_thin.py`.
