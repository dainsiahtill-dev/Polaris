# Project Director Landing Blueprint - 2026-05-06

Status: active; batch-1 audit launched 2026-05-06
Owner: Codex acting as project director and final auditor
Scope: Polaris product landing, architecture completion, reliability hardening

## 1. Current Understanding

Polaris is a meta-tool platform, not a target-project application. All product
work must strengthen Polaris itself: planning, role orchestration, Director
execution, evidence, auditability, runtime visibility, and closed-loop quality
gates. Repository rules prohibit adding target-project or business-specific
code to the main workspace.

The active architecture direction is ACGA / Cell governance on top of
KernelOne. Backend work must prefer existing Cells, public contracts, graph
truth, and KernelOne substrate before creating new behavior. State ownership,
effects, event emission, evidence writes, and public/internal boundaries must
remain traceable.

This landing effort is not a single feature sprint. It is a supervised
multi-batch execution loop:

- inspect the current product surface and graph truth;
- identify defects or missing product-critical contracts;
- delegate bounded work to subagents in batches of at most five;
- audit their evidence and changes;
- merge only minimal, tested, reversible improvements;
- repeat until the product has a defensible end-to-end landing path.

## 2. Evidence Register

Already read or confirmed in this session:

- `AGENTS.md`
- `src/backend/AGENTS.md`
- `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`
- `src/backend/docs/graph/catalog/cells.yaml`
- `src/backend/API_AUDIT_REPORT.md`
- `docs/blueprints/PRINCIPAL_CORE_AUDIT_BATCH_20260506.md`
- recent git history through `cfbf2a0`
- current working tree status and diff summary
- `package.json`
- `playwright.electron.config.ts`
- `src/backend/polaris/delivery/http/routers/role_chat.py`
- `src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py`

Confirmed facts:

- The current working tree is dirty and contains many unrelated modified and
  untracked files. Project Director changes must be file-scoped and must not
  revert unrelated user or generated work.
- A recent factory feature commit already exists:
  `69cb4c7 feat(factory): enhance FactoryWorkspace with artifacts and summary handling`.
- A later commit exists:
  `cfbf2a0 Add unit tests for Polaris runtime and system v2 endpoints`.
- `src/backend/polaris/delivery/http/routers/role_chat.py` implements
  `/v2/role/{role}/chat`, `/v2/role/{role}/chat/stream`, role status, role
  list, llm-events, cache stats, and cache-clear routes, with unit coverage in
  `src/backend/polaris/tests/unit/delivery/http/routers/test_role_chat.py`.
- `cells.yaml` already defines owners for context, delivery gateway,
  workspace guard, runtime state owner, task market, and other core Cells.
- Prior exploratory findings flagged potential gaps in task-market mainline
  integration, context pack contracts, and evidence-chain enforcement.

Open assumptions:

- The unified role chat contract appears implemented; remaining risk is not
  route absence but whether all role paths use the intended Cell/KernelOne
  runtime and failure semantics.
- Director task consumption may still contain placeholder behavior in one
  path; the authoritative execution path must be traced before changing it.
- Evidence package hashing may contain an implicit encoding defect; this must
  be verified against the actual source and tests.
- Context descriptor generation and generated context pack drift may be a
  product issue, but the owning Cell contract must be confirmed first.
- Electron smoke/runtime E2E commands exist. Full acceptance is explicitly
  gated by `KERNELONE_E2E_USE_REAL_SETTINGS=1` through
  `infrastructure/scripts/run-electron-acceptance-e2e.mjs`.

## 3. Batch Strategy

Each batch may use at most five subagents. The project director keeps final
responsibility for scope, evidence, code review, testing, and acceptance.

Batch 1: contract and defect confirmation

- Electron/frontend runtime startup and connection chain.
- Backend HTTP route/auth/rate-limit/readiness policy.
- runtime.v2 WebSocket, NATS, cursor, and consumer lifecycle.
- PM/Director/QA product workflow and placeholder risk.
- Test, CI, acceptance, and evidence package gates.

Batch 2: API landing slice

- Harden the smallest confirmed API contract gap from Batch 1.
- Do not treat role-chat route absence as a current fact unless new evidence
  contradicts `role_chat.py` and its tests.
- Add request/response models only where they reduce ambiguity without broad
  v2 migration.

