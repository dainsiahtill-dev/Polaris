# Director Planning

## Kind

`workflow`

## Purpose

Director task planning, main execution loop, risk/quality tracking, and context gathering.
Owns DirectorAgent, execution rules, and logic utilities.

## Migration Status

⚠️ **COPIED, NOT MIGRATED** (status 2026-06-07)

The implementation was COPIED from `polaris/cells/director/execution/internal/`, not
moved: `director_agent.py`, `context_gatherer.py`, and `director_logic_rules.py` still
remain in `polaris/cells/director/execution/internal/`, so the two locations are
duplicated. Public contracts and directory structure are established, but the migration
out of `execution/internal/` is not complete.
