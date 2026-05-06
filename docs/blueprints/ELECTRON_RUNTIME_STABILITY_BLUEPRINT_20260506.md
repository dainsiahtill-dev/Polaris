# Electron Runtime Stability Blueprint 2026-05-06

## Current Understanding

Polaris is an Electron desktop app with a Python FastAPI backend. The failing
Electron acceptance flow exercises Court -> PM -> Director -> QA. PM is
configured with `pm_runs_director=true`, so the PM workflow already launches
Director and QA.

Observed evidence shows the PM workflow produced successful
`runtime/results/director.result.json` and `runtime/results/integration_qa.result.json`,
but the Playwright flow then entered Director Workspace and clicked the Director
execute button again. That created a second manual execution path and made the
acceptance evidence no longer represent the PM-orchestrated run.

The same run also exposed Windows command execution and runtime storage layout
risks:

- `subprocess.run(["npm", ...], shell=False)` cannot reliably resolve `npm.cmd`
  on Windows unless the executable is resolved explicitly.
- `sync_process_settings_environment()` writes the derived
  `settings.runtime_base` into `KERNELONE_RUNTIME_CACHE_ROOT`, turning a
  computed value into process-global configuration.
- Polaris business storage layout always appends `.polaris/projects` even when
  the configured runtime base already contains `.polaris`.

## Evidence List

- User terminal log: backend raised repeated 429s and NATS durable delete 404.
- Local Playwright evidence: full-chain PM workflow artifacts existed and were
  successful while the test failed in the manual Director phase.
- `src/backend/polaris/kernelone/process/command_executor.py`: command executor
  needed Windows shim resolution and timeout partial-output preservation.
- `src/backend/polaris/cells/storage/layout/internal/settings_utils.py`:
  environment sync wrote `settings.runtime_base` to `KERNELONE_RUNTIME_CACHE_ROOT`.
- `src/backend/polaris/cells/storage/layout/internal/layout_business.py`:
  Polaris runtime root construction lacked the generic resolver's double
  metadata-dir guard.
- `src/backend/polaris/tests/electron/full-chain-audit.spec.ts`: manual Director
  execution after PM auto-orchestration.
- `src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts`: two clicks
  on the Director execute toggle after PM auto-orchestration.

## Defect Analysis

1. Windows command execution failed for Node projects.
   - Trigger: Director verification runs `npm run build` or `npm run test` on
     Windows through `shell=False`.
   - Root cause: unqualified allowlisted executables were not resolved through
     `shutil.which`, so Windows `.cmd` shims were not found.
   - Impact: Director verification failed even when manual `npm run build` and
     `npm test` passed in the target workspace.

2. Runtime root can be nested under a previous runtime root.
   - Trigger: a runtime base already containing `.polaris` is used as the base
     for Polaris business layout.
   - Root cause: Polaris layout code duplicated path construction and missed the
     generic KernelOne guard for metadata-dir-containing bases.
   - Impact: artifacts may appear under
     `<runtime>/projects/<key>/runtime/.polaris/projects/<key>/runtime`.

3. Acceptance tests execute Director twice.
   - Trigger: PM is run with `pm_runs_director=true`; after PM completes, tests
     click `director-workspace-execute`.
   - Root cause: the test conflates "verify Director lineage" with "start a
     manual Director run".
   - Impact: duplicate execution, stale/latest-event confusion, and possible
     Electron page closure while polling the second run.

## Repair Plan

- Keep the Windows command executor fix minimal: resolve allowlisted executable
  names on Windows and preserve timeout stdout/stderr.
- Add a Polaris storage helper that constructs `runtime_projects_root` once and
  avoids double `.polaris` nesting. Reuse it in both `resolve_polaris_roots()`
  and `PolarisStorageLayout`.
- Change settings environment sync to export only explicit runtime roots:
  `runtime.root` to `KERNELONE_RUNTIME_ROOT`, explicit `runtime.cache_root` to
  `KERNELONE_RUNTIME_CACHE_ROOT`. Do not export derived `runtime_base`.
- Change Electron acceptance Director checks to read workflow/Director lineage
  after PM orchestration instead of clicking the manual Director toggle.
- Preserve existing public HTTP and CLI interfaces.

## Test Plan

- Happy path: PM workflow creates Director tasks with `metadata.pm_task_id`; QA
  artifact reaches `integration_qa_passed`.
- Edge cases: runtime base already contains `.polaris`.
- Exception cases: command timeout preserves partial output; Windows command
  shim resolution works without a shell.
- Regression cases: PM auto-orchestration acceptance no longer starts or stops a
  second Director run.

## Rollback Plan

Rollback by reverting the touched files from this change set:

- `src/backend/polaris/kernelone/process/command_executor.py`
- `src/backend/polaris/domain/state_machine/phase_executor.py`
- `src/backend/polaris/cells/storage/layout/internal/settings_utils.py`
- `src/backend/polaris/cells/storage/layout/internal/layout_business.py`
- `src/backend/polaris/tests/electron/full-chain-audit.spec.ts`
- `src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts`
- related pytest files

Post-rollback checks must focus on Windows Node verification, runtime layout
paths, and Electron full-chain evidence lineage.
