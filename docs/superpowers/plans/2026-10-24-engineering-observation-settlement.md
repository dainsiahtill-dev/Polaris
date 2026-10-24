# Engineering Observation and Settlement Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development; preserve main-only and the independent file buckets below.

**Goal:** restore responsive truthful observation and safe autonomous settlement for the L213 failure without weakening current-input acceptance.
**Architecture:** existing EngineeringRunService/SQLite owns facts; a query-local KFS observation serves existing applicability checks; readonly lifecycle projection reaches existing Supervisor independently of artifact freshness.
**Tech stack:** Python/SQLite/KFS/KernelOne/Nats-JetStream; existing HTTP/CLI.
**Spec:** docs/blueprints/POLARIS_ENGINEERING_OBSERVATION_SETTLEMENT_20261024.md

## Global constraints

Main only; no branch/worktree, target edits or new controller/table/ledger. Preserve original permission/deadline/unknown effects, UTF-8, canonical project.polaris and single realtime rail. All temporary experiments/cache under project.polaris. No main-instance restart.

## Review focus

- Current-source/external-dependency/output changes must still invalidate verification and required QA across separate reads and cold reopen.
- Missing release/legacy/unknown effects may not authorize automatic stop.
- Query IO and encoding must not starve health/runtime.v2.
- Partial termination must never signal reused/current/main/foreign identities or report unconfirmed stopped.
- Observer UNKNOWN plus successful resource closure stays non-delivery.

## Task A: one-query actual observation and responsive transport

Files: roles/runtime/internal/engineering_verification.py; roles/runtime/public/engineering_service.py; delivery/http/routers/engineering_runs.py; new tests test_engineering_observation.py and test_engineering_observation_responsiveness.py. Do not edit engineering_session.py or resource/query/CLI/instance bucket.

Consumes existing session store/Scope/KFS. Produces current-source API behavior with one real input capture/query, no cross-query cache, existing wire snapshot fields and current/lookup parity.

- [ ] Add RED owner/capture-count and real ASGI scheduling/encoding counterexamples; preserve the actual rejected-QA fixture distinction.
- [ ] Introduce the smallest host-only request-local current-input observation and reuse it across verification/QA/visual checks. Keep default non-observation callers fresh.
- [ ] Reuse kinds/role filters and existing bounded worker primitives for complete read/encoding; preserve schema, permissions, independent QA, context refs and cold behavior.
- [ ] Run focused/stale/visual/recovery tests plus Ruff/format/mypy; submit evidence and exact pins. No commit or source edits outside this bucket.

## Task B: canonical resource facet and progress-safe settlement

Files: roles/runtime/internal/engineering_session.py; new roles/runtime/public/engineering_resource_state.py (thin facade); optional small internal resource-query module only if needed; delivery/cli/engineering_acceptance.py; instances/internal/service.py and existing public facade only if required; corresponding resource/CLI/instance/consumer tests. Do not edit Task A files or shared docs/graph.

Interface: query_engineering_resource_state(workspace, session_id=None, request_key=None) is a readonly public program query of the same store. Return bounded exact identity/version/terminal/release/active/pending/reconciled facts and safe_to_stop; it cannot return fresh accepted or create/migrate/recover a run.

Supervisor interface: optional `engineering_settlement` contains the generic `engineering.resource_settlement.v1` expectation, not the internal CLI/Bench binding. CLI converts and retains its internal-only scope checks; the owner independently validates normal-project identity and canonical quiescence without Bench business fields. Confirmed partial closure is existing owner Registry state, never a PID wildcard.

- [ ] Add RED unavailable-heavy-HTTP/terminal-quiescence and frontend-error/backend-progress counterexamples; pair illegal/active/identity/cold negatives.
- [ ] Implement readonly same-owner query using canonical storage and a consistent readonly transaction. No second truth source or writing constructor.
- [ ] Migrate CLI observe/stop and adjacent internal consumers to independent settlement; acceptance unknown stays unknown; preserve5400/6000 and old attempts.
- [ ] Preserve partial stop errors, revalidate each residual child, reuse K1 waits/signals; no blanket zombie shortcut.
- [ ] Run focused/cold/consumer tests and Ruff/format/mypy; report evidence/pins, no commit or cross-bucket edits.

## Task C: ROOT integration and fresh acceptance

- [ ] Independently review each bucket and actual RED/GREEN evidence; resolve interfaces before any dependent work.
- [ ] Synchronize graph/cell/blueprint/technical evidence; run final targeted and architecture/KernelOne ALL/catalog checks from a stable candidate.
- [ ] Independent whole-change review, main integration, then frozen new batch with existing explicit Go profile and original task/acceptance.
- [ ] Real independent QA, ROOT product quality and automatic resource closure; preserve L213 timeout/assisted history. Continue the original120 afterward; no full-platform completion claim.