Batch 3: Director / task-market landing slice

- Confirm canonical task intake.
- Replace placeholder behavior only if it is on a product path.
- Add state transition and idempotency tests.

Batch 4: evidence and audit package slice

- Enforce explicit UTF-8 and stable hashing where missing.
- Ensure evidence bundle failures have deterministic error behavior.
- Add regression tests for missing files, non-ASCII payloads, and malformed
  metadata.

Batch 5: context plane slice

- Formalize minimal query objects only where call sites need them.
- Add drift/validation tests before changing generated context artifacts.
- Avoid moving state ownership out of the existing context Cells.

Batch 6: frontend mission control slice

- Confirm that the product surface exposes run status, evidence, acceptance,
  and failure causes.
- Add UI tests for empty, loading, error, and passed states.

Batch 7: Electron / Playwright acceptance slice

- Stabilize repository-root resolution and runtime launch assumptions.
- Run discoverability, panel, and one full-chain test where feasible.

Batch 8: security and workspace guard slice

- Audit auth, path traversal, workspace mutation, and dangerous command
  blocking.
- Fix only verified bypasses or misleading pass states.

Batch 9: docs and operator runbook slice

- Align user-facing run commands with actual scripts and routes.
- Produce a short recovery and rollback runbook tied to evidence paths.

Batch 10: final integration and release gate

- Re-run targeted backend, frontend, Electron, and smoke gates.
- Produce the final audit package with issue history, evidence paths, remaining
  risks, and next release blockers.

## 4. Defect Analysis Model

No finding is accepted without:

- file and line evidence;
- call-chain evidence or a deterministic counterexample;
- trigger conditions;
- root cause separated from symptom;
- impact scope;
- smallest safe fix point;
- regression test plan.

Severity definitions:

- Blocker: crash, data corruption, state corruption, security exposure, or core
  product flow unavailable.
- Major: incorrect results, missing product-critical contract, hard-to-debug
  state drift, or edge-case failure on supported flows.
- Minor: local robustness or maintainability issue with low blast radius.
- Nitpick: style or naming only; not sufficient to justify product work alone.

## 5. Repair Strategy

Allowed changes:

- narrow backend or frontend code changes tied to confirmed product defects;
- tests proving the defect and fix;
- docs or blueprint updates reflecting actual behavior.

Disallowed changes:

- target-project business logic;
- broad refactors without protective tests;
- graph truth rewrites unless code behavior actually changes;
- public interface changes unless a documented contract mismatch is proven;
- suppressing tool failures without root-cause analysis.

## 6. Verification Plan

Python gates for modified backend files:

- `ruff check <paths> --fix`
- `ruff format <paths>`
- `mypy <paths>`
- `pytest <tests> -q`

Frontend and Electron gates when touched:

- `npm run lint`
- `npm run typecheck`
- targeted `npm test -- <tests>`
- `npm run test:e2e -- --list`
- targeted Playwright test when the change affects Electron behavior

Product gates:

- role chat contract tests;
- Director task execution state tests;
- evidence bundle tests;
- context pack validation tests;
- Factory run / audit bundle tests;
- Electron visible product surface tests.

## 7. Rollback Plan

Rollback must be file-scoped and preserve unrelated user work:

- identify files changed in each batch;
- keep a before/after validation command list;
- revert only the batch-specific files if needed;
- rerun the exact regression tests that justified the change;
- never delete unrelated untracked audit or cache files without instruction.

## 8. Batch 1 Acceptance Criteria

Batch 1 is complete only when:

- five subagent findings are collected or explicitly timed out;
- each finding is classified as confirmed, high probability, needs more
  validation, or rejected;
- any code changes have tests and quality gates;
- Batch 2 tasks are narrowed to confirmed product-critical gaps.

## 9. Batch 1 Launch Register

Launched subagents:

1. Electron startup and frontend runtime connection audit.
2. Backend HTTP route/auth/rate-limit/readiness contract audit.
3. runtime.v2 WebSocket, NATS, cursor, and consumer lifecycle audit.
4. PM/Director/QA workflow and Director placeholder path audit.
5. Test, CI, acceptance, and evidence package audit.

No implementation changes are authorized from Batch 1 reports until the Project
Director consolidates evidence and writes the next batch's minimal task brief.
