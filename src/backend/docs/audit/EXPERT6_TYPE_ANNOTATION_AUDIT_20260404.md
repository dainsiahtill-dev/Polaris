# Type Annotation Coverage Audit - Expert 6 Report

**Date:** 2026-04-04  
**Scope:** polaris/kernelone/, polaris/cells/, polaris/domain/, polaris/infrastructure/, polaris/delivery/  
**Thoroughness:** VERY THOROUGH

---

## Executive Summary

This audit identifies type annotation gaps and inconsistencies across the polaris codebase.

**Key Findings:**
- 153+ files contain functions without type annotations
- Multiple files use high concentrations of Any type
- Inconsistent use of Optional[X] vs X | None
- Multiple type: ignore patterns masking protocol assignment issues

---

## [TYPE-001] invoke_stream Returns Untyped Async Generator

**File:** polaris/kernelone/llm/engine/executor.py:183
**Problem:** The async generator method has no return type annotation.
**Severity:** HIGH
**Recommendation:** Add: async def invoke_stream(self, request: AIRequest) -> AsyncIterator[StreamEvent]:

---

## [TYPE-002] High Any Usage in build_repo_map

**File:** polaris/kernelone/context/repo_map.py:59
**Problem:** Function returns dict[str, Any] without a proper TypedDict.
**Severity:** HIGH
**Recommendation:** Create a TypedDict or dataclass for the return type.

---

## [TYPE-003] Untyped ToolFn Callable Alias

**File:** polaris/kernelone/llm/toolkit/executor/runtime.py:23
**Problem:** ToolFn uses untyped Callable with dict[str, Any] return.
**Severity:** MEDIUM
**Recommendation:** Replace with explicit Protocol.

---

## [TYPE-004] _load_stripper Returns Untyped Any

**File:** polaris/kernelone/context/ports.py:197
**Problem:** The method returns Any from dynamic import.
**Severity:** MEDIUM
**Recommendation:** Add comment explaining dynamic import.

---

## [TYPE-005] Untyped Parameters in BackendToolRuntime.invoke

**File:** polaris/kernelone/llm/toolkit/executor/runtime.py:56-62
**Problem:** cwd: Any and timeout: Any lack type annotations.
**Severity:** MEDIUM
**Recommendation:** Type cwd as str | Path and timeout as float | int.

---

## [TYPE-006] SubagentSpawner.__init__ Untyped llm_client Parameter

**File:** polaris/kernelone/agent/subagent_runtime.py:61-64
**Problem:** llm_client=None has no type annotation.
**Severity:** MEDIUM
**Recommendation:** Add explicit type annotation.

---

## [TYPE-007] _active_subagents Uses High-Concentration Any

**File:** polaris/kernelone/agent/subagent_runtime.py:84
**Problem:** dict[str, Any] uses Any for values.
**Severity:** MEDIUM
**Recommendation:** Create a SubagentHandle dataclass.

---

## [TYPE-008] TaskRuntimeService.__getattr__ Returns Untyped Any

**File:** polaris/cells/runtime/task_runtime/internal/service.py:58
**Problem:** __getattr__ returns Any bypassing type checking.
**Severity:** HIGH
**Recommendation:** Add comment explaining intentional compatibility proxy.

---

## [TYPE-009] MessageHandler/AsyncMessageHandler Untyped Callbacks

**File:** polaris/kernelone/events/message_bus.py:145-146
**Problem:** AsyncMessageHandler returns Any instead of Awaitable[None].
**Severity:** MEDIUM
**Recommendation:** Use Awaitable[None] as return type.

---

## [TYPE-010] pm_chat_stream Missing Return Type

**File:** polaris/delivery/http/routers/pm_chat.py:75
**Problem:** FastAPI endpoint has no return type.
**Severity:** MEDIUM
**Recommendation:** Add return type StreamingResponse.

---

## [TYPE-011] Untyped Callback in _run_pm_dialogue

**File:** polaris/delivery/http/routers/pm_chat.py:101
**Problem:** queue parameter is untyped.
**Severity:** LOW
**Recommendation:** Type queue contents properly.

