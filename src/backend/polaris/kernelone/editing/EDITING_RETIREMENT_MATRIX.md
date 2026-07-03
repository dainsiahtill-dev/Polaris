# KernelOne Editing Retirement Matrix

Status: active
Decision: `polaris/kernelone/editing/* + polaris.kernelone.llm.toolkit.protocol` is the canonical editing stack.

## Canonical Path

1. LLM output -> `polaris.kernelone.llm.toolkit.protocol`
2. Rich format routing -> `polaris.kernelone.editing.operation_router`
3. Unified apply -> `StrictOperationApplier` / `apply_protocol_output`

## Retired Modules to Downgrade

1. `polaris/cells/roles/kernel/internal/output_parser.py`
- Keep as compatibility parser facade only.
- Must not own main patch semantics.
- Any retired regex fallback is deprecation-only and should emit warning.

2. `polaris/cells/director/execution/internal/patch_apply_engine.py`
- Keep as thin shim to `polaris.kernelone.llm.toolkit.protocol`.
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
- Retired compatibility only.
- Must not be used for canonical apply.

## Guardrail

New editing features must land in `polaris/kernelone/editing/*` and be consumed by `polaris.kernelone.llm.toolkit.protocol`.
Do not add new primary editing behavior in retired modules above.
