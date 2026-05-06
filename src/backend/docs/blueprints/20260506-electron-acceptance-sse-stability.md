# Electron Acceptance SSE Stability Blueprint

Date: 2026-05-06

## Current Understanding

Polaris Electron acceptance currently fails before PM/Director can be validated because the Docs Init preview stream never reaches a terminal UI state. The product path is:

1. Playwright opens the Docs Init dialog.
2. `DocsInitDialog.buildPreview()` starts `/docs/init/preview/stream`.
3. `docs.py` streams LLM progress through `sse_event_generator()`.
4. The UI only shows `docs-init-apply` after a `complete` SSE event normalizes into a valid preview.

The root requirement is not only to pass Playwright, but to make long-running LLM/SSE flows diagnosable and bounded so the desktop app does not leave users in an endless modal.

## Evidence List

Read and traced:

- `src/frontend/src/app/components/DocsInitDialog.tsx`
- `src/frontend/src/hooks/useSSEStream.ts`
- `src/backend/polaris/delivery/http/routers/docs.py`
- `src/backend/polaris/delivery/http/routers/sse_utils.py`
- `src/backend/polaris/cells/llm/dialogue/internal/docs_suggest.py`
- `src/backend/polaris/cells/llm/dialogue/public/service.py`
- `src/backend/docs/graph/catalog/cells.yaml`
- `src/backend/polaris/cells/delivery/api_gateway/cell.yaml`
- `src/backend/polaris/cells/llm/dialogue/cell.yaml`

Runtime evidence:

- `test-results/electron/full-chain-audit-unattende-eaf39-trong-JSON-evidence-package/trace.zip`
- `C:/Temp/Polaris_ETMS_Stress_E2E_mou1mb5b/.polaris/docs/product/requirements.md`
- `C:/Temp/Polaris_ETMS_Stress_E2E_mou1mb5b/.polaris/pm_data/tasks/registry.json`

Confirmed:

- The first UI defect was a z-index collision: `LlmRuntimeOverlay` used `z-[75]`, above Docs Init modal `z-50`.
- After lowering the overlay, Playwright successfully clicked `docs-init-build-preview`.
- The next failure is waiting for `docs-init-apply`; the trace shows repeated polling for `docs-init-apply` until the test-level timeout closes the page.
- `sse_event_generator()` only turns `RuntimeError` and `ValueError` into terminal error events.
- `generate_docs_fields_stream()` also only catches `RuntimeError` and `ValueError`.
- If the LLM stream raises a different exception or never yields a terminal event, the frontend remains in `loadingPreview` and the modal never reaches Apply.

Still unconfirmed:

- The exact external provider failure mode in the latest run. The code path proves that multiple common provider failures can produce the same indefinite UI symptom.

## Defect Analysis

### Defect 1: Runtime overlay above modals

Trigger: LLM Runtime overlay expands while Docs Init dialog is open.

Root cause: floating runtime overlay used a z-index above modal overlays, and its inner container re-enabled pointer events.

Impact: modal actions become unclickable in real Electron.

### Defect 2: SSE stream can fail without a terminal event

Trigger: LLM provider stream raises non-`RuntimeError`/`ValueError`, times out outside the narrow catch, or returns no terminal result.

Root cause: the generic SSE wrapper assumes task functions always push `complete` or `error`, but does not enforce that contract for broad exceptions or completed tasks with errors.

Impact: frontend keeps waiting, Playwright times out, and the user gets no actionable failure state.

### Defect 3: Docs preview has no bounded LLM fallback

Trigger: Architect LLM is slow, blocked, or returns no parseable result during initial documentation.

Root cause: Docs preview treats live LLM enrichment as mandatory in the streaming path even though the product already has deterministic default document fields.

Impact: a transient external LLM problem prevents docs bootstrapping and cascades into PM seeing zero tasks.

## Fix Plan

Minimal changes:

1. Keep the overlay z-index fix and test already added.
2. Harden `sse_event_generator()` so every task failure becomes a terminal `error` event.
3. Expose a small public `build_default_docs_fields()` helper from the `llm.dialogue` cell instead of importing an internal helper across Cell boundaries.
4. Add a bounded Docs preview LLM collection helper in `docs.py`.
5. On docs preview LLM timeout/error/no-result, emit a warning stage and complete with deterministic fallback fields instead of leaving the stream open.

Non-goals:

- No rewrite of the LLM provider runtime.
- No change to provider configuration semantics.
- No target-project edits.
- No broad PM/Director behavior changes until the docs-init blocker is verified.

Compatibility:

- Public HTTP response shape remains the same.
- New fallback only affects failure/timeout/no-result paths that currently hang or fail the bootstrap flow.
- A new additive public helper is exported from `llm.dialogue`; existing callers are unaffected.

## Test Plan

Happy path:

- Existing docs preview tests still pass.

Edge cases:

- Docs preview stream completes with fallback when LLM stream produces no result.
- Docs preview stream completes with fallback when LLM stream emits an error.

Exception cases:

- Generic SSE wrapper emits terminal `error` for unexpected task exceptions.
- Docs field stream returns an error event for unexpected provider exceptions.

Regression cases:

- Overlay stays below modal dialogs.
- Real Electron acceptance proceeds past Docs Init preview and writes concrete docs before PM planning.

## Rollback Plan

Files expected to change:

- `src/frontend/src/app/components/LlmRuntimeOverlay.tsx`
- `src/frontend/src/app/components/LlmRuntimeOverlay.test.tsx`
- `src/backend/polaris/delivery/http/routers/sse_utils.py`
- `src/backend/polaris/delivery/http/routers/docs.py`
- `src/backend/polaris/cells/llm/dialogue/internal/docs_suggest.py`
- `src/backend/polaris/cells/llm/dialogue/public/service.py`
- focused tests under `src/backend/polaris/...`

Rollback:

- Revert the listed file changes.
- Re-run focused frontend/backend tests.
- Re-run `KERNELONE_E2E_USE_REAL_SETTINGS=1 npm run test:e2e:acceptance`.

Review focus after rollback or forward fix:

- Docs Init no longer hangs.
- Fallback documents contain a non-empty goal, acceptance criteria, and backlog.
- PM task snapshot is populated after docs are applied.
