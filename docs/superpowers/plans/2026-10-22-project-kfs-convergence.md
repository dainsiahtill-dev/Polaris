# Project KFS Convergence Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans or subagent-driven-development; follow the existing authorized autonomous execution on main, without new branches.

**Goal:** All platform-owned project runtime/cache/image I/O converges through KFS under its owning workspace `.polaris`.

**Architecture:** Reuse storage.layout, KFS/KernelDatabase/native adapters and existing state owners. First close canonical writer/read-only root selection and KFS provisioning; then migrate remaining producer/persistence/read/recovery paths by owner.

**Tech Stack:** Python, KernelOne FS/DB/process, SQLite, existing pytest and release/catalog checks.

**Spec:** docs/blueprints/POLARIS_PROJECT_KFS_CONVERGENCE_20261022.md

## Global Constraints

- Main branch only; preserve unrelated work and oldFAILED/CANCELLED/UNKNOWN. No target Bench edits or external build/install/acceptance.
- No new controller/ledger/runtime roots; same-workspace identities, effects, unknown/cancel recovery and native authority remain.
- All new test/cache/scratch/audit material in owner project `.polaris`. No hot root changes to main/NATS/front-end.
- Temporary staging migration must preserve atomic rename and physical guards; no generic name-based exemption for adapters.

## Review Focus

- Cached external roots cannot bypass new writer policy; add a real resolver test.
- Business resolver invalid roots cannot silently fall back to a different writer; test registered-path behavior.
- Cold read/current namespace and old historical candidates must not create/probe files; real before/after tree checks.
- KFS initialization during root preparation must not recurse through the write resolver; real default/cold test.
- Main shared-owner data and target project data must remain distinct; inspect paths, not merely return200.

### Task 1: B1 canonical runtime write root and KFS provisioning

Files: modify `src/backend/polaris/kernelone/storage/layout.py`; add `src/backend/polaris/kernelone/storage/tests/test_project_storage_root_policy.py`; migrate adjacent tests only when their old external-write expectations conflict with the new user requirement.

Interfaces: existing `resolve_storage_roots`, `resolve_storage_roots_read_only`, `resolve_existing_storage_roots_read_only`, `_runtime_base_and_mode`; retain signatures and existing StorageRoots owner. Provision through existing KernelFileSystem/guarded capabilities, not business Path/open.

- [x] Write meaningful actual-filesystem tests: canonical progress, external writer refusal before effects, cache/business-root refusal, legacy bare-root normalization, cold read with zero writes and history discovery.
- [x] Run new tests on unchanged product and preserve expected RED output in `.polaris/audits/project-storage-20261022/b1/`.
- [x] Implement smallest root policy and KFS preparation change; no browser/Session/controller expansion in B1.
- [x] Run GREEN and adjacent storage-layout/business-root/read-only-consumer tests; fix actual regressions, preserve original failed evidence.
- [x] Run scoped Ruff/mypy, independent diff review and required integration checks; report unknown global baselines honestly.
- [x] Record B1 evidence and integrate only its authorized files on main. Do not mark all storage/KFS work complete.

### Subsequent owner batches

- [ ] B2 existing KFS metadata/temp/staging/DB capability closure, with real safety/recovery counterexamples.
- [ ] B3 production session/tree/manifest/context-image producer/consumer/cold recovery migrations.
- [ ] B4 owner-local process/browser/cache scratch and release lifecycle.
- [ ] B5 platform offline relocation plus remaining inventory owners and Electron bridge.
- [ ] B6 refreshed inventory, retained visual-ordering fix, final checks and new frozen original120 native batch.

Detailed code/API choices for later batches follow their concrete boundary evidence; their unchecked entries are not completed work or product gates.

B1 integration note: first native result 1053 passed/18 failed/1 skipped retained; five fixture assumptions migrated; thirteen original cold-child failures remain causally UNKNOWN. Final full regression passed 1071 tests/1 skipped with all14 frozen hashes unchanged. Four representative recovery checks retained read-only SQLite evidence. KernelOne ALL passed 12 stages/368 tests/1 skipped on unchanged production SHA. ROOT Ruff/format/standard mypy passed all11 Python changes. Official Catalog still fails with159 pre-existing findings; independent196-file BASE byte comparison records that boundary without baseline changes. B2a read-only brief proposes only three missing metadata/list/readlink methods, reusing the already-public guarded snapshot. No B2 implementation yet.
