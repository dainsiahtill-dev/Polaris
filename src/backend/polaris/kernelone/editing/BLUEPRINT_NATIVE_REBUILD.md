# KernelOne Editing Native Rebuild Blueprint

Status: active (vendor cleanup completed)
Owner: KernelOne / editing
Scope: `polaris/kernelone/editing/**`

## Goal

Build and keep editing runtime as Polaris-native modules under
`polaris/kernelone/editing`.
Reference implementations can be used from external Aider sources, but no
vendored runtime tree is allowed in-repo.

## Hard Rules

1. No runtime imports from `polaris.kernelone.editing.vendor.*`.
2. Runtime editing behavior must live in native modules only.
3. Every migration step is file-level, test-backed, and reversible.
4. UTF-8 is mandatory for all text I/O.

## Migration Sequence (file-level)

### Wave A: Core editing behavior (highest priority)

1. `coders/search_replace.py` -> `editing/search_replace_engine.py` (started)
2. `coders/editblock_coder.py` -> `editing/editblock_engine.py`
3. `coders/udiff_coder.py` + `patch_coder.py` -> `editing/patch_engine.py`
4. `coders/wholefile_coder.py` -> `editing/wholefile_engine.py`
5. `diffs.py` -> `editing/diff_utils.py`

### Wave B: Prompt/operation contracts

1. `coders/base_prompts.py` + `*_prompts.py` -> `editing/prompts/`
2. `coders/chat_chunks.py` -> reuse `kernelone/context/chunks` or provide adapter
3. `editor.py` -> `editing/operation_router.py`
4. `exceptions.py` -> `editing/errors.py`

### Wave C: Optional capabilities (only if still needed)

1. `linter.py` -> keep out of editing core unless required by apply path
2. `repo.py` + `repomap.py` -> prefer existing `kernelone/context/repo_intelligence`
3. `run_cmd.py` -> prefer existing process runtime, do not duplicate

## Explicit Non-Goals

Do not migrate product/UI/runtime shell modules into editing core:

- `io.py`, `mdstream.py`, `analytics.py`, `openrouter.py`, `llm.py`, `sendchat.py`, `waiting.py`
- model registry/config assets not needed for code-apply path

If a capability is needed later, rebuild it against KernelOne contracts, do not re-export vendor code.

## Exit Criteria for Vendor Deletion

1. All required editing features have native modules and tests.
2. `test_editing_vendor_quarantine.py` passes (no runtime vendor imports).
3. Protocol apply path uses only native editing modules.
4. `polaris/kernelone/editing/vendor/*` deleted from repository (DONE 2026-03-25).
