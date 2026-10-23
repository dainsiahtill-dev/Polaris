# Offline Web Origin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps below; all work stays on main.

**Goal:** Render authorized built HTML/SVG with ordinary Web asset/module semantics without rewriting the target for observation.

**Architecture:** Extend the existing roles.runtime browser tool with optional web_root. Reuse Playwright route fulfillment, KFS and the current K1 sandbox/process; retain the existing journal and host scope.

**Tech Stack:** Python, ToolSpecRegistry, KernelFileSystem, Playwright/Chromium, existing engineering SQLite and native codecs.

**Spec:** docs/blueprints/POLARIS_OFFLINE_WEB_ORIGIN_20261023.md

## Global Constraints

- No target edits/install/test execution by external engineering agents; explicit SYNTHETIC fixtures only.
- main only; preserve unrelated changes and main/NATS/Launcher processes.
- Source changes occur after v7 freeze/resource closure; never hot-edit native acceptance runs.
- Existing scope, explicit-empty permission, network isolation, original budgets, verification, QA and UNKNOWN remain.
- All artifacts/caches/screenshots under owning .polaris; normal I/O through KFS, text UTF-8.
- No new controller/Cell/ledger, legacy admission, server socket, or product polling.

## Review Focus

- URL decoding/traversal/symlink/hidden paths cannot read outside the declared root.
- ESM, CSS and dynamic local resources actually execute; tool ok alone is insufficient.
- Network-denied requests cannot escape via service workers, redirects or other origins/methods.
- Capture/revisit and cold JSON reconstruction retain exact scope and old-pixel identity.
- Static resource budgets and cancellation/drain remain finite and observable.

### Task 0: Canonical KFS runtime-source producer

Owner: ROOT after original helper capacity failure; no other K1 writer.
Files: `src/backend/polaris/kernelone/fs/guarded_regular_file_snapshot.py`, `fs/__init__.py`, new `fs/tests/test_guarded_snapshot_runtime_source.py`.
Produces: public zero-argument `get_guarded_snapshot_runtime_source() -> str`, same canonical stdlib-only module UTF-8 via fixed bounded256KiB package read. Existing guard algorithms do not change. Includes dormant writes but grants no authority; consumer retains sandbox and binds only read.

- [x] Preserve two original API-missing RED cases.
- [x] Add fixed package source getter and public lazy export; parent rechecked actual partial edits after external helper error.
- [x] Verify exact source/stdlib imports, real fresh isolated Python/module registration, ordinary bytes/identity and unsafe path/symlink/size rejection (2 GREEN); adjacent guard/style/type pass.
- [x] Integrate producer with the actual contained browser consumer and independent review/final qualification below.

### Task 1: Tool producer and contained browser consumer

Owner: browser implementer. Exclusive files:
- src/backend/polaris/cells/roles/runtime/internal/engineering_browser.py
- src/backend/polaris/cells/roles/runtime/internal/engineering_roles.py
- src/backend/polaris/cells/roles/runtime/public/engineering_service.py (`_observe_dispatch` saved-argument normalization only)
- src/backend/polaris/cells/roles/runtime/tests/test_engineering_browser_static_origin.py (new)
- src/backend/polaris/cells/roles/runtime/tests/test_engineering_browser_static_origin_service.py (new)

Consumes: current browser_observe/capture_browser_observation, PermissionScope/BrowserPolicy, public get_guarded_snapshot_runtime_source() and existing KFS/process ports.
Produces: optional capture-only web_root in actual ToolSpec schema and normalized/persisted arguments; same tool result with static root/mode evidence; no new public service or DB schema.

- [x] Add a real sandbox browser regression: synthetic nested built HTML loading an absolute ESM asset and CSS; public DOM marker and computed style expected, source/output unchanged. Record RED on baseline.
- [x] Implement explicit root validation and request fulfillment within the current capture process. Guard reads with KFS, single16MiB/total64MiB; GET/HEAD only for the controlled static origin; static mode rejects file URLs, other origins and non-read methods, while old file mode keeps its existing policy. Keep old file mode when omitted.
- [x] Add necessary negative/compatibility/cancel/revisit cases from Review Focus; record GREEN and original mode control.
- [x] Preserve the actual15PASS/2FAIL cold-revisit counterexample: saved raw safe alias `path` is not canonical `entry`. Normalize saved capture arguments through the existing registry/tool normalization before current scope checks; retain original raw provenance, image identity and freshness.
- [x] Test registry/schema JSON reconstruction, service-owned actual browser observation/image producer and cold persisted argument/identity path. Mark fake-model/transport boundaries explicitly.
- [x] Run scoped style/type and adjacent browser/visual/verification/QA/recovery regressions; preserve all first failures. Report exact source hashes and limitations, then STOP writes for independent review.

### Task 2: ROOT integration and independent review

Owner: ROOT docs/governance; independent reviewer read-only. No overlapping writes with Task1.

- [x] Independently verify native v7 613 CORS feedback, actual same-byte rewrite witness and SYNTHETIC controls; freeze/result/quality/cancellation facts remain immutable.
- [x] Read final implementation and producer-consumer tests, audit authority/URL/resource/lifecycle edges, resolve scoped findings with RED/GREEN evidence.
- [x] Synchronize docs/README.md, feature experiment record, roles.runtime README/cell and catalog test declarations. Evaluate subgraphs/descriptor without broad generators; unchanged owner/effects do not require new graph topology.
- [x] Run required architecture and KernelOne ALL, catalog incremental against retained historical baseline, style/type/related pytest from the final candidate. Pre-existing catalog debt stays distinct from new issues.
- [ ] Commit only reviewed authorized files on main. Source qualification is not project delivery.

### Task 3: Frozen fresh original project

Owner: ROOT single native starter and personal quality judge.

- [ ] Prepare a fresh isolated L1-01 with original catalog/host/5400+6000 budgets and the unchanged M3 role configuration from v7; freeze explicit full HEAD/config/operators.
- [ ] Observe actual PM→CE→Director, tools/current verification, current independent QA and physical request/image bindings. No hot edits, target repair or fabricated native evidence.
- [ ] Seal native result, resources and ROOT personal quality. Preserve all failed/cancelled/UNKNOWN and costs; unknown HTTP/bill/human minutes stay unknown.
- [ ] Only evidence-backed first full closure permits widening the serial120 evaluation; other optional feature experiments stay conditional rather than new startup gates.
