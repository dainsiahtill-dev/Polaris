# Electron Full Delivery Batch 4 Blueprint

## 1. Current Understanding

- Polaris is an Electron desktop orchestrator for Architect/Court planning, PM task planning, Director execution, and QA verification.
- Current Electron acceptance can complete Court -> PM -> Director -> QA through real Playwright operation.
- ChiefEngineer and QA still lack first-class clickable UI workspaces; current evidence is mostly projection/artifact based.
- This batch focuses on runtime stability and observability before adding broader UI surfaces.

## 2. Evidence Inventory

- Read key frontend hooks:
  - `src/frontend/src/hooks/useMemos.ts`
  - `src/frontend/src/app/hooks/useUsageStats.ts`
  - `src/frontend/src/hooks/useMemory.ts`
  - `src/frontend/src/lib/queryClient.tsx`
- Read Electron fixtures and tests:
  - `src/backend/polaris/tests/electron/fixtures.ts`
  - `src/backend/polaris/tests/electron/full-chain-audit.spec.ts`
  - `src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts`
- Subagent findings confirmed:
  - `useMemos` can list memos multiple times during cold start because `loadMemoList` depends on `memoSelected`.
  - File reads can be triggered before workspace is ready.
  - Electron fixture auto-dismisses engine failure dialogs without preserving visual evidence.
  - ChiefEngineer/QA UI operation chain is not yet fully clickable.

## 3. Defect Analysis

### Defect A: Memo startup request burst

- Trigger: App mounts `useMemos({ workspace })` while workspace can be empty, then settings later populate workspace.
- Direct cause: `loadMemoList` captures `memoSelected`; selecting the first memo recreates the callback and retriggers the effect.
- Impact: duplicate `/memos/list` and `/files/read` calls during startup, increasing 429 risk.

### Defect B: Visual failure evidence is discarded

- Trigger: Electron shows an engine failure alert dialog.
- Direct cause: fixture periodically clicks the close button and dismisses native dialogs without saving dialog text/screenshot.
- Impact: a failing UI state can be hidden before the audit package records the root symptom.

## 4. Fix Plan

- Update `useMemos`:
  - Treat an explicitly provided empty workspace as not ready.
  - Make `loadMemoList` stable and use functional state updates.
  - Deduplicate memo content reads by `(workspace, memo path)`.
- Add `useMemos` tests for cold-start workspace transition and refresh behavior.
- Update Electron fixture:
  - Save full-page screenshot and text attachment before auto-dismissing visible engine failure dialogs.
  - Save native dialog text before dismissing native dialogs.

## 5. Test Plan

- Happy path: workspace becomes ready, memos list once, first memo content loads once.
- Edge case: explicit empty workspace does not call backend.
- Regression: selecting first memo no longer causes a second `/memos/list`.
- Visual evidence: Electron fixture TypeScript compiles and acceptance still runs.

## 6. Rollback Plan

- Revert:
  - `src/frontend/src/hooks/useMemos.ts`
  - `src/frontend/src/hooks/useMemos.test.tsx`
  - `src/backend/polaris/tests/electron/fixtures.ts`
  - this blueprint
- Re-run:
  - `npm run test -- src/frontend/src/hooks/useMemos.test.tsx --runInBand`
  - `npm run test:e2e:acceptance` with `KERNELONE_E2E_USE_REAL_SETTINGS=1`

## 7. Addendum: LLM Runtime Visual And Binding Defects

### Current Understanding

- The Settings modal can show successful LLM connectivity while the runtime overlay still reports `LLM BLOCK`.
- The full-chain Electron audit can fail before late Director/QA artifacts are written, even when the backend completes shortly afterward.
- PM runtime provider invocation is role-bound, but one cell-local PM port still passed legacy `settings.model` as a fallback model.

### Evidence Inventory

- User screenshot showed:
  - `required: pm, director, qa` and `blocked: pm, director, qa` while the UI was otherwise idle.
  - raw structured JSON fragments in the "实时推理流" list.
  - generic `LLM 配置保存失败` despite provider tests passing.
