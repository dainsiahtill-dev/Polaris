# Context Pruning & Recovery Blueprint

**Date**: 2026-04-12  
**Status**: Draft → Implementation  
**Scope**: TurnEngine, ContextOS, ToolLoopController  

## Executive Summary

This blueprint defines the long-term architecture for preventing and recovering from Agent dead loops. Building on ADR-0068, it specifies event sourcing safeguards, recovery procedures, observability metrics, and comprehensive testing strategies.

## 1. Problem Statement

### 1.1 Dead Loop Manifestations

| Type | Symptom | Frequency | Impact |
|------|---------|-----------|--------|
| **Tool Loop** | Same tool repeated 3+ times | High | Wasted tokens, latency |
| **Cross-Tool Loop** | ABAB pattern exploration | Medium | Infinite exploration |
| **Stagnation** | Read-only ops without write | High | No progress |
| **Persona Override** | Roleplay bypasses thinking tags | Medium | Loss of reasoning |
| **Context Bloat** | 300+ events, attention decay | High | System prompt遗忘 |

### 1.2 Root Causes
1. Soft warnings ignored by model in deadlock state
2. No cross-tool pattern detection
3. No workspace modification tracking
4. No runtime format enforcement
5. Compression based only on tokens, not event count

## 2. Architecture

### 2.1 Four-Layer Defense

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: Output Format Enforcement                         │
│  - Validate <thinking> tags in append_tool_cycle()         │
│  - Inject format error with example on violation            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: State Stagnation Detection                        │
│  - Track consecutive read-only operations                   │
│  - Reset on any write/execute                               │
│  - Circuit breaker at 5 ops                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Cross-Tool Loop Detection                         │
│  - ABAB pattern detection (6-tool window)                   │
│  - ABCABC pattern detection                                 │
│  - Circuit breaker on pattern match                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Same-Tool Repetition Blocker                      │
│  - Tool + args signature matching                           │
│  - Circuit breaker at 3 repeats                             │
│  - Warning at 2 (soft → hard escalation)                    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Event Sourcing Safeguards

```python
# Critical invariant: Circuit breaker events are appended to transcript
# before raising exception, ensuring audit trail
try:
    controller.track_tool_call(result, tool_name)
except ToolLoopCircuitBreakerError as e:
    # Error is already logged in transcript as system event
    # Recovery hint included for model to self-correct
    return RunResult(error=e.recovery_hint, status="circuit_broken")
```

### 2.3 Intent Switch Flow

```
User: "看下server.js是什么实现" 
  │
  ▼
[Context] current_goal="分析server.js" 
  │
  ▼
User: "给我创建个 role_logger.py"  ← Intent switch detected
  │
  ▼
[Detection] view_verbs → write_verbs transition
  │
  ▼
[Summary Extraction] 
  "[已完成: 分析server.js] 已探明3个对象; 决策: Express路由分析完成"
  │
  ▼
[Context Pruning] Old analysis archived, new goal prominent
  │
  ▼
[Active Context] "[!] 意图切换: 当前任务 → 创建role_logger.py"
```

## 3. Implementation Phases

### Phase 1: Hard Circuit Breakers (P0) ✅ COMPLETED

**Files**:
- `tool_loop_controller.py`: `ToolLoopCircuitBreakerError`, detection logic
- Constants: `SUCCESS_LOOP_HARD_THRESHOLD=3`, `MAX_READ_ONLY_STAGNATION=5`

**Verification**:
```python
def test_circuit_breaker_same_tool():
    controller = ToolLoopController(...)
    for i in range(3):
        with pytest.raises(ToolLoopCircuitBreakerError) if i == 2 else nullcontext():
            controller.track_tool_call({"success": True, "args": {"path": "x"}}, "read_file")
```

### Phase 2: Cross-Tool & Format Enforcement (P0) ✅ COMPLETED

**Files**:
- `tool_loop_controller.py`: `_detect_cross_tool_loop()`, `_validate_thinking_compliance()`

**Verification**:
```python
def test_cross_tool_loop_abab():
    controller = ToolLoopController(...)
    controller._recent_tool_names = ["repo_tree", "read_file", "repo_tree", "read_file"]
    assert controller._detect_cross_tool_loop() is True

def test_thinking_validation():
    is_valid, error = ToolLoopController._validate_thinking_compliance("咳咳...地球佬")
    assert is_valid is False
    assert "thinking" in error.lower()
```

### Phase 3: Intent Switch Optimization (P1) ✅ COMPLETED

**Files**:
- `models.py`: `RunCard.detect_intent_switch()`, `generate_intent_switch_summary()`
- `assembler.py`: Intent switch handling in context assembly

### Phase 4: Observability & Metrics (P2) 🔄 IN PROGRESS

**Metrics to Track**:
```python
@dataclass
class DeadLoopMetrics:
    circuit_breaker_triggers: Counter  # By type (same-tool, cross-tool, stagnation)
    intent_switches_detected: Counter
    thinking_violations: Counter
    emergency_compactions: Counter
    avg_events_per_turn: Histogram
    read_only_streak_length: Histogram
```

