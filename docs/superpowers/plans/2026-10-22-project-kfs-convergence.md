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

### Task 2a: KFS read observations

Base: B1 integrated on main at `9b0ac540e5277de5f1398195e18c5119601021d2`. B2a implementation and scoped acceptance complete; remaining B2 capabilities and actual consumer migrations are still pending.

Exclusive files: new `src/backend/polaris/kernelone/fs/guarded_metadata.py`, existing `fs/types.py`, `fs/runtime.py`, `fs/__init__.py`, new `fs/tests/test_guarded_metadata.py`. ROOT owns documentation. Existing public regular-file snapshot and its descriptor walker stay unchanged; no DB, staging, Session or Cell consumer changes.

- [x] Preserve expected RED for the absent public observations on unchanged implementation.
- [x] Implement `workspace_lstat`, `workspace_list_directory` and `workspace_readlink` using existing retained no-follow witnesses.
- [x] Verify actual files, links, special files, root/parent/leaf replacement, enumeration limits, descriptor release and read-only progress.
- [x] Verify adjacent guarded snapshot/directory/facade consumers and scoped style/types; independent review before integration.
- [x] Integrate only the bounded capability on main. Actual consumer migration remains in B3 and later batches; new APIs alone do not mean their callers use KFS.

Detailed preregistration: `.polaris/audits/project-storage-20261022/b2-preparation/b2a-implementation-brief.json`. Evidence belongs to `.polaris/audits/project-storage-20261022/b2a/`.

Dependency refinement: B3 shared ContextOS readers depend on B2 metadata size/identity, then use the existing guarded snapshot with an actual-file size bound. No fixed small snapshot cap is introduced, and retention's aggregate quota is not reused as a read limit. B3 retention additionally needs bounded listing. This supersedes the earlier external plan's claim that complete B3a needed no B2 capability; no B3 code or experiment has run.

R3 correction accepted: ROOT fullFS/storage first result1failed483passed is retained, while same-product KernelOne ALL passed12stages/368tests/1skip. The directory-membership test assumed every insertion changes its seven metadata fields. Sixty-four finite native fixtures refute that premise; original failed invocation lacks before/after metadata and remains causally UNKNOWN. The test now verifies an actual deterministic mode change with permissions restored; final fullFS/storage484passed with all8 frozen hashes unchanged. Four production hashes match the passed K1ALL. Preserve the requirement that future manifests compare members/content independently. No sleep, retry, timestamp forcing, production rescan or atomic-directory claim was added.

### Task 3a: ordinary ContextOS snapshot reads and registry discovery

Base: `1e7bed16306759bdac46b3c36c67a56367382a43`. B3a implementation and scoped integration checks are complete; pin queries, retention/statistics and other owners remain subsequent work.

Production scope: `polaris/cells/instances/internal/service.py`, `instances/public/service.py`, `context/engine/public/snapshot_paths.py`, `context/engine/public/service.py`, `polaris/delivery/http/v2/context.py`, all below `src/backend/`. Tests: existing `cells/instances/tests/test_instance_service.py`, `cells/context/engine/tests/test_public_service.py`, `polaris/tests/unit/delivery/http/v2/test_context_router_hardening.py`.

Adjacent-fixture ruling: also allow test-only changes in `polaris/kernelone/llm/engine/tests/test_context_store.py` after its two existing viewer fixtures failed the raw-byte hash contract. Seed the actual persisted-byte key, retain the original failed result, preserve corrupt-JSON HTTP `CONTEXT_READ_ERROR` and public `unreadable`, and keep valid non-object JSON as a distinct format error. Do not weaken production hash verification or change an existing error code merely to pass a fixture.

- [x] Reproduce cold read provisioning through existing entry points and preserve RED before implementation; use isolated synthetic workspace/registry data under this project's `.polaris`.
- [x] Reuse InstanceRegistry's strict decoder and error projection through an explicit non-provisioning read boundary; do not construct a writer supervisor, start/refresh processes, publish events or silently turn corruption into empty facts. Read-only registry mutation attempts must reject before effects.
- [x] Select canonical candidates with the existing read-only root resolver; preserve exact workspace matching, source labels and historical discovery. Valid canonical reads must remain possible when optional registry discovery is unavailable; missing-history errors must retain that diagnostic.
- [x] Share KFS metadata/guarded snapshot acquisition between both hash endpoints and the public final-request query. Bind the exact original UTF-8 bytes to the requested key, retain provider/image payloads and existing projection shapes, and stop on an unsafe/corrupt first-present candidate. No fixed small byte cap or JSON reserialization during acquisition.
- [x] Verify real producer bytes, both HTTP paths, cold absence with zero writes, cross-workspace rejection, corruption/unsafe input, degraded registry, native replacement and later valid progress. Clearly label synthetic provider/image fixtures.
- [x] Validate adjacent writable registry behavior and shared reader consumers, source/type/style checks and independent review, then freeze required integration. No live main/NATS/Bench operations.

