# PM Director Execution Mode Alias Fix - 2026-05-31

## Problem

PM run-once can receive Director workflow settings from the desktop service as
`director_workflow_execution_mode=parallel` and `director_max_parallel_tasks=3`.
The PM contract engine uses a different vocabulary:
`director_execution_mode=multi` and `max_directors=3`.

When the bridge does not translate these aliases, the engine treats `parallel`
as invalid and falls back to `single/max_directors=1`. Real Director tasks then
run serially, exceed the E2E SLA, and surface to the user as PM blocked or PM
iteration failed.

## Root Cause

There are two runtime configuration surfaces:

1. UI/service/CLI options use `serial|parallel`.
2. PM contract/engine options use `single|multi`.

The merge path read only `director_execution_mode` and `max_directors`, so
`director_workflow_execution_mode` and `director_max_parallel_tasks` were
ignored.

## Fix Strategy

Normalize both vocabularies at every PM contract boundary:

- `parallel` maps to `multi`.
- `serial` maps to `single`.
- `director_max_parallel_tasks` maps to `max_directors`.
- Payload engine values still take precedence when valid.

This preserves existing contract vocabulary while honoring the desktop
settings that launch PM.

## Verification Plan

- Unit test `EngineRuntimeConfig.from_sources` with desktop-style args.
- Unit test payload normalization accepts `parallel/max_parallel_tasks`.
- Unit test pm_planning pipeline merge preserves `parallel + 3`.
- Run Ruff, format, mypy, and pytest for touched files.
