# KernelOne Convergence Audit

Status: `wave-1 completed`
Date: `2026-03-21`
Scope: `src/backend/polaris/kernelone/**`
Baseline: `docs/FINAL_SPEC.md` + `docs/KERNELONE_ARCHITECTURE_SPEC.md`

## 1. Boundary Analysis

KernelOne in Polaris is not a generic utils bucket. It is the Agent/AI operating substrate.
That means the highest-priority defects are not cosmetic issues. They are defects that:

1. Break deterministic import/runtime behavior
2. Hide failures behind silent fallback or path mutation
3. Re-couple KernelOne to deleted legacy entrypoints or to infrastructure internals
4. Prevent future Cell/plugin governance from treating KernelOne as a stable SDK surface

This audit therefore prioritized:

1. `sys.path` mutation and import-time fallback hacks
2. Deterministic runtime bugs in high-trust subsystems
3. Dead legacy entrypoints
4. Missing architecture guard tests

## 2. Evidence Snapshot

Static scan snapshot after wave-1 fixes:

1. `polaris/kernelone/**/*.py` files: `176`
2. `sys.path.insert/append` in KernelOne: `0`
3. `except Exception` occurrences: `242`
4. `pass` occurrences: `100`
5. Remaining direct platform-layer leaks found by scan: `2`

Remaining direct platform-layer leaks:

1. `polaris/kernelone/llm/providers/__init__.py`
   `kernelone -> infrastructure.llm.providers`
2. `polaris/kernelone/llm/toolkit/executor.py`
   `kernelone -> infrastructure.realtime.process_local.message_event_fanout`

Hotspot files by size and likely future refactor pressure:

1. `polaris/kernelone/llm/toolkit/parsers.py`
2. `polaris/kernelone/llm/toolkit/executor.py`
3. `polaris/kernelone/tools/io_utils.py`
4. `polaris/kernelone/llm/toolkit/protocol_kernel.py`
5. `polaris/kernelone/task_graph/task_board.py`
6. `polaris/kernelone/process/background_manager.py`

## 3. Wave-1 Fixes Landed

### 3.1 Import hygiene and path mutation removal

Fixed modules:

1. `polaris/kernelone/fs/jsonl/ops.py`
2. `polaris/kernelone/memory/memory_store.py`
3. `polaris/kernelone/memory/reflection.py`
4. `polaris/kernelone/runtime/lifecycle.py`
5. `polaris/kernelone/process/ollama_utils.py`
6. `polaris/kernelone/tools/runtime_executor.py`

What changed:

1. Removed `sys.path` mutation and pseudo-standalone fallback logic
2. Replaced duplicated import fallback branches with deterministic behavior
3. Kept optional dependency degradation explicit instead of mutating interpreter state

### 3.2 Deterministic bug fixes

Fixed confirmed runtime defects:

1. `polaris/kernelone/memory/reflection.py`
   Normal import path did not import `os`, which could trigger `NameError` in `ReflectionStore._load()`.
2. `polaris/kernelone/memory/reflection.py`
   `ReflectionGenerator.generate()` passed structured Ollama response objects directly into JSON parsing; this could fail because the parser expected string-like input.
3. `polaris/kernelone/tools/runtime_executor.py`
   Runtime still attempted to load deleted `tools.main`, which meant shared backend tool registration could silently collapse to an empty set.

### 3.3 Test boundary cleanup

Fixed test-only architectural coupling:

1. `polaris/kernelone/fs/tests/test_kernel_filesystem.py`
   Removed direct dependency on `polaris.infrastructure.storage.LocalFileSystemAdapter`.
   KernelOne tests now use a local protocol-compatible adapter.

### 3.4 New regression guards

Added or strengthened tests:

1. `tests/architecture/test_kernelone_boundary_clean_subsystems.py`
   High-trust subsystems now fail if they mutate `sys.path`.
2. `tests/test_kernelone_reflection.py`
   Covers reflection file load and structured Ollama response handling.
3. `tests/test_kernelone_runtime_executor.py`
   Covers direct tool exposure and `cwd`-scoped invocation after removal of `tools.main`.

## 4. Validation Performed

Passed validations:

1. `python -m py_compile` on all changed modules and new tests
2. `pytest -q tests/test_kernelone_reflection.py`
3. `pytest -q tests/test_kernelone_runtime_executor.py`
4. `pytest -q tests/architecture/test_kernelone_boundary_clean_subsystems.py`
5. `pytest -q polaris/kernelone/fs/tests/test_kernel_filesystem.py`
6. `pytest -q tests/test_io_utils_logical_paths.py`
7. `pytest -q tests/test_task_board_concurrency.py`
8. `pytest -q tests/test_llm_toolkit_executor_safety.py`
9. `pytest -q tests/test_llm_toolkit_executor_file_events.py`
10. `pytest -q tests/test_llm_toolkit_native_function_calling.py`

Known limitation during broad test collection:

1. `pytest -q tests -k kernelone` did not complete because unrelated collection errors currently exist in non-KernelOne areas:
   - `tests/test_llm_qualification_validators.py`
   - `tests/test_pm_service_lifecycle_lock.py`
   - `tests/test_roles_kernel.py`

These are repository-level migration issues, not regressions introduced by wave-1 KernelOne fixes.

## 5. Remaining High-Risk Backlog

### P0

1. Remove `kernelone -> infrastructure` leak in `polaris/kernelone/llm/providers/__init__.py`
2. Remove `kernelone -> infrastructure` leak in `polaris/kernelone/llm/toolkit/executor.py`
3. Replace broad-exception usage on hot runtime paths:
   - `polaris/kernelone/task_graph/task_board.py`
   - `polaris/kernelone/process/background_manager.py`
   - `polaris/kernelone/llm/toolkit/executor.py`
   - `polaris/kernelone/llm/engine/executor.py`

### P1

1. Split `polaris/kernelone/tools/io_utils.py` by responsibility
2. Reduce `except Exception` in `fs`, `runtime`, `task_graph`, `llm.engine`
3. Standardize failure logging and error taxonomy instead of silent `pass`

### P2

1. Add subsystem-level admission records for each KernelOne package
2. Add architecture tests for forbidden platform-layer imports across all of `polaris/kernelone`
3. Build a formal bootstrap registration path for provider/runtime integrations so KernelOne no longer relies on compatibility shims

## 6. Conclusion

Wave-1 did not claim full KernelOne completion.

What it did complete is the first mandatory convergence step:

1. High-trust subsystems no longer mutate interpreter import state
2. A deleted legacy tool entrypoint is replaced by a working in-process executor path
3. Reflection runtime has deterministic import and parsing behavior
4. KernelOne now has explicit regression tests for these failure classes

KernelOne is healthier after this wave, but it is not yet fully converged.
The next hard boundary to close is provider/runtime decoupling and broad-exception reduction on hot execution paths.