Evidence/design inputs: `.polaris/audits/project-storage-20261022/b3-context-preparation/{plan.json,read-bound-decision.json,registry-read-dependency.json}`. The early external proposal that B3a needed no B2 capability is superseded. Existing producer, stores, pins, role execution, model limits and runtime transport remain their current owners; this task does not certify the remaining paths.

B3a acceptance: author targeted95passed/33 lifecycle tests deliberately unrun, adjacent147passed; Ruff/format/mypy passed nine Python files. Independent scoped review passed with native deletion and later valid progress probes. Fresh-child reads preserved fixture bytes/mtime with zero product writes;11 bytecode writes classified separately under authorized project cache. ROOT integration204passed plus KernelOne ALL12stages/368passed1skipped, with all11 frozen files unchanged before/after. Post-check edits only record acceptance in docs. Evidence: `.polaris/audits/project-storage-20261022/b3a/{report.json,read-recovery-evidence.json,review/report.json,root-integration/results.json,root-integration/kernelone-release.json}`. Original failures remain preserved. B3b preparation next; no full-platform or real-delivery claim.

### Task 3b: existing pin, statistics and retention owner

Base: B3a integrated `8f0cd304ab196859958b7d09b628b1e7c2153d59`. B3b implemented and scoped acceptance checks complete after R1 below. Exact initial finite brief: `.polaris/audits/project-storage-20261022/b3b-preparation/plan.json`.

Initial production bucket under `src/backend/polaris/`: `kernelone/llm/engine/context_store_retention.py`, `kernelone/llm/engine/executor.py` (only ordinary snapshot byte persistence), `cells/context/engine/public/service.py` (pin query only), `delivery/http/v2/context.py` (stats/sweep only). Four existing tests: `kernelone/llm/engine/tests/test_context_store_retention.py`, `kernelone/llm/engine/tests/test_context_store.py`, `cells/context/engine/tests/test_public_service.py`, `tests/unit/delivery/http/v2/test_context_router_hardening.py`. ROOT owns docs and commits; the R1 evidence below extends this initial scope by three existing KFS files. Lock-root implementation stays unchanged.

- [x] Preserve a finite physical RED for cold-query provisioning or path-delete replacement before changing its mechanism.
- [x] Add nonprovisioning readonly query/stats to existing owners; readonly mutators reject and unavailable authority never becomes no-pins.
- [x] Route original already-serialized ordinary snapshot bytes through existing repository and same authority; keep exact hash/image contents and avoid nested lock acquisition.
- [x] Use existing bounded KFS list/metadata/guarded snapshot/CAS deletion; incomplete inventory must fail explicitly and later valid inventory must progress. EOF is not an atomic membership guarantee.
- [x] Preserve TTL/count/bytes behavior, pin protection and counter recovery; retain partial deletion effects while rejecting false full statistics/throttle updates.
- [x] Complete finite E1-E7, scoped Ruff/format/mypy/pytest, real adjacent consumers and independent review; ROOT freezes integration without touching live services/models/Bench.

Ruling: preserve the existing shared lock authority until B5, because introducing a project-local lock in this slice would split writers. This is explicitly incomplete physical-root convergence. Traversal budget is finite operational protection, not a new model/file admission quota; exhausting it is unavailable, never empty success. Successful HTTP fields remain compatible; failure maps before successful numeric projection. All new experiment scratch/cache belongs under this main project's `.polaris` with absolute test cache paths.

#### B3b review round 1: durable confirmation

Initial author565passed/one existing fork warning; ROOT native260passed. Initial independent review CHANGES_REQUIRED/spec_met=false, one physical P1: failed parent fsync after actual create/rename leaves exact bytes, then same-fault retry returns success with zero parent-sync attempts. Evidence `b3b/review/durability-probe.json`. ROOT71615 endedexit1: KernelOne ALL11 stages passed, tests367passed1failed1skipped; newly added fixture `with target_repository._exclusive_lock(): pass` raised existing bare-pass metric245 over244. Preserve all original results and frozen10-file match; neither test counts nor readable bytes waive the P1.

