# KernelOne Editing Legacy Downgrade Matrix

Status: active
Decision: `polaris/kernelone/editing/* + protocol_kernel` is the canonical editing stack.

## Canonical Path

1. LLM output -> `polaris.kernelone.llm.toolkit.protocol_kernel`
2. Rich format routing -> `polaris.kernelone.editing.operation_router`
3. Unified apply -> `StrictOperationApplier` / `apply_protocol_output`

## Legacy Modules to Downgrade

1. `polaris/cells/roles/kernel/internal/output_parser.py`
- Keep as compatibility parser facade only.
- Must not own main patch semantics.
- Any legacy regex fallback is deprecated-only and should emit warning.

2. `polaris/cells/director/execution/internal/patch_apply_engine.py`
- Keep as thin shim to `protocol_kernel`.
- No new parsing/apply logic allowed.

3. `polaris/cells/director/execution/internal/file_apply_service.py`
- Keep delivery/application boundary role only.
- Apply semantics must delegate to `apply_protocol_output`.

4. `polaris/cells/roles/adapters/internal/director_adapter.py`
- Local PATCH_FILE execution fallback is compatibility-only.
- Main path must use unified kernel apply.

5. `polaris/kernelone/prompts/utils.py` and `polaris/kernelone/prompts/catalog.py`
- Prompt-time validation only.
- Must not become execution-time parser truth.

6. `polaris/kernelone/runtime/shared_types.py` PATCH regex assets
- Legacy compatibility only.
- Must not be used for canonical apply.

## Guardrail

New editing features must land in `polaris/kernelone/editing/*` and be consumed by `protocol_kernel`.
Do not add new primary editing behavior in legacy modules above.

