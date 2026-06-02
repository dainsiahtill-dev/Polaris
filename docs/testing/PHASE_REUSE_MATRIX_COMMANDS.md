# Polaris E2E Phase Reuse Matrix

This matrix fixes the default E2E entry points for full-chain audit work. Use it to avoid rerunning already-proven phases while keeping evidence explicit.

For requirement-to-evidence mapping, see `docs/testing/FULL_CHAIN_AUDIT_ACCEPTANCE_MATRIX.md`.

## Commands

### Full Cold Chain

Run only after large cross-phase changes or before final acceptance:

```powershell
$env:KERNELONE_E2E_USE_REAL_SETTINGS='1'
$env:KERNELONE_E2E_PROJECT_SCENARIO='game'
$env:KERNELONE_E2E_WORKSPACE_NAME='Polaris_Game_Stress_E2E_freshN'
$env:KERNELONE_E2E_DIRECTOR_RESULT_TIMEOUT_MS='900000'
npm run test:e2e -- --output=test-results/electron-full-chain-game-freshN src/backend/polaris/tests/electron/full-chain-audit.spec.ts
```

### Resume From PM

Use after Architect/Court evidence is already accepted and PM/ChiefEngineer/Director/QA are under test:

```powershell
$env:KERNELONE_E2E_USE_REAL_SETTINGS='1'
$env:KERNELONE_E2E_RESUME_WORKSPACE='C:\Temp\Polaris_Game_Stress_E2E_freshN'
$env:KERNELONE_E2E_START_PHASE='pm'
$env:KERNELONE_E2E_PROJECT_SCENARIO='game'
$env:KERNELONE_E2E_DIRECTOR_RESULT_TIMEOUT_MS='900000'
npm run test:e2e -- --output=test-results/electron-full-chain-game-freshN-from-pm src/backend/polaris/tests/electron/full-chain-audit.spec.ts
```

### Resume From Director

Use only when the PM contract and ChiefEngineer handoff have already passed the current strict gates:

```powershell
$env:KERNELONE_E2E_USE_REAL_SETTINGS='1'
$env:KERNELONE_E2E_RESUME_WORKSPACE='C:\Temp\Polaris_Game_Stress_E2E_freshN'
$env:KERNELONE_E2E_START_PHASE='director'
$env:KERNELONE_E2E_PROJECT_SCENARIO='game'
$env:KERNELONE_E2E_DIRECTOR_RESULT_TIMEOUT_MS='900000'
npm run test:e2e -- --output=test-results/electron-full-chain-game-freshN-from-director src/backend/polaris/tests/electron/full-chain-audit.spec.ts
```

## Evidence Rules

- Cold chain evidence must include court, PM, ChiefEngineer, Director, QA screenshots and JSON audit package.
- Resume runs must record the skipped phase source in `issues_fixed` with `root_cause=resume_strategy`.
- PM resume evidence must satisfy the current strict PM contract gate, including workspace binding and game domain coverage.
- Director resume evidence must be fresh for the current audit run: `director.result.json` older than the audit start is ignored, and a successful Director phase must show runtime contribution relative to the run baseline.
- Director resume evidence must include `director_tool_audit.policy_evidence_count > 0` when Director contributes file changes in the current run.
- QA PASS is not sufficient unless `integration_qa.result.json.evidence_grade` is `real_command_passed`.
