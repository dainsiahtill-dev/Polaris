# Instance Stop Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. All work stays on main; no branch/worktree creation.

**Goal:** Health projection refresh during an authorized instance drain preserves correct stop settlement without weakening owner replacement checks.
**Architecture:** Reuse InstanceSupervisor, InstanceRegistry mutation lock and engineering_stop_progress. No new owner, ledger, API or retry.
**Tech Stack:** Python, existing KFS/Registry, pytest.
**Spec:** docs/blueprints/INSTANCE_STOP_SETTLEMENT_BOUNDARY_20261025.md

## Global Constraints

- Original L2-18 target and old UNKNOWN remain immutable. No existing services restarted/stopped.
- Only service.py and its existing instance-service tests belong to implementer. Root owns docs.
- UTF-8; private experiments/cache under main .polaris; all shell commands begin rtk.
- CodeGraph first; read backend/graph norms. No permission/deadline expansion.

## Review Focus

- Concurrent health-only metadata changes must not erase a confirmed drain.
- Replacement token/PID/workspace/start identity must still block further process operations.
- Partial failure must preserve current metadata and exact confirmed closure; unknown effects never replay.
- Existing self-stop/main-port/resource-namespace protections stay active.
- Test adapters and real OS/process concurrency evidence must be labelled separately.

### Task 1: Reproduce and fix existing stop owner boundary

**Files:** modify src/backend/polaris/cells/instances/internal/service.py; tests in src/backend/polaris/cells/instances/tests/test_instance_service.py (a dedicated adjacent test file allowed only if needed).
**Consumes/Produces:** existing stop_instance(instance_id, engineering_settlement=None) and Registry contract unchanged.

- [x] Add behavioral regression for a health-only update at a drain/commit barrier using the existing real registry owner. Observe old-code failure for the intended reason; persist red output privately.
- [x] Add or reuse owner replacement and partial-stop counterexamples; do not mirror implementation details.
- [x] Implement the smallest ownership/merge correction justified by the red result. Preserve concurrent metadata and confirmed progress under existing lock.
- [x] Run focused tests, existing instance tests and adjacent settlement/transport tests; Ruff/format/mypy on changed paths.
- [x] Return exact diff, red/green evidence, changed mechanism and remaining risks; do not commit or push. Root performs independent review and integration on main.

### Task 2: Independently review and verify real concurrency

- [x] Root/a fresh reviewer checks the actual diff and test authority, including real replacement and UNKNOWN semantics.
- [x] Run one bounded real registry/process concurrency experiment in a fresh isolated internal fixture with protected main untouched. No target-project code or LLM calls.
- [x] Run required architecture/K1 checks proportionate to touched contracts; retain baseline failures separately.
- [x] Update this plan and public status; commit reviewed main changes. Source qualification is not fresh delivery proof.

Actual closeout: two explicit red/green boundaries, independent scope reviews, 194 related tests, Root 9 focused tests, final K1 all 368pass/1skip, real stop-call concurrency with stated limits. Root source integration follows; fresh Native not yet run.