- Runtime artifacts from the failed Electron run appeared after the test timed out:
  - `runtime/results/director.result.json`
  - `runtime/results/integration_qa.result.json`
- Runtime events contained a PM provider error referencing the stale legacy model from global `settings.json`.
- Code evidence:
  - `llm.evaluation.internal.index._global_index_path(workspace_path)` incorrectly treats the workspace as the global root.
  - `CellPmInvokePort.invoke()` passes `state.model` to `invoke_role_runtime_provider(... fallback_model=...)`.
  - `compute_llm_config_sync_updates()` updates `pm_model` but leaves legacy `model` stale.

### Defect Analysis

1. LLM readiness false block:
   - Trigger: active workspace differs from Polaris global config root.
   - Root cause: LLM test index lookup uses `workspace/.polaris/config/llm/llm_test_index.json` as the first "global" candidate, so global UI test results are invisible to `/llm/status`.
   - Impact: overlay and PM diagnostics can report blocked roles after successful tests.

2. Stale fallback model:
   - Trigger: role-bound provider primary invocation fails or retries while `settings.model` still contains an old legacy model.
   - Root cause: fallback model comes from legacy state instead of the same role binding source of truth.
   - Impact: PM may retry a configured MiniMax provider with a stale ModelScope/Ollama model and produce misleading provider errors.

3. Runtime event noise:
   - Trigger: stream event candidates are all structured JSON fragments.
   - Root cause: frontend filtering falls back to the unfiltered candidate list when filtering removes every item.
   - Impact: users see `}`, `"error": ""`, and timestamp fragments as live reasoning events.

4. Electron audit false timeout:
   - Trigger: real-settings chain exceeds the fixed 5-minute Director artifact timeout.
   - Root cause: timeout was tuned for deterministic fixture mode, not real LLM/provider latency.
   - Impact: audit can fail while backend completes successfully shortly afterward.

5. Recovered PM invoke error remains fatal:
   - Trigger: PM role provider invocation fails, then deterministic requirements fallback successfully creates Director tasks and returns exit code 0.
   - Root cause: the PM planning cell persists `last_pm_error_code=PM_LLM_INVOKE_FAILED`, while the orchestration facade replaces the empty task contract with fallback tasks but does not clear or downgrade the fatal PM state.
   - Impact: Electron can show a blocking "PM 运行异常" dialog after the chain has recovered and continued.

### Fix Plan

- Resolve LLM test index global path through KernelOne global config storage, keeping workspace-local index as a secondary compatibility path.
- Sync legacy `settings.model` from the configured PM role model when saving LLM config.
- Remove stale `state.model` fallback from `CellPmInvokePort`; use the role-bound model as the only runtime model source.
- Filter structured runtime fragments even when all candidate events are low-signal.
- Increase Electron Director artifact timeout and include richer runtime status context on timeout.
- Downgrade `PM_LLM_INVOKE_FAILED` to `PM_LLM_FALLBACK_APPLIED` only when requirements fallback recovered the iteration to exit code 0.

### Test Plan

- Unit: LLM index loading must prefer app-global index even when workspace is a separate project directory.
- Unit: LLM config sync must update both `pm_model` and legacy `model`.
- Unit: PM invoke port must not pass stale `state.model` as runtime fallback.
- Unit: recovered PM invoke errors must be persisted as warnings and unrelated PM errors must remain fatal.
- Frontend: overlay must show no JSON fragments when every recent event is a structured fragment.
- E2E: rerun `npm run test:e2e:acceptance` with real settings.

### Rollback Plan

- Revert this addendum and the following files if regressions appear:
  - `src/backend/polaris/cells/llm/evaluation/internal/index.py`
  - `src/backend/polaris/cells/llm/provider_config/internal/settings_sync.py`
  - `src/backend/polaris/cells/orchestration/pm_planning/internal/pipeline_ports.py`
  - `src/backend/polaris/delivery/cli/pm/orchestration_engine.py`
  - `src/backend/polaris/tests/test_pm_zero_tasks_fallback.py`
  - `src/frontend/src/app/components/LlmRuntimeOverlay.tsx`
  - `src/backend/polaris/tests/electron/full-chain-audit.spec.ts`
