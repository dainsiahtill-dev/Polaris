# Context Durable Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Main only; no branch/worktree.

**Goal:** Every committed available native snapshot reference retains readable original bytes through GC and cold recovery.
**Architecture:** Existing Engineering owner supplies generic host-only reference ownership; existing K1 CAS/pin locks protect bytes before GC/publication. V1 readers stay compatible, original24hex APIs remain read-only.
**Tech Stack:** Python, current KernelOne store/locks/KFS, engineering SQLite, pytest.
**Spec:** docs/blueprints/CONTEXT_DURABLE_REFERENCE_RETENTION_20261025.md

## Global Constraints

- No target edits/oldCASrestore/providercalls/servicescontrol; longterm memory excluded.
- Host authority stays out of model input/options and provider body. Explicit empty permissions unchanged.
- OldV1Factory remains compatibility only; no new business owner or ledger.
- Allruntime/cache/evidence underown project.polaris viaKFS UTF8; shellcommandsrtk.

## Review Focus

- Current object can be evicted beforeobserver; publication protection must be in producer.
- Concurrent calls/roles/runs cannot use stale owner identity.
- Cold present refs may migrate; absent refs never recreated, reads never mutate.
- Invalid/mixed/foreign/symlink pins cannot weaken GC safety or target permissions.
- Protected-over-budget and crash orphans cannot become false success or automatic replay.

### Task 1: Full ownership handoff migration

**Files:** existing K1 final_request_evidence/_audit_pin.py+__init__.py, llm/engine/context_store_retention.py+executor.py+native_request_capture.py and scopedtests; roles/runtime/public/engineering_service.py, internal/engineering_session.py and scopedcontexttests. Only necessary owned Cell metadata/subgraph notes after Rootreview.
**Interface:** generic ContextSnapshotReferenceOwnerV1/PinV2; optional host resolver and reference_owner argument, existingV1wrapper/query unchanged. Pin in same existing immutable-byte lock beforeGC/available.

- [x] Read actual public/internal call paths and exact V1 consumers; write integrated red test on existing capture+SQLite with synthetic provider input and small bytecap. Preserve actual red before code.
- [x] Implement generic contract, atomic pin+bytes, both producer paths, host resolver and event validation together. No partial cutover.
- [x] Implement bounded sameworkspace cold reference reconciliation before new GC; preserve missing refs and orphan unknowns without operation replay.
- [x] Add cold/API/V1compatibility/pressure/protected-budget/process concurrency/crash/security counterexamples; run scopedtests/staticchecks.
- [x] Return exact report and Sourcepins; stop edits for fresh independent review. Root owns integration/docs.

### Task 2: Integrate and verify new evidence

- [x] Independently review authority, lock/publication ordering, migration, bytes and recovery; fix actual blockers.
- [x] Required architecture/K1 gates; evaluate affected descriptors/catalog without broad regeneration.
- [ ] Commit on main, freeze new Source/config. Fresh bounded formal run thencold-read every new event ref and actualprovider body; historicalL218 gaps unchanged. This last step remains product acceptance, not Sourcequalification.

Component closeout: all producers and cold/read consumers migrated; AUTH-CALL-01 and both storage suggestions closed; 17 pins matched; final K1 all 368pass/1skip and no-new catalog gate passed. Last commit/freeze/native step remains pending actual action.
