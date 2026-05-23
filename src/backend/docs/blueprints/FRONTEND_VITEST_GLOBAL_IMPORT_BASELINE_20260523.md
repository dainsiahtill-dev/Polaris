# Frontend Vitest Global Import Baseline

Date: 2026-05-23

## Scope

- Test baseline files under `src/frontend/src/app/**/__tests__/`
- Provider tests under `src/frontend/src/app/components/llm/providers/`
- Visual config test under `src/frontend/src/app/components/llm/visual/utils/`

## Root Cause

The full frontend suite used `src/frontend/vitest.config.ts`, which does not expose Vitest globals. Twelve older suites still referenced `describe`, `it`, `expect`, or `vi` as implicit globals, and one suite used `jest.fn()`. The targeted Brain-memory tests passed, but `npm test` failed before those older suites could execute.

## Fix

- Added explicit Vitest imports to the twelve affected test files.
- Replaced legacy `jest.fn()` calls in `apiValidation.test.ts` with `vi.fn()`.
- Kept the production code unchanged for this baseline repair.

## Verification

- Initial `npm test`: failed with `ReferenceError: describe is not defined` in 12 suites.
- `npx eslint` on the twelve repaired suites: passed.
- `npm test -- <12 repaired suites>`: 12 files, 61 tests passed.
- Final `npm test`: 74 files, 665 tests passed.
