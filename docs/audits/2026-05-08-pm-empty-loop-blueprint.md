# PM Empty Loop Blueprint

## Current Understanding

- Polaris is the meta tool platform; target workspaces must stay outside this repository.
- The observed PM page "empty loop" is a compound failure surface:
  - the real PM run can terminate with `PM_LLM_INVOKE_FAILED`;
  - the old Electron real-flow test kept waiting for `/state/snapshot.tasks > 0`;
  - the PM workspace did not render a persistent terminal/error banner or block PM startup when required LLM roles were not ready.
- The backend now fails closed when PM LLM invocation fails and suppresses deterministic fallback task generation for provider/runtime failures.

## Evidence List

- Electron screenshot: `test-results/electron/pm-director-real-flow-real-c864b--PM-and-Director-workspaces/test-failed-1.png`
- Runtime engine status: `%LOCALAPPDATA%/Temp/Polaris/runtime/electron-e2e/.../runtime/status/engine.status.json`
- Runtime PM contract: `%LOCALAPPDATA%/Temp/Polaris/runtime/electron-e2e/.../runtime/contracts/pm_tasks.contract.json`
- Dry-run readiness evidence: `npm run test:e2e:real-flow -- --dry-run` reported stale role model readiness for `pm`, `director`, and `qa`.
- Repo pollution check: no `.polaris` directory was found under `C:\Users\dains\Documents\GitLab\polaris`.

## Defect Analysis

- Symptom: user enters PM workspace and sees no useful progress; tests time out waiting for tasks.
- Trigger: real settings bind roles to a provider/model combination whose last successful test index is missing or stale, or PM provider invocation fails at runtime.
- Direct cause: E2E waited for generated tasks without first checking PM terminal status, PM contract terminal errors, or LLM readiness.
- Deeper design cause: readiness, process terminal state, and UI role controls were not treated as one shared contract.
- Impact: real failures looked like PM inactivity; Director/QA could be skipped while the UI still exposed PM actions.

## Fix Plan

- Add PM/LLM fail-fast checks to `pm-director-real-flow.spec.ts`.
- Disable PM run controls when the required PM LLM role is blocked.
- Show a persistent PM workspace banner for LLM readiness blocks and PM terminal errors.
- Extend shared frontend process status types to include broker terminal fields.
- Do not modify target projects or user global LLM config.

## Test Plan

- Happy path: when LLM readiness is valid, PM can start and flow continues to Director checks.
- Edge case: LLM state is `BLOCKED` for `pm`; the test must fail immediately with screenshot/evidence and UI must disable PM run.
- Exception path: PM process exits non-zero or contract contains `terminal_error_code`; the test must fail before waiting for tasks.
- Regression: stale model readiness from real settings must be reported as readiness evidence, not as a PM task timeout.

## Rollback Plan

- Revert:
  - `src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts`
  - `src/frontend/src/app/App.tsx`
  - `src/frontend/src/app/components/pm/PMWorkspace.tsx`
  - `src/frontend/src/app/types/appContracts.ts`
  - `src/frontend/src/services/api.types.ts`
- Re-run the real-flow dry-run and Electron visual spec to confirm behavior returns to previous baseline if needed.

## Addendum: Role Readiness Diagnostics

- Additional evidence from `C:\Users\dains\AppData\Local\Temp\Polaris_Role_Readiness_*` showed both MiniMax-M2.7 and MiniMax-M2.5 passed connectivity but failed role-level `response` / `qualification` readiness.
- The immediate diagnostic defect was in `EvaluationRunner`: suite-level failures without `details.cases` produced zero case records, so readiness failures carried no actionable error detail into reports or indexes.
- Minimal fix: preserve suite-level `error`, output summary, score, and latency as a synthetic case when a suite has no explicit cases; use the same conversion path for normal and streaming evaluation runs.
- Rollback for this addendum:
  - `src/backend/polaris/cells/llm/evaluation/internal/runner.py`
  - `src/backend/polaris/cells/llm/evaluation/tests/test_runner.py`
