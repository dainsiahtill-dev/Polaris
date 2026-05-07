# Electron Stability Follow-up Optimization Blueprint - 2026-05-07

## 1. Current Understanding

Polaris is an Electron + FastAPI + runtime WebSocket desktop product. The last repair batch made the application launchable and made PM, Director, and Chief Engineer workspaces operational. The remaining follow-up optimization scope is not feature expansion for a target project; it is platform reliability and observability hardening for Polaris itself.

Current module responsibilities:

- Electron E2E tests live under `src/backend/polaris/tests/electron/` and are driven by `playwright.electron.config.ts` plus npm runner scripts.
- Runtime event streaming is delivered by `polaris.delivery.ws.endpoints.websocket_loop` and in-process fanout from `polaris.infrastructure.realtime.process_local.message_event_fanout`.
- PM, Director, and Chief Engineer workspaces render runtime artifacts and task contracts from frontend React components under `src/frontend/src/app/components/`.
- Python and frontend test configuration live in `pyproject.toml`, `pytest.ini`, `.gitignore`, Vitest setup, and package scripts.

Current problem hypotheses:

- Real LLM full-chain Electron tests exist, but they are not exposed as a clear nightly/CI profile with deterministic settings seeding.
- Local fanout events are still legacy-shaped even though JetStream events are `runtime.v2`; this keeps parsing compatibility scattered across frontend code.
- PM/Director/Chief Engineer views show real fields, but users still cannot always see where a field came from.
- Temporary pytest directories and test warning noise make git/test output harder to audit.

## 2. Evidence List

Read files:

- `package.json`
- `playwright.electron.config.ts`
- `infrastructure/scripts/run-electron-acceptance-e2e.mjs`
- `src/backend/polaris/tests/electron/fixtures.ts`
- `src/backend/polaris/tests/electron/test_e2e_runner_scripts.py`
- `src/backend/polaris/delivery/ws/endpoints/websocket_loop.py`
- `src/backend/polaris/infrastructure/realtime/process_local/message_event_fanout.py`
- `src/backend/polaris/infrastructure/messaging/nats/nats_types.py`
- `src/frontend/src/app/hooks/runtimeParsing.ts`
- `src/frontend/src/app/hooks/useRuntime.ts`
- `src/frontend/src/app/hooks/useRuntimeStore.ts`
- `src/frontend/src/app/components/pm/PMDocumentPanel.tsx`
- `src/frontend/src/app/components/director/DirectorTaskPanel.tsx`
- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx`
- `.gitignore`, `pytest.ini`, `pyproject.toml`

Confirmed call chains:

- Real flow E2E: npm script -> Node runner -> Playwright config -> Electron fixture -> real settings gate.
- Runtime file activity: MessageBus `FILE_WRITTEN` -> `RuntimeEventFanout` -> `_drain_fanout_events` -> WebSocket payload -> frontend `useRuntime` -> `useRuntimeStore`.
- PM docs provenance: PM docs service -> `PMDocumentPanel` tree/content render.
- Director provenance: `/v2/director/tasks?source=...` -> fallback task merge -> `ExecutionTask` -> `DirectorTaskPanel`.
- Chief Engineer provenance: runtime tasks/workers/PM state -> `ChiefEngineerWorkspace`.

Unconfirmed information:

- Whether production CI secrets already contain usable real LLM settings. This must remain a required external prerequisite, not a code assumption.

## 3. Defect Analysis

1. Real LLM E2E profile is under-specified.
   - Trigger: CI/nightly or local operator wants to run real PM/Director flow.
   - Root cause: existing acceptance runner requires env gates but does not seed settings or provide a named nightly profile.
   - Impact: tests are skipped or difficult to run consistently.

2. Runtime event schema is not uniform.
   - Trigger: local fanout emits `file_edit`, `task_trace`, and `seq.*` messages outside the `runtime.v2` envelope.
   - Root cause: JetStream and process-local fanout evolved separately.
   - Impact: frontend needs scattered compatibility logic and diagnostics lack stable event metadata.

3. Provenance is incomplete in workspaces.
   - Trigger: user inspects PM/Director/Chief Engineer state and needs to know whether it came from docs, local tasks, workflow, runtime workers, or PM state.
   - Root cause: source fields are carried in metadata but not consistently surfaced.
   - Impact: debugging still requires opening JSON artifacts manually.

4. Test output contains avoidable local temporary-directory noise.
   - Trigger: git status or search touches generated pytest cache directories with restricted permissions.
   - Root cause: local temp/cache patterns are not fully ignored.
   - Impact: audit output is noisy and can mask real failures.

## 4. Fix Plan

Minimal changes:

- Add a dedicated real-flow Electron runner that can seed UTF-8 settings from `KERNELONE_E2E_SETTINGS_JSON(_BASE64)` and always forces `KERNELONE_E2E_USE_REAL_SETTINGS=1`.
- Add npm script and GitHub nightly workflow using that runner.
- Add runtime v2 metadata to process-local fanout WebSocket messages while keeping legacy `type` and `event` fields for compatibility.
- Extend frontend file edit event typing/parsing to preserve `schemaVersion`, `sourceChannel`, and `eventKind`.
- Add compact provenance chips/rows to PM, Director, and Chief Engineer surfaces.
- Ignore generated pytest temp/cache directories and add pytest `norecursedirs` entries.

Non-goals:

- Do not change external LLM provider contracts.
- Do not rewrite WebSocket protocol or remove legacy compatibility.
- Do not invent PM/Director/Chief Engineer data.
- Do not modify any target project code.

Compatibility:

- Existing WebSocket consumers continue receiving legacy `type=file_edit` and `event` fields.
- New schema fields are additive.
- E2E real-flow runner fails fast when settings are missing instead of silently skipping.

## 5. Test Plan

Happy path:

- Real-flow runner dry-run returns expected specs and settings bootstrap metadata.
- Runtime fanout file edit payload carries `runtime.v2` metadata and frontend parses it.
- PM/Director/Chief Engineer components render provenance markers from real props.

Edge cases:

- Runner dry-run without settings still succeeds for command preview but reports settings source as missing.
- Frontend parser accepts legacy file edit events without schema metadata.
- Empty PM docs still render a truthful empty state.

Exception cases:

- Invalid settings JSON should fail the runner before launching Playwright.
- Missing real settings in non-dry-run should exit non-zero.

Regression cases:

- Existing Electron acceptance runner path tests remain valid.
- Existing runtime file edit tests continue passing.
- Existing PM/Director/Chief Engineer UI tests continue passing with provenance assertions.

## 6. Rollback Plan

Rollback is file-local:

- Remove `infrastructure/scripts/run-electron-real-flow-e2e.mjs`.
- Revert `package.json` script additions and `.github/workflows/electron-real-flow-nightly.yml`.
- Revert additive schema fields in `websocket_loop.py`, `useRuntimeStore.ts`, and `runtimeParsing.ts`.
- Revert provenance UI additions in PM/Director/Chief Engineer components and related tests.
- Revert `.gitignore` and `pytest.ini` ignore additions.

Behaviors to recheck after rollback:

- Electron smoke E2E still launches.
- Runtime file edit events still display in Director.
- PM/Director/Chief Engineer pages still show no invented data.
