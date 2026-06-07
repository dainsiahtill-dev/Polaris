# Production Stability Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one fail-closed runner that turns Polaris long-run production validation into a single auditable command.

**Architecture:** Keep the existing Electron full-chain audit and dual-entry matrix as source-of-truth E2E gates. Add a thin orchestration script that sequences full-chain, fault/rollback, stress/performance, and governance checks, then writes a UTF-8 JSON audit package.

**Tech Stack:** Node.js runner, existing Playwright E2E scripts, existing Python pytest suites, package.json script entry.

---

### Task 1: Runner Contract Test

**Files:**
- Modify: `src/backend/polaris/tests/electron/test_e2e_runner_scripts.py`

- [ ] **Step 1: Write the failing test**

Add a test that executes `infrastructure/scripts/run-production-stability-validation.mjs --dry-run` and asserts the JSON payload contains required phases: `full_chain`, `fault_injection_rollback`, `performance_stress`, and `governance`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src/backend pytest src/backend/polaris/tests/electron/test_e2e_runner_scripts.py::test_production_stability_runner_dry_run_declares_all_required_gates -q`

Expected: FAIL because the runner file does not exist yet.

### Task 2: Minimal Runner

**Files:**
- Create: `infrastructure/scripts/run-production-stability-validation.mjs`

- [ ] **Step 1: Implement a dry-run and gate list**

The script must parse `--dry-run`, `--output`, and `--skip-real-chain`. Dry-run prints JSON without executing commands. Non-dry-run executes configured commands sequentially and writes a JSON audit package.

- [ ] **Step 2: Run the focused test**

Run: `PYTHONPATH=src/backend pytest src/backend/polaris/tests/electron/test_e2e_runner_scripts.py::test_production_stability_runner_dry_run_declares_all_required_gates -q`

Expected: PASS.

### Task 3: Package Entry And Regression

**Files:**
- Modify: `package.json`
- Modify: `src/backend/polaris/tests/electron/test_e2e_runner_scripts.py`

- [ ] **Step 1: Add package script assertion**

Extend the test suite so `package.json` exposes `test:e2e:production-stability`.

- [ ] **Step 2: Add the package script**

Set `test:e2e:production-stability` to `node --env-file-if-exists=.env infrastructure/scripts/run-production-stability-validation.mjs`.

- [ ] **Step 3: Run validation**

Run:
- `PYTHONPATH=src/backend pytest src/backend/polaris/tests/electron/test_e2e_runner_scripts.py -q`
- `node --check infrastructure/scripts/run-production-stability-validation.mjs`
- `npm run test:e2e:production-stability -- --dry-run`

Expected: all PASS.
