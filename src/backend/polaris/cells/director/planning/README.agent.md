# Director Planning

## Kind

`workflow`

## Purpose

Director task planning, main execution loop, risk/quality tracking, and context gathering.
Owns DirectorAgent, execution rules, and logic utilities.

## Migration Status

⚠️ **PARTIAL MIGRATION** (status 2026-06-30)

`director_logic_rules.py` is owned here and the old execution-internal logic
shim has been removed. `director_agent.py` and `context_gatherer.py` still have active
execution implementations in `execution/internal/`; do not copy them again or
restore deleted compatibility modules. Public contracts and directory structure
are established, but the migration out of `execution/internal/` is not complete.
