# ADR-0074: Multi-Layer Dead Loop Prevention Architecture

## Status
- **Status**: Accepted
- **Date**: 2026-04-12
- **Author**: AI Agent (via code audit)
- **Deciders**: Architecture Team

## Context

Agent system experienced severe dead loop incidents where:
1. **Tool Loop**: Same tool (repo_tree, read_file) repeated 3+ times with same arguments
2. **Cross-Tool Loop**: Alternating pattern (repo_tree → read_file → repo_tree → read_file)
3. **State Stagnation**: 5+ consecutive read-only operations without workspace modification
4. **Persona Override**: Role-playing content bypassed mandatory `<thinking>` tags
5. **Context Bloat**: 321 events caused attention degradation and system prompt遗忘

Root cause analysis showed soft warnings were insufficient; model in deadlock state ignores reminders.

## Decision

Implement **hard circuit breakers** with four-layer defense:

### Layer 1: Same-Tool Repetition Blocker
- **Threshold**: 3 identical calls (tool + args)
- **Action**: Raise `ToolLoopCircuitBreakerError` with recovery hint
- **Rationale**: Soft warnings ("[SYSTEM REMINDER]...") are ignored; hard fault forces state change

### Layer 2: Cross-Tool Pattern Detection  
- **Patterns**: ABAB (A→B→A→B), ABCABC (3-cycle repetition)
- **Window**: 6 most recent tool calls
- **Action**: Circuit breaker with "探查阶段已超时" message
- **Rationale**: Detects exploration paralysis even when tools alternate

### Layer 3: State Stagnation Detection
- **Metric**: Consecutive read-only operations without write
- **Threshold**: 5 read ops (repo_tree, read_file, glob, etc.)
- **Reset**: Any write tool (edit_file, write_file, execute_command) resets counter
- **Action**: Circuit breaker with "请根据已有信息立即给出结论"
- **Rationale**: Distinguishes productive exploration from action paralysis

### Layer 4: Output Format Enforcement
- **Requirement**: All assistant responses MUST start with `<thinking>` tag
- **Validation**: Runtime check in `append_tool_cycle()`
- **Violation**: Inject format error with example:
  ```
  [SYSTEM FORMAT ERROR] 回复必须以<thinking>标签开头
  请严格按照以下格式重试：
  <thinking>确认任务目标、检查工具结果、规划下一步</thinking>实际回复内容...
  ```
- **Rationale**: Prevents persona roleplay from bypassing reasoning framework

### Intent Switch Optimization
- **Detection**: View verbs (看/分析/read/analyze) → Write verbs (写/创建/write/create)
- **Action**: Extract summary before pruning old goal context
- **Benefit**: Frees token space while preserving key findings

## Consequences

### Positive
- Guaranteed termination of dead loops (hard faults vs soft warnings)
- Clear recovery guidance injected into context
- Reduced token waste from repetitive tool calls
- Maintained semantic continuity during intent switches

### Negative
- Circuit breakers may interrupt legitimate deep exploration
- Requires tuning of thresholds per use case
- Additional overhead in hot path (tool result tracking)

### Mitigations
- Thresholds are configurable class attributes
- Write tools reset all counters (allows legitimate batch operations)
- Circuit breaker includes specific recovery instructions

## Implementation

### Files Modified
- `polaris/cells/roles/kernel/internal/tool_loop_controller.py`
  - `ToolLoopCircuitBreakerError` exception class
  - `_track_successful_call()` - stagnation & cross-tool detection
  - `_detect_cross_tool_loop()` - pattern matching
  - `_validate_thinking_compliance()` - format enforcement
  
- `polaris/kernelone/context/context_os/models.py`
  - `RunCard.detect_intent_switch()` - heuristic detection
  - `RunCard.generate_intent_switch_summary()` - summary extraction
  - `MAX_EVENTS_BEFORE_EMERGENCY_COMPACT` - event-based compression

- `polaris/kernelone/context/chunks/assembler.py`
  - Intent switch handling in context assembly

### Configuration Constants
```python
SUCCESS_LOOP_HARD_THRESHOLD = 3      # P0: Immediate stop
MAX_READ_ONLY_STAGNATION = 5         # P0: Action required  
CROSS_TOOL_LOOP_WINDOW = 6           # P0: Pattern detection
MAX_EVENTS_BEFORE_EMERGENCY_COMPACT = 50  # P1: Aggressive compression
```

## References
- Issue: Agent陷入无限工具调用死循环 (2026-04-12)
- Related: Prompt templates `## 输出顺序（强制 — 防止人设反噬）`
- Related: ADR-0076 ContextOS 2.0 摘要策略选型

## Notes
Circuit breakers are designed to be **fail-closed** - better to interrupt
a potentially valid long exploration than allow infinite resource consumption.
Recovery guidance is intentionally specific to guide model back on track.
