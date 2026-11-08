# Host-selected engineering browser DOM observation

Date: 2026-11-05. Task: DOM-BROWSER-TEXT-MODE. Classification: pattern.

## Intent and scope
Text-only model endpoints reject image content. Existing browser_observe executes actual readonly contained Chromium and already observes final DOM, controls, console, page errors and static resource reads, but always captures images and prompts image inspection. Add host-only observation_mode=images|dom, default images. Reuse EngineeringRunService, its saved PermissionScope and SQLite journal, ToolSpecRegistry normalization, KFS path guards, and KernelOne process containment. No second owner, target rewrite, new service, product dependency installation, polling or LLM fallback.

## Decisions
DOM mode uses the same page load/actions and bounded waits. It skips screenshot capture entirely and returns only final actual DOM/controls and existing console/page-error/network/static-resource evidence. Explicit mode, final-observation timing and visual_evidence=false distinguish it from per-frame visual observations. It makes no visual-correctness or acceptance judgment. Existing frame_times_ms stays compatible: in DOM mode only the final offset controls final DOM observation; no intermediate DOM snapshots are claimed. Browser observation success means capture succeeded; page_errors remain visible failures of the observed app, never an app-success verdict.

Host config and frozen contract reject dom plus require_visual_review. Missing historical field decodes as images. Mode persists in saved scope, equality and current-policy revalidation. Role prompt names active mode. Shared ToolSpec describes both host-selected modes without changing global registry per request. DOM mode rejects historical image revisit, preventing image attachments through saved capture references. Default images retains existing image history, integrity checks, pinning, freshness and visual gates.

## Assumptions and risks
Existing DOM summary is sufficient for this increment; not arbitrary JS evaluation, full accessibility tree, animation inspection or remote server access. Capture runs only through existing readonly/no-network OS sandbox and owned process drain. DOM-only checks cannot prove canvas appearance or visual correctness. Older saved image runs retain original image mode; host mode changes cannot upgrade or silently mutate saved authority.

## Architecture and complexity
Owner remains roles.runtime; bootstrap maps host configuration. No dependency/state/effect boundary changes, graph additions or descriptor generation. Existing roles_execution_pipeline and execution_governance_pipeline declarations cover this behavior. Add one finite enum branch around image capture and validation, one prompt branch, one revisit guard; existing action/DOM/resource loops retain bounds and asymptotic complexity. No additional persistent state beyond optional scope field.

## Verification plan
New test_engineering_browser_dom_mode.py: contract/config invalid combinations; profile mapping; cold scope roundtrip and mode conflict; actual sandboxed Chromium final controls and CommonJS page error; no images and unchanged target bytes; default images still JPEG; active prompt and shared schema; DOM image revisit rejection. Existing browser/static-origin/visual persistence and role regressions. Ruff check/format and mypy touched source. Parent performs independent native model acceptance; component tests cannot establish project delivery.

## Implementation qualification
Default image scopes omit observation_mode in serialized JSON, preserving historical idempotency bytes; DOM scopes persist the explicit mode. Current host policy cannot switch a saved mode. DOM tool observations still run the same before/after input-binding and cancellation checks, but do not emit visual_observation_completed. The existing tool_observed record carries their actual DOM evidence.

The three existing browser fixture configurations now accept explicit POLARIS_TEST_BROWSER_EXECUTABLE, POLARIS_TEST_BROWSER_PYTHON and POLARIS_TEST_BROWSER_PACKAGES; historical defaults remain unchanged. This permits current installed runtimes without fake browser/process implementations. Optional Pillow 13.0.0 for pre-existing image decode assertions was installed only under the external audit tools/browser-test-deps directory, not project dependency files or .venv.

Full Chromium1248 exposed an existing timeout process-disappearance assertion failure; pristine HEAD browser capture reproduced it using the same real KernelOne sandbox/process. The observed process still existed in D state at the assertion, then disappeared; D/vsize0 is not accepted as proof of drain. Full Chromium also missed an existing 150ms static HEAD observation and exceeded the existing90s cold-reinspection deadline. Headless shell1248 reran the physical cancellation, timeout and static HEAD cases successfully without changing assertions. All first-failure evidence remains in the external report. No process lifecycle production code was changed.

Ruff and formatting cover all touched Python files. Mypy reports two independently reproduced pristine-HEAD errors: PermissionScope.command_cache unreachable condition and bootstrap ProviderAdapter protocol mismatch; those unrelated code paths remain unchanged. Full repository/KernelOne release gates and fresh native model delivery are parent integration responsibilities, not claimed by this component change.

Final qualification: 43 focused DOM/persistence/service/visual-loop tests passed. Existing image cold reinspection passed in56.73s with installed headless shell1248 and original /usr/bin/python3 3.12 plus existing .pyenv3.12 Playwright packages; the original90s deadline stayed unchanged. This distinguishes the expanded3.13 fixture runtime scope from a saved-image compatibility failure. Source artifacts and exact grouped results are in /home/dains/.polaris/bench_runs/qwen38-all120-20261105/dom-browser-support.json. Fresh native text-only model acceptance remains pending parent work.