Ruling: add the missing durability capability to the existing KFS module, then consume it only at immutable publication; preparation's no-new-KFS-capability assumption was falsified. No new controller, file authority/enrollment or rewrite-as-sync. Exact brief `.polaris/audits/project-storage-20261022/b3b/fix-round-1/brief.md`.

Fix-only bucket below `src/backend/polaris/`: `kernelone/fs/guarded_regular_file_snapshot.py`, `kernelone/fs/__init__.py`, `kernelone/fs/tests/test_guarded_regular_file_snapshot.py`, `kernelone/llm/engine/context_store_retention.py`, `kernelone/llm/engine/tests/test_context_store_retention.py`. Expanded cumulative candidate11Pythonfiles; original other6 candidate files stay unchanged. ROOT alone owns blueprint/plan/VC. Current freeze released only after71615 finished; no hot editing occurred.

- [x] Physical RED for snapshot and pin: create commits, parent fsync fails, identical retry under same fault must still fail and attempt actual sync.
- [x] Guarded positive file/parent fsync, exact unchanged bytes/inode, identity/byte drift and IO fault evidence; no rewriting or new enrollment.
- [x] Remove fault, same-object retry progresses; readonly query/stats prohibit write-sync and still recover normally.
- [x] Replace empty lock fixture with meaningful native/public producer setup without raising the exception budget.
- [x] Scoped checks, new frozen hashes/delta, independent R1 re-review and fresh affected ROOT integration/KernelOne ALL. Initial candidate remains rejected; new R1 candidate meets scoped acceptance.

Final R1 evidence: author592passed/1 existing fork warning, exact exception-budget1passed (unchanged threshold), Ruff/format/mypy11passed; independent APPROVED_SCOPED_R1 and fresh native durable-retry/progress probe. ROOT86146 completedexit0 with391native passed and KernelOne ALL12stages/368passed1skipped, all14 frozen hashes unchanged throughout. Post-check changes only record acceptance in ROOT docs. Evidence `.polaris/audits/project-storage-20261022/b3b/fix-round-1/{report.json,review/report.json,review/durability-probe.json,root-integration/results.json,root-integration/kernelone-release.json}`. No real provider/browser/Bench/main lifecycle was run. Existing lock root remains B5; operation budget1000000 explicit, large-store costs unmeasured. This is a module closure; original120 remains0accepted/1cancelled/119unstarted/0active.

### Task 2b-S1: owner-bound workspace atomic write (after frozen B3b integration)

Finite prepared design: `.polaris/audits/project-storage-20261022/b2-staging-preparation/plan.json`. Not implemented. Scope the existing KFS workspace atomic entry plus adapter/type/contract/native primitive and meaningful existing-loop effect tests; do not migrate all path-only helpers or CAS in this first slice. Reuse explicit KFS workspace, canonical layout and guarded directory witnesses; same-device stage lives under owner `.polaris/runtime/fs/staging`. Preserve existing parent permissions/umask and file0600 overwrite behavior; no silent non-atomic fallback or post-effect path-resolution error reported as no effect.

- [ ] T1 physical create/replace/empty UTF-8 writes and actual stage placement/cleanup.
- [ ] T2 existing supported identity/symlink/hardlink boundaries and descriptor closure; no new VFS threat model.
- [ ] T3 real same-device cross-directory atomic replacement; unsupported/EXDEV denied without fallback (fault injection labeled).
- [ ] T4 pre-effect unchanged target versus real post-rename failure and explicit uncertainty.
- [ ] T5 actual durable engineering write dispatch after real rename failure, legal same-service resume/readback without original write replay; original row/effect unknown remains original, edit/command not waived.
- [ ] T6 managed-adapter contract, UTF-8/path regressions, scoped checks and independent review. Remaining logical/config/path-only/CAS callers stay explicit followups.

Native DB follows existing bounded OPTION_A in `.polaris/audits/project-storage-20261022/b2-preparation/bounded-native-db-ruling-options.json`: extract current physical safeguards into KFS + existing KernelDatabase/SqliteAdapter without changing Session state owner or adding VFS/process/lease table. No implementation acceptance yet.

B5 preparation is source-only in `.polaris/audits/project-storage-20261022/platform-root-observation/transition-plan.json`. New-launch root binding and later deployed offline transfer are distinct claims. Existing external bootstrap producer UNKNOWN need not block a new explicit launch recipe; real NATS cross-root transfer/rebind and coordinated maintenance remain necessary before deployed-root closure. No current process changed.
