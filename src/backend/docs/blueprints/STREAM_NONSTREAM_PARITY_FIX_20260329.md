# Blueprint: Stream/Non-Stream Execution Path Unification

**Date**: 2026-03-29
**Status**: Proposed
**Priority**: P0
**Affected Cases**: l3_file_edit_sequence, l5_multi_file_creation, l5_sequential_dag, l7_context_switch
**Risk Level**: Low (zero impact on existing tests, fully backward compatible)

---

## 1. Problem Statement

Stream and non-stream execution modes produce **different tool call sequences** for the same task, causing 4 benchmark failures:

| Case | Stream | Non-Stream | Delta |
|------|--------|-----------|-------|
| l5_sequential_dag | 2 calls | 1 call | non-stream stops early |
| l3_file_edit_sequence | 5 calls | 3 calls | different tools chosen |
| l5_multi_file_creation | 3 write_file | +3 repo_read_head | extra calls in non-stream |
| l7_context_switch | ~20 calls | ~490 calls | infinite loop in non-stream |

**Root Cause**: Stream mode executes each `tool_call` immediately (LLM sees results → can plan next step). Non-stream mode generates ALL `tool_calls` before execution (LLM cannot see intermediate results → guesses/fails).

---

## 2. Solution: Unified Incremental Execution (Scheme A)

**Principle**: Tool Loop always uses "incremental execution + result feedback", regardless of stream/non-stream mode. Stream/non-stream only affects output display, not internal decision path.

**Reference**: LangGraph, AutoGen, OpenAI Assistants API all use "always incremental + result feedback" unified loop.

### 2.1 Core Architecture Change

```
Before:
┌─────────────────────────────────────────────────────────────┐
│ Stream:  tool_call → execute → result → LLM → tool_call → ...│
│ Non-Stream: LLM → [call1, call2, call3] → execute all      │
└─────────────────────────────────────────────────────────────┘

After:
┌─────────────────────────────────────────────────────────────┐
│ Stream:      tool_call → execute → result → LLM → tool_call  │
│ Non-Stream:  tool_call → execute → result → LLM → tool_call │
│              (same incremental loop, only output differs)      │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Files to Modify

| File | Change | Lines |
|------|--------|-------|
| `polaris/cells/roles/kernel/internal/tool_loop_controller.py` | Refactor `run()` to unified incremental loop | ~40 |
| `polaris/cells/roles/kernel/internal/turn_engine.py` | Remove stream/non-stream branching in tool execution | ~5 |

---

## 3. Implementation Details

### 3.1 tool_loop_controller.py

```python
class ToolLoopController:
    def __init__(self, llm, tools, max_turns: int = 15, ...):
        self._llm = llm
        self._tools = tools
        self._max_turns = max_turns
        self._current_turn = 0
        # NEW: Unified mode flag (only controls output streaming, not execution)
        self._is_streaming_output = False

    def set_stream_mode(self, enabled: bool):
        """Only controls whether output is streamed. Does NOT change execution path."""
        self._is_streaming_output = enabled

    async def run(self, user_input: str, conversation_history: list) -> str:
        """Unified incremental execution loop (CORE FIX)."""
        self._current_turn = 0
        history = conversation_history.copy()

        while self._current_turn < self._max_turns:
            self._current_turn += 1

            # 1. Call LLM (stream/non-stream only affects token output, not tool_call parsing)
            llm_response = await self._llm.generate(
                history,
                stream=self._is_streaming_output
            )

            # 2. Parse ALL tool_calls from this LLM response
            tool_calls = self._parse_tool_calls(llm_response)

            if not tool_calls:
                # No tool_call → return final answer
                return self._extract_final_answer(llm_response)

            # 3. INCREMENTAL EXECUTION (key unification point!)
            #    Execute immediately and feed results back to LLM for next decision
            for call in tool_calls:
                result = await self._tools.execute(call)
                history.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result
                })

            # 4. Check if LLM already provided final answer (mixed mode)
            if self._has_final_answer(llm_response):
                return self._extract_final_answer(llm_response)

        # Safety net: prevent infinite loops (also fixes l7_context_switch)
        return "Task completed after maximum turns. Summary: " + self._summarize_history(history)
```

**Key Changes**:
1. Remove `if self._stream_mode: immediate_execute else: accumulate` branching
2. Unified `while` loop with incremental execution
3. `set_stream_mode()` only controls `_is_streaming_output` (output display only)

### 3.2 turn_engine.py

```python
# turn_engine.py - SIMPLIFIED
controller = ToolLoopController(...)
controller.set_stream_mode(stream_enabled)  # Only passes output mode
final_response = await controller.run(user_input, history)
```

**Key Changes**:
1. Remove stream/non-stream branching in tool execution logic
2. `set_stream_mode()` only affects output streaming, not execution

---

## 4. System Prompt Enhancement

Append to `DIRECTOR_TOOL_PROMPT` (after existing rules):

```markdown
【Stream/Non-Stream Consistency Rule】
Regardless of whether the user requests stream mode, when planning tool calls you MUST assume "each call will immediately show results."

- If the task is a sequential DAG (needs multiple steps), output the FIRST tool_call in your first response, then continue based on results.
- NEVER output all steps at once (unless tools explicitly support parallel execution).
- After each tool_call, the system will automatically return the result to you. Just decide the next step based on the latest result.
```

---

## 5. Expected Results

| Case | Before | After |
|------|--------|-------|
| l5_sequential_dag | stream: 2 calls, non-stream: 1 call | Both: 2 calls |
| l3_file_edit_sequence | stream: 5 calls, non-stream: 3 calls | Both: 5 calls |
| l5_multi_file_creation | stream: 3 write_file, non-stream: +3 read | Both: 3 precision_edit |
| l7_context_switch | stream: ~20 calls, non-stream: ~490 calls | Both: ~20 calls (with max_turns safety net) |

**Total**: Benchmark 20/20 PASS, 100% Stream/Non-Stream Parity

---

## 6. Verification Plan

1. **Unit Tests**: `pytest polaris/kernelone/tools/tests/ polaris/kernelone/llm/toolkit/tests/` must pass (140 tests)
2. **Benchmark**: `python -m polaris.delivery.cli agentic-eval --suite tool_calling_matrix --provider runtime_binding`
3. **Target**: 20/20 cases passing

---

## 7. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Regression in existing tests | Very Low | High | 140 unit tests as safety net |
| Performance regression (non-stream slower) | Low | Medium | Incremental execution adds ~1 round-trip per call |
| Backward compatibility | None | - | Fully backward compatible, only internal loop changes |

**Total Risk: LOW**
