# Paperclip Polaris Team Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. This is external engineering configuration, not a Polaris product subsystem. The user approved the 26-person design and direct implementation; do not create branches or commits.

**Goal:** Reuse POL's CEO and provision 25 employees, all `codex_local`, `gpt-6.1-sol`, reasoning `high`, with independently verifiable reporting relationships, instructions, and a main-checkout project workspace.

**Architecture:** Paperclip is an external assignment and review system only. Its reports and employee statuses never become Polaris runtime dependencies, receipts, Run Ledger facts, or Bench success conditions. At most four employee levels; independent quality reports directly to CEO.

**Tech Stack:** Existing loopback Paperclip REST API; Python standard-library engineering-only client; existing Codex adapter; Playwright for read-only browser acceptance.

**Spec:** User-approved organization in the preceding conversation; the exact roster and module responsibilities are recorded in `docs/governance/POLARIS_PAPERCLIP_TEAM_20261004.md`.

## Global Constraints

- 26 employees total, including existing CEO; no duplicate CEO, no unrelated company changes.
- All model settings: `model=gpt-6.1-sol`, `modelReasoningEffort=high`; never silently fall back.
- Reuse responsible user's configured OpenAI subscription binding; never copy or expose credentials.
- Main workspace: `/home/dains/Documents/polaris`; no worktrees, branches, automatic installation, or service startup.
- Every new employee: timer disabled, on-demand wake disabled, `maxConcurrentRuns=1`; one explicitly authorized read-only pilot may be awakened once, then restore disabled wake.
- Project shared workspace concurrency: `serialize`; issue overrides disabled. This is stricter than the proposed maximum of three workers and protects the existing dirty checkout until finer verified isolation exists.
- Generated Bench targets remain read-only; only Polaris platform changes can remediate generic root causes.
- Root owns acceptance; module responsibility is not blanket filesystem authorization.
- All shell commands start with RTK, code discovery uses CodeGraph first, UTF-8 is explicit.

## Review Focus

1. API partial failure must reconcile existing employees, not blindly repeat create POSTs.
2. Role/reporting-chain validation must reject cycles, foreign managers, and depth greater than four.
3. Defaults must not re-enable timer wake, 20-way concurrency, or approval/sandbox bypass.
4. Persisted model fields alone are insufficient; inspect actual pilot execution arguments.
5. A successful pilot must not modify production source or generated target code, and is not fresh Bench qualification.

## Task 1: Safe provisioning client and roster

- [ ] Write engineering tests for exact payload settings, cycle/depth protection, duplicate reconciliation, and independent QA reporting.
- [ ] Observe missing-client RED before implementation; implement the bounded client and run its complete test suite.
- [ ] Capture sanitized pre-state and existing source/target hashes before external changes.

Files: `.superpowers/sdd/2026-10-04-paperclip-polaris-team/test_provision_team.py`, `provision_team.py`, `progress.md`.
Interface: immutable roster keys plus parent keys; `build_payload(employee, manager_id, ai_binding)` returns the existing Paperclip create-agent contract. `validate_roster` fails closed before any API mutation.

## Task 2: Employees, instructions, and project

- [ ] Reconcile CEO and company by the verified POL prefix. Create remaining employees in manager-before-child order.
- [ ] Set restrictive permissions through the existing permissions API; managers may assign tasks, employees may not hire or expand their own scope.
- [ ] Persist each managed instruction bundle using public APIs. Preserve CEO's earlier instructions as a separate historical reference.
- [ ] Create one idempotent Polaris engineering project with primary local workspace and serialized shared execution; leave Onboarding unchanged.
- [ ] Seed three precise current-frontier backlog issues plus one read-only pilot; automatic wake remains disabled.
- [ ] Independently GET all employees, instructions, project, workspaces, and organization graph; assert exact settings and hierarchy.

## Task 3: Real execution and final acceptance

- [ ] Wake only the independent audit employee for the read-only pilot, with bounded execution and exact issue/workspace binding.
- [ ] Inspect real run status and execution arguments for model, effort, sandbox, and cwd; preserve errors honestly.
- [ ] Restore disabled on-demand wake, compare source and generated-target hashes, and verify no other employee ran.
- [ ] Read-only Playwright acceptance of the POL employee/organization page; distinguish API acceptance from any unresolved UI loading issue.
- [ ] Publish an engineering-only acceptance report and update the user's requested experience note. Never inject these into Polaris product facts.

## Rulings

- Configuration/API task, not product-code behavior: standard-library tests protect client validation and payloads; no whole Polaris test rerun is claimed or needed for unchanged production source.
- No branch, commit, or automatic source changes, per user instructions.
- User's latest instruction overrides differentiated model tiers: all 26 use the same exact model and high effort.
- User already approved implementation; no repeated document approval pause.