---

## [TYPE-012] run_verify Returns Untyped dict[str, Any]

**File:** polaris/infrastructure/accel/verify/verify/core.py:99-103
**Problem:** Returns untyped dictionary.
**Severity:** HIGH
**Recommendation:** Create VerifyRunResult dataclass.

---

## [TYPE-013] DirectorConfig.__post_init__ Missing Return Type

**File:** polaris/cells/director/execution/service.py:97
**Problem:** __post_init__ has no return type.
**Severity:** LOW
**Recommendation:** Add -> None return type.

---

## [TYPE-014] Protocol Methods Return Any

**File:** polaris/kernelone/context/ports.py:43-76
**Problem:** Protocol methods return Any | None.
**Severity:** MEDIUM
**Recommendation:** Create WorkflowDefinition type alias.

---

## [TYPE-015] _invoke_with_timeout Untyped Parameter

**File:** polaris/kernelone/llm/engine/executor.py:86
**Problem:** coro parameter has no type.
**Severity:** MEDIUM
**Recommendation:** Add Coroutine[Any, Any, Any] type.

---

## [TYPE-016] Inconsistent Optional vs | Style

**Files:** Multiple across polaris/
**Problem:** Mixed Optional[X] and X | None usage.
**Severity:** LOW
**Recommendation:** Standardize on X | None style.

---

## [TYPE-017] compact Method Uses Untyped Dictionary

**File:** polaris/domain/services/llm_compact_service.py:77-81
**Problem:** list[dict[str, Any]] uses untyped dict.
**Severity:** MEDIUM
**Recommendation:** Create Message TypedDict.

---

## [TYPE-018] High type: ignore for Protocol Assignments

**Files:** Multiple akashic files
**Problem:** __protocol__ = SomePort  # type: ignore[attr-defined] masks issues.
**Severity:** HIGH
**Recommendation:** Use hasattr() checks or type-safe injection.

---

## [TYPE-019] WorkerPool Classes Missing Return Annotations

**File:** polaris/cells/roles/runtime/internal/worker_pool.py
**Problem:** Multiple methods lack return type annotations.
**Severity:** MEDIUM
**Recommendation:** Add return type annotations to all public methods.

---

## [TYPE-020] ToolHandlerRegistry Protocol Untyped Internals

**File:** polaris/kernelone/llm/toolkit/executor/handlers/registry.py:89-91
**Problem:** ToolHandler Protocol has untyped internals.
**Severity:** LOW
**Recommendation:** Ensure implementations satisfy Protocol.

---

## Type Annotation Coverage Statistics

| Module | Files with Missing Annotations | Coverage % (est.) |
|--------|-------------------------------|-------------------|
| kernelone/ | ~80 | ~65% |
| cells/ | ~70 | ~70% |
| domain/ | ~15 | ~75% |
| infrastructure/ | ~35 | ~70% |
| delivery/ | ~40 | ~60% |

---

## Top Priority Fixes

1. **[TYPE-001]** Add return type to invoke_stream in AIExecutor
2. **[TYPE-008]** Document TaskRuntimeService.__getattr__ return type
3. **[TYPE-012]** Create typed result class for run_verify
4. **[TYPE-018]** Address Protocol assignment type: ignore pattern
5. **[TYPE-002]** Create TypedDict for build_repo_map return type

---

## Appendix: Files Requiring Immediate Attention

1. polaris/kernelone/llm/engine/executor.py
2. polaris/kernelone/context/repo_map.py
3. polaris/kernelone/agent/subagent_runtime.py
4. polaris/kernelone/context/ports.py
5. polaris/cells/runtime/task_runtime/internal/service.py
6. polaris/kernelone/events/message_bus.py
7. polaris/delivery/http/routers/pm_chat.py
8. polaris/infrastructure/accel/verify/verify/core.py
9. polaris/cells/director/execution/service.py
10. polaris/kernelone/llm/toolkit/executor/runtime.py

