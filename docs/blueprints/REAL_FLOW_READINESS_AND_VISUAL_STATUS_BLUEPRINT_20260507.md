# Real Flow Readiness And Visual Status Blueprint 2026-05-07

## Current Understanding

Polaris desktop uses Electron + Vite for the renderer and a FastAPI backend for runtime, role workspaces, LLM status, and orchestration. The real-flow E2E runner seeds an isolated `KERNELONE_HOME` before launching Playwright against the Electron app.

The latest failed real-flow evidence shows PM never produced tasks while the runtime overlay reported LLM readiness blocking during active planning. Separate visual evidence showed an impossible `PM Active 493440h9m` duration and compact status text rendering structured fragments such as `}`.

## Evidence

- `infrastructure/scripts/run-electron-real-flow-e2e.mjs` seeded `settings.json` and `llm_config.json`, but did not seed `config/llm/llm_test_index.json`.
- `src/backend/polaris/cells/runtime/projection/internal/llm_status.py` builds blocked roles from `policies.required_ready_roles` and `load_llm_test_index(settings)`.
- `src/backend/polaris/cells/llm/evaluation/internal/index.py` resolves the global readiness index at `config/llm/llm_test_index.json` under `KERNELONE_HOME`.
- Playwright visual evidence showed PM running with zero tasks and LLM blocked during planning.
- `src/frontend/src/app/App.tsx` converted numeric `started_at` through `new Date(value).getTime() / 1000`, which misreads epoch seconds as 1970 milliseconds.
- `src/frontend/src/app/components/ControlPanel.tsx` displayed raw compact runtime labels without filtering structured stream fragments.

## Defects

1. Seeded real-flow runs can silently omit LLM readiness evidence.
   - Trigger: real-flow uses seeded LLM config with required roles, but no matching `llm_test_index.json`.
   - Root cause: runner seeded configuration but not the readiness index consumed by backend status projection.
   - Impact: PM/Director may appear broken after a long E2E timeout instead of failing fast with a clear readiness-seed error.

2. Runtime status bar can display impossible active durations.
   - Trigger: backend returns epoch seconds and frontend treats the number as epoch milliseconds.
   - Root cause: process timestamp contract is not normalized at the frontend boundary.
   - Impact: visual trust loss and noisy diagnostics.

3. Compact runtime labels can show JSON stream fragments.
   - Trigger: streaming status emits partial structured fragments while no human-readable task/tool label exists.
   - Root cause: compact header labels did not share the overlay's low-signal filtering rule.
   - Impact: UI shows `{`, `}`, `"summary": {}` as operational status.

## Fix Plan

- Extend the real-flow runner to accept `KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64`, `KERNELONE_E2E_LLM_TEST_INDEX_JSON`, or `KERNELONE_E2E_LLM_TEST_INDEX_PATH`, and to copy an existing local host readiness index for local seeded runs.
- Add a pre-launch readiness validation: if seeded LLM config requires ready roles, the seeded index must mark those roles `ready=true`, otherwise fail before launching Electron.
- Normalize process start timestamps through a shared frontend utility that supports epoch seconds, epoch milliseconds, and ISO timestamps while rejecting accidental 1970-era values.
- Filter structured runtime fragments from compact ControlPanel labels.

## Non-Goals

- Do not change backend LLM readiness semantics.
- Do not bypass required role readiness in real-flow.
- Do not alter PM/Director public APIs.
- Do not modify target workspace project files.

## Test Plan

- Python regression tests for the real-flow runner:
  - Happy path: seed settings, LLM config, and readiness index.
  - Exception path: required roles without readiness index fail before Electron launch.
  - Privacy path: dry-run output does not print secret values or local paths.
- Frontend unit tests:
  - Timestamp normalization for seconds, milliseconds, ISO, and accidental 1970 values.
  - ControlPanel filters structured fragments.
  - RealTimeStatusBar does not show huge durations for invalid normalized timestamps.
- E2E:
  - Run visual Electron Playwright spec and inspect screenshot/console failures.
  - Run `npm run test:e2e`.
  - Run seeded `npm run test:e2e:real-flow` with real settings and readiness index.

## Rollback Plan

Revert these files if needed:

- `infrastructure/scripts/run-electron-real-flow-e2e.mjs`
- `src/backend/polaris/tests/electron/test_e2e_runner_scripts.py`
- `src/frontend/src/app/utils/runtimeDisplay.ts`
- `src/frontend/src/app/utils/runtimeDisplay.test.ts`
- `src/frontend/src/app/App.tsx`
- `src/frontend/src/app/components/ControlPanel.tsx`
- `src/frontend/src/app/components/ControlPanel.test.tsx`
- `src/frontend/src/app/components/RealTimeStatusBar.tsx`
- `src/frontend/src/app/components/__tests__/RealTimeStatusBar.test.tsx`

After rollback, re-run the same targeted tests and the visual Electron E2E to confirm behavior returns to the previous baseline.
