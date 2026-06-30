# Kernel Services Migration Guide

**Migration Date**: 2026-03-31
**Scope**: `polaris/cells/roles/kernel/internal/llm_caller/`

## Summary

The LLM caller modules have been consolidated from standalone files into a unified service class. This migration improves maintainability and provides a cleaner API for LLM invocation.

### Changes

| Before | After | Status |
|--------|-------|--------|
| `call_sync.py` | `LLMInvoker.call()` | Removed |
| `call_structured.py` | `LLMInvoker.call_structured()` | Removed |
| `call_stream.py` | `LLMInvoker.call_stream()` | Removed |
| - | `LLMInvoker` (new service class) | Added |

## Migration Instructions

### For Existing Code Using `LLMCaller`

`LLMCaller` has been retired by the LS-06G convergence cut. Existing code must
migrate to `LLMInvoker` or, for request construction tests, `LLMRequestPreparer`.
Direct imports from `polaris.cells.roles.kernel.internal.llm_caller.caller` are
treated as architecture drift.

### For New Code

Use `LLMInvoker` directly:

```python
# New code (recommended)
from polaris.cells.roles.kernel.internal.llm_caller import LLMInvoker

invoker = LLMInvoker(workspace=".")
response = await invoker.call(profile, system_prompt, context)
```

### Method Mapping

| Retired Method | Replacement | Notes |
|------------|------------|-------|
| `LLMCaller.call()` | `LLMInvoker.call()` | Use the canonical invocation service |
| `LLMCaller.call_structured()` | `LLMInvoker.call_structured()` | Use the canonical invocation service |
| `LLMCaller.call_stream()` | `LLMInvoker.call_stream()` | Use the canonical invocation service |

## API Compatibility

### `LLMInvoker` Class

```python
class LLMInvoker:
    def __init__(self, workspace: str = "", enable_cache: bool = True) -> None:
        ...

    async def call(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        response_model: type | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        prompt_fingerprint: str | None = None,
        platform_retry_max: int = 1,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        event_emitter: Any | None = None,
    ) -> LLMResponse:
        ...

    async def call_structured(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        response_model: type,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        max_retries: int = 3,
        prompt_fingerprint: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        event_emitter: Any | None = None,
    ) -> StructuredLLMResponse:
        ...

    async def call_stream(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        event_emitter: Any | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        ...
```

## Import Changes

### Before

```python
# These imports no longer work
from polaris.cells.roles.kernel.internal.llm_caller.call_sync import call
from polaris.cells.roles.kernel.internal.llm_caller.call_structured import call_structured
from polaris.cells.roles.kernel.internal.llm_caller.call_stream import call_stream
```

### After

```python
# Use the unified service class
from polaris.cells.roles.kernel.internal.llm_caller import LLMInvoker

invoker = LLMInvoker(workspace=".")
response = await invoker.call(profile, system_prompt, context)
structured_response = await invoker.call_structured(profile, system_prompt, context, MyModel)
async for event in invoker.call_stream(profile, system_prompt, context):
    ...
```

## Backward Compatibility

There is no runtime compatibility facade for `LLMCaller`. Backward-compatible
behavior is covered at the canonical boundaries:

- `LLMInvoker` owns provider invocation, stream invocation, fallback, cache, and
  event emission behavior.
- `LLMRequestPreparer` owns final provider request preparation, tool schema
  projection, response format projection, and execution-envelope metadata.
- `LLMEventEmitter` owns call lifecycle event emission.

## Testing

Run the test suite to verify the migration:

```bash
# Run canonical LLM invocation/request-preparation tests
python -m pytest \
  polaris/cells/roles/kernel/tests/test_llm_caller_helpers.py \
  polaris/cells/roles/kernel/tests/test_llm_invoker_decomposition_characterization.py \
  polaris/cells/roles/kernel/tests/test_llm_invoker_role_binding_fallback.py \
  polaris/cells/roles/kernel/tests/test_llm_invoker_final_request_receipt.py \
  polaris/cells/roles/kernel/tests/test_llm_caller_capability_profile.py \
  -v

# Run all kernel tests
python -m pytest polaris/cells/roles/kernel/tests/ -v
```

## Rollback Plan

If issues are encountered:

1. The old files are not in git history (they were deleted)
2. To rollback, restore from backup or revert the git commit
3. `LLMCaller` compatibility is intentionally not restored during rollback-less
   forward fixes; repair callers at `LLMInvoker` / `LLMRequestPreparer` instead.

## Benefits

1. **Simplified Architecture**: Single service class instead of multiple standalone modules
2. **Better Encapsulation**: Related functionality grouped in a class
3. **Easier Testing**: Single entry point for LLM invocation
4. **Clearer API**: Explicit service class with documented methods
5. **Maintainability**: Easier to understand and modify

## Related Documentation

- `polaris/cells/roles/kernel/internal/llm_caller/__init__.py` - Package exports
- `polaris/cells/roles/kernel/internal/llm_caller/invoker.py` - Invocation service implementation
- `polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py` - Final provider request preparation

## Contact

For questions about this migration, contact the Kernel team or refer to the
architecture documentation in `docs/AGENT_ARCHITECTURE_STANDARD.md`.