**Implementation**:
```python
# In tool_loop_controller.py
def _track_successful_call(self, tool_result, tool_name):
    # Existing logic...
    
    # Emit metric event
    emit_event(MetricEvent(
        name="tool_loop.read_only_streak",
        value=self._read_only_streak,
        tags={"tool": tool_name}
    ))
```

### Phase 5: Recovery Procedures (P2) 📋 PLANNED

**Recovery State Machine**:
```
CIRCUIT_BREAKER_TRIGGERED
        │
        ▼
┌───────────────┐
│  PAUSE_EXEC   │ ──► Inject recovery prompt
│  (1 turn)     │
└───────────────┘
        │
        ▼
┌───────────────┐
│  RETRY_CHECK  │ ──► Did model self-correct?
│               │
└───────────────┘
        │
    ┌───┴───┐
    ▼       ▼
RESUME  ESCALATE
(continue)  (human review)
```

**Recovery Prompt Template**:
```
[SYSTEM CIRCUIT BREAKER TRIGGERED]
原因: {reason}

强制恢复程序：
1. 停止当前探查行为
2. 回顾已有信息（见上文）
3. 选择以下之一执行：
   A) 如果有足够信息：执行写入操作完成当前任务
   B) 如果信息不足：明确说明缺失什么，而不是继续探查
   C) 如果任务已完成：直接给出最终答案

禁止再次调用 {banned_tools}，直到明确进展被确认。
```

### Phase 6: Testing Strategy (P2) 📋 PLANNED

**Test Categories**:

1. **Unit Tests** - Individual detection logic
   ```python
   # test_circuit_breaker.py
   class TestSameToolDetection:
       def test_triggers_at_threshold(self): ...
       def test_resets_on_different_tool(self): ...
       def test_resets_on_write_tool(self): ...
   
   class TestCrossToolDetection:
       def test_abab_pattern(self): ...
       def test_abcabc_pattern(self): ...
       def test_no_false_positive_abc(self): ...
   
   class TestStagnationDetection:
       def test_increments_on_read(self): ...
       def test_resets_on_write(self): ...
       def test_triggers_at_max(self): ...
   ```

2. **Integration Tests** - Full TurnEngine loop
   ```python
   # test_turn_engine_dead_loop.py
   async def test_engine_handles_circuit_breaker():
       engine = TurnEngine(...)
       result = await engine.run(user_message="探索目录")
       # Simulate 3 repo_tree calls
       assert result.error == "circuit_breaker_triggered"
       assert "探查阶段已超时" in result.recovery_hint
   ```

3. **Chaos Tests** - Adversarial scenarios
   ```python
   # test_chaos_dead_loop.py
   async def test_resists_intentional_loop():
       # Model intentionally tries to loop
       for _ in range(10):
           try:
               await execute_tool("read_file", {"path": "x"})
           except ToolLoopCircuitBreakerError:
               return  # Success - loop prevented
       pytest.fail("Circuit breaker failed")
   ```

## 4. Long-Term Improvements

### 4.1 ML-Based Detection
- Train classifier on historical dead loop transcripts
- Features: tool sequence embeddings, time between calls, result similarity
- Predict probability of dead loop before 3rd repeat

### 4.2 Dynamic Thresholds
```python
class AdaptiveCircuitBreaker:
    def __init__(self):
        self.base_threshold = 3
        self.success_rate = 1.0  # Decay on successful completions
    
    def threshold(self) -> int:
        # Lower threshold if agent historically loops often
        return max(2, int(self.base_threshold * self.success_rate))
```

### 4.3 Cross-Session Learning
- Persist loop patterns to long-term memory
- "上次分析相似代码库时，你在第4次read_file后陷入循环"
- Proactive warnings before threshold reached

## 5. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Dead loop incidents / 1000 turns | < 1 | Event log analysis |
| Avg tool calls per completed task | < 15 | Turn tracking |
| Circuit breaker false positive rate | < 5% | Manual review sample |
| Intent switch context relevance | > 90% | User satisfaction survey |
| <thinking> compliance rate | > 98% | Output validation |

## 6. Appendix

### 6.1 Tool Categories

```python
READ_ONLY_TOOLS = {
    "read_file", "repo_read_head", "repo_read_tail", 
    "repo_read_slice", "repo_read_around", "repo_rg",
    "search_code", "list_directory", "repo_tree",
    "file_exists", "glob"
}

WRITE_TOOLS = {
    "write_file", "append_to_file", "edit_file", 
    "precision_edit", "execute_command"  # Side effects
}
```

### 6.2 Verb Taxonomy

```python
VIEW_VERBS = {
    "看", "分析", "检查", "读取", "查看", "探查", "了解",
    "read", "analyze", "check", "inspect", "view", "explore",
    "look", "see", "find", "search"
}

WRITE_VERBS = {
    "写", "创建", "修改", "生成", "添加", "实现",
    "write", "create", "edit", "modify", "generate", "add",
    "implement", "build", "make"
}
```

### 6.3 Related Documents
- ADR-0068: Dead Loop Prevention Architecture
- ADR-0067: ContextOS 2.0 摘要策略选型
- `prompt_templates.py`: Output format requirements
