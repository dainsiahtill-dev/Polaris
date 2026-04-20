# 日志审计报告 #91：数据一致性分析

**审计日期**: 2026-04-13
**审计范围**: `polaris/kernelone/` 和 `polaris/cells/`
**审计类型**: 数据一致性分析
**关联修复**: TOP6 Critical Fixes (2026-04-01), Stream/Non-Stream Parity Fix (2026-03-29)

---

## 1. 执行摘要

本次审计分析了 `polaris/kernelone/` 和 `polaris/cells/` 下的数据一致性问题。审计发现系统存在多层次的数据一致性保障机制，但仍有部分历史遗留问题和潜在风险点需要关注。

**关键发现**:
- 状态机转换规则清晰，已实现显式验证机制
- Session/Turn 状态持久化采用 SSOT (Single Source of Truth) 原则
- 5 层缓存架构明确区分了可缓存与不可缓存边界
- 并发控制使用 RLock 全局锁保护
- 已修复多个历史一致性问题（TOP6 修复已落地）

---

## 2. 状态机状态转换一致性

### 2.1 TurnStateMachine 实现分析

**文件**: `polaris/cells/roles/kernel/internal/turn_state_machine.py`

**状态转换规则**:
```python
_VALID_TRANSITIONS: dict[TurnState, set[TurnState]] = {
    TurnState.IDLE: {TurnState.CONTEXT_BUILT},
    TurnState.CONTEXT_BUILT: {TurnState.DECISION_REQUESTED},
    TurnState.DECISION_REQUESTED: {TurnState.DECISION_RECEIVED, TurnState.FAILED},
    TurnState.DECISION_RECEIVED: {TurnState.DECISION_DECODED, TurnState.FAILED},
    TurnState.DECISION_DECODED: {
        TurnState.FINAL_ANSWER_READY,
        TurnState.TOOL_BATCH_EXECUTING,
        TurnState.HANDOFF_WORKFLOW,
        TurnState.FAILED,
    },
    # ... 完整状态转换图
}
```

**关键约束**（已明确禁止的转换）:
1. `TOOL_BATCH_EXECUTED` 禁止回到 `DECISION_REQUESTED`（防止 continuation loop）
2. `FINALIZATION_REQUESTED` 禁止触发 `TOOL_BATCH_EXECUTING`（防止工具链）
3. 禁止从中间状态跳回 `IDLE`
4. 禁止跳过必要阶段

**一致性保障**:
- 所有状态转换必须通过 `transition_to()` 方法
- 违反规则会抛出 `InvalidStateTransitionError`
- 状态历史 (`_history`) 记录每次转换的时间戳

### 2.2 新旧架构边界

| 方法 | TurnEngine（旧） | TurnTransactionController（新） |
|------|-----------------|-------------------------------|
| 执行入口 | `run()` / `run_stream()` | `execute()` / `execute_stream()` |
| 执行模式 | while 循环直到停止 | 单次事务化执行 |
| 状态管理 | ConversationState + PolicyLayer | TurnStateMachine + TurnLedger |
| 停止条件 | PolicyLayer.evaluate() | State Machine 状态转换 |

**评估结论**: 状态转换规则设计严谨，新旧架构边界清晰，但需要注意两种引擎共存期间的兼容性问题。

---

## 3. Session/Turn 状态持久化一致性

### 3.1 SSOT 强制执行

**文件**: `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

**关键约束 (P0 SSOT Enforcement)**:
```python
def __post_init__(self) -> None:
    # P0 SSOT Enforcement: Seed _history ONLY from context_os_snapshot
    # For new sessions (empty transcript_log), start with empty history.
    # This enables fresh ContextOS bootstrapping for new sessions.
    snapshot_history = self._extract_snapshot_history()
    if snapshot_history is self._NO_SNAPSHOT:
        raise ValueError(
            "ToolLoopController requires context_os_snapshot for SSOT compliance. "
            "request.history fallback is DEPRECATED and no longer supported. "
        )
```

**评估结论**: 系统强制要求 `context_os_snapshot` 作为唯一历史来源，废弃了旧的 `request.history` 回退机制。

### 3.2 SessionContinuityEngine

**文件**: `polaris/kernelone/context/session_continuity.py`

**关键机制**:
- `SessionContinuityPack`: 包含 summary, stable_facts, open_loops 等
- `ContextOSProjection`: 维护完整的 `transcript_log` 和 `working_state`
- 会话恢复时通过 `turn_events` 保留完整元数据（kind, route, dialog_act, source_turns, artifact_id, created_at）

**评估结论**: Session 持久化设计良好，支持增量更新和元数据保留。

### 3.3 Budgets 同步问题（已修复 - TOP6 BUG 2）

**问题描述**: `_request_to_state` 未同步 config 到 budgets

**修复验证**: 在 `polaris/cells/roles/kernel/internal/turn_engine/engine.py` 中:
```python
def _request_to_state(self, request: RoleTurnRequest, role: str) -> ConversationState:
    state = ConversationState.new(...)
    # Sync TurnEngineConfig limits into state budgets so should_stop() and
    # hard-limit checks both use the same source of truth.
    state.budgets.max_turns = self.config.max_turns
    state.budgets.max_tool_calls = self.config.max_total_tool_calls
    state.budgets.max_wall_time_seconds = self.config.max_wall_time_seconds
    state.budgets.max_stall_cycles = self.config.max_stall_cycles
    return state
```

**评估结论**: BUG 2 已修复，config 和 budgets 现在保持同步。

---

## 4. Cache 和 Source-of-Truth 一致性

### 4.1 5 层缓存架构

**文件**: `polaris/kernelone/context/cache_manager.py`

**缓存层级**:
| Tier | 类型 | 持久化 | TTL | 备注 |
|------|------|--------|-----|------|
| SESSION_CONTINUITY | 内存 LRU | 否 | 300s | 会话连续性包 |
| REPO_MAP | 磁盘 JSON | 是 | 600s | 语言特定仓库映射 |
| SYMBOL_INDEX | 磁盘 JSON | 是 | 600s | 每文件符号索引 |
| HOT_SLICE | 内存 LRU | 否 | 300s | 最近使用的代码切片 |
| PROJECTION | 磁盘 JSON | 是 | 300s | 会话连续性投影 |

### 4.2 明确禁止缓存的内容

根据 blueprint §5.7:
- Graph truth（禁止缓存）
- Source-of-truth session rows（禁止缓存）
- Public contract ownership（禁止缓存）

**评估结论**: 缓存边界定义清晰，但需要确保代码严格遵守此约束。

### 4.3 mtime-Based Invalidation

**HOT_SLICE tier**:
```python
async def _get_hot_slice(self, key: str) -> Any | None:
    # mtime-based invalidation if recorded
    if entry.file_mtime is not None and entry.file_mtime > 0:
        current_mtime = os.path.getmtime(normalized)
        if current_mtime > mtime_recorded:
            self._hot_slices.pop(key, None)
            return None
```

**评估结论**: mtime-based invalidation 提供了细粒度的文件变更检测。

### 4.4 写工具缓存问题（已修复 - TOP6 BUG 3）

**问题描述**: 写工具缓存返回 stale 结果

**修复验证**: 写工具（WRITE_TOOLS）在 `AgentAccelToolExecutor` 中已设置:
```python
# skip_cache=True for write tools in executor.py
if tool_name in WRITE_TOOLS:
    return await self._execute_with_cache(tool_name, args, skip_cache=True)
```

**评估结论**: BUG 3 已修复，写工具现在跳过缓存。

---

## 5. 并发写入冲突处理

### 5.1 ContextOS 并发控制

**文件**: `polaris/kernelone/context/context_os/runtime.py`

**锁机制**:
```python
class StateFirstContextOS:
    def __init__(self, ...) -> None:
        # Phase 1 Fix: Main lock for project() concurrency safety
        # threading.RLock allows nested locking by the same thread
        self._project_lock: threading.RLock = threading.RLock()
```

**使用方式**:
```python
def project(self, ...) -> ContextOSProjection:
    with self._project_lock:
        # 并发安全的投影操作
        ...
```

**评估结论**: 使用 `threading.RLock` 保护 `project()` 调用，支持同一线程的嵌套锁定。

### 5.2 ProviderManager 并发控制

**文件**: `polaris/infrastructure/llm/providers/provider_registry.py`

**全局锁保护**:
```python
def get_provider_instance(self, provider_type: str) -> BaseProvider | None:
    # H-NEW Fix: Hold global_lock throughout check-evict-recreate to prevent TOCTOU race.
    with self._global_lock:
        existing = self._provider_instances.get(resolved)
        if existing is not None:
            age = now - self._instance_timestamps.get(resolved, now)
            failures = self._instance_failures.get(resolved, 0)
            if age <= self._INSTANCE_TTL_SECONDS and failures < self._FAILURE_EVICTION_THRESHOLD:
                return existing
            # Evict stale/failed instance
            ...
```

**评估结论**: Provider 实例获取和失败计数更新都在全局锁保护下进行，防止 TOCTOU (Time-of-Check-Time-of-Use) 竞态条件。

### 5.3 Provider 失败计数清理（已修复 - TOP6 BUG 1）

**问题描述**: `record_provider_failure` 未清理 failure count

**修复验证**:
```python
def record_provider_failure(self, provider_type: str) -> None:
    with self._global_lock:
        count = self._instance_failures.get(resolved, 0) + 1
        self._instance_failures[resolved] = min(count, self._MAX_FAILURE_COUNT)
        if count >= self._FAILURE_EVICTION_THRESHOLD:
            logger.warning(...)
            self._provider_instances.pop(resolved, None)
            self._instance_timestamps.pop(resolved, None)
            self._instance_failures.pop(resolved, None)  # reset failure count on eviction
```

**评估结论**: BUG 1 已修复，provider 被驱逐时 failure count 被正确清理。

### 5.4 工具执行并发控制

**文件**: `polaris/cells/roles/kernel/internal/turn_engine/engine.py`

**ResourceQuotaManager**:
```python
def _check_quota_before_turn(self, agent_id: str) -> tuple[bool, str]:
    manager = self._get_quota_manager()
    try:
        status = manager.check_quota(agent_id)
        if status == QuotaStatus.SYSTEM_OVERLOADED:
            return (False, "System resource limits exceeded")
        ...
    except (KeyError, ValueError, RuntimeError) as exc:
        logger.debug("[TurnEngine] Quota check failed (allowing turn): %s", exc)
        return (True, "")  # Quota system failure should not block execution
```

**评估结论**: 并发槽位通过 `ResourceQuotaManager` 管理，失败时采用 fail-open 策略。

---

## 6. 事务边界清晰性

### 6.1 TurnTransactionController 事务边界

**文件**: `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`

**事务流程**:
```
1. 构建 context -> DECISION_REQUESTED
2. 调用 LLM -> DECISION_RECEIVED
3. 解码决策 -> DECISION_DECODED
4. [分支] 直接回答 -> FINAL_ANSWER_READY
5. [分支] 工具调用 -> TOOL_BATCH_EXECUTING -> TOOL_BATCH_EXECUTED
6. [分支] LLM_ONCE 收口 -> FINALIZATION_REQUESTED -> FINALIZATION_RECEIVED
7. 完成 -> COMPLETED
```

**TurnLedger 账本**:
```python
@dataclass
class TurnLedger:
    """Turn 账本 - 记录单次 turn 的完整轨迹"""
    turn_id: str
    llm_calls: list[dict]
    tool_executions: list[dict]
    decisions: list[dict]
    events: list[TurnPhaseEvent]
    state_history: list[tuple[str, int]]
```

**评估结论**: 事务边界清晰，每个 turn 有独立的账本记录。

### 6.2 ToolLoopController 增量执行

**关键语义**:
```python
def append_tool_result(self, tool_result: dict[str, Any], tool_args: dict[str, Any] | None = None) -> None:
    """Append a single tool result to history without duplicating assistant message.

    Uses ContextEvent to preserve metadata. This enables incremental execution
    where each tool result is immediately visible to the LLM before the
    next tool execution decision.
    """
```

**评估结论**: 增量执行模式确保 Stream 和 Non-Stream 路径一致。

### 6.3 双重保险机制

**Budget 检查**:
```python
# 双重保险 1: ConversationState.should_stop()
should_stop, stop_reason = state.should_stop()
if should_stop:
    return _build_run_result(error=stop_reason, is_complete=False)

# 双重保险 2: max_turns 硬限制
if round_index >= self.config.max_turns:
    return _build_run_result(error="max_turns_exceeded", is_complete=False)
```

**评估结论**: 双重保险机制防止单一检查失效导致的无限循环。

---

## 7. Stream/Non-Stream 执行路径一致性

### 7.1 问题根因

根据 `docs/blueprints/STREAM_NONSTREAM_PARITY_FIX_20260329.md`:

| Case | Stream | Non-Stream | Delta |
|------|--------|-----------|-------|
| l5_sequential_dag | 2 calls | 1 call | non-stream stops early |
| l3_file_edit_sequence | 5 calls | 3 calls | different tools chosen |
| l5_multi_file_creation | 3 write_file | +3 repo_read_head | extra calls in non-stream |
| l7_context_switch | ~20 calls | ~490 calls | infinite loop in non-stream |

**根因**: Stream 模式增量执行每个 tool_call，非 stream 模式生成所有 tool_calls 后批量执行。

### 7.2 修复方案

**统一增量执行模式**:
```python
# 增量追加：每个工具执行后立即追加结果到 transcript
# 这样 LLM 在下一步决策时能看到这个结果
for call in exec_tool_calls:
    result = await self._execute_single_tool(...)
    round_tool_results.append(result)
    _controller.append_tool_result(result, tool_args=getattr(call, "args", None))
```

**iteration 字段添加**（已修复 - TOP6 Tool Call iteration=None Fix）:
```python
# 在 tool_call / tool_result 事件中添加 iteration=round_index
yield {"type": "tool_call", "tool": call.tool, "args": safe_args, "iteration": round_index}
```

**评估结论**: Stream/Non-Stream 一致性修复已落地，增量执行模式确保两种路径产生相同的工具调用序列。

---

## 8. 已知历史遗留问题

### 8.1 仍需关注的问题

1. **异常吞噬问题**: CLAUDE.md 提到仍有 206 处 `except Exception` / `except:` 和 53 处 `pass` 语句
2. **新旧引擎共存**: TurnEngine（旧）和 TurnTransactionController（新）并存可能导致行为不一致
3. **环境变量前缀混用**: `POLARIS_` 和 `KERNELONE_` 在同一代码层混用

### 8.2 已验证修复的问题

| 修复项 | 状态 | 验证文件 |
|--------|------|----------|
| TurnEngine max_turns 硬限制 | ✅ 已修复 | `engine.py` should_stop() + 硬限制 |
| 写工具禁 retry | ✅ 已修复 | `executor.py` skip_cache + max_attempts=1 |
| 全局异常 logging | ✅ 已修复 | 14 处 silent except → logger.exception() |
| Provider TTL | ✅ 已修复 | `provider_registry.py` TTL=5min + 失败驱逐 |
| 审计 HMAC | ✅ 已修复 | `audit/runtime.py` HMAC-SHA256 chain link 签名 |
| Tool 定义统一 | ✅ 已修复 | `core.py` contracts.py 优先 + STANDARD_TOOLS 降级 |
| record_provider_failure 清理 | ✅ 已修复 | eviction 时清理 failure count |
| _request_to_state 同步 | ✅ 已修复 | config 同步到 budgets |
| 写工具缓存 stale | ✅ 已修复 | 写工具 skip_cache=True |
| Stream/Non-Stream 一致 | ✅ 已修复 | 增量执行模式 |

---

## 9. 风险评估

| 风险项 | 概率 | 影响 | 缓解措施 |
|--------|------|------|----------|
| 并发写入冲突 | 低 | 高 | RLock 全局锁保护 |
| 缓存 stale 数据 | 中 | 中 | mtime-based invalidation + TTL |
| 状态转换异常 | 低 | 高 | InvalidStateTransitionError 强制检查 |
| Session 恢复不一致 | 低 | 高 | SSOT + TurnLedger 账本 |
| Provider 失败累积 | 中 | 中 | TTL + 失败计数上限 |

---

## 10. 建议

### 10.1 短期建议

1. **完善异常日志**: 对剩余的 silent except 添加有意义的日志记录
2. **统一环境变量前缀**: 考虑统一 `POLARIS_` 和 `KERNELONE_` 前缀使用
3. **增加一致性测试**: 为 Stream/Non-Stream 路径添加专门的回归测试

### 10.2 中期建议

1. **迁移完成度评估**: 评估 TurnEngine 到 TurnTransactionController 的迁移进度
2. **缓存边界审计**: 确保没有新代码违反"禁止缓存 Graph truth"约束
3. **性能基准建立**: 建立数据一致性相关的性能基准

### 10.3 长期建议

1. **考虑分布式锁**: 如果系统扩展到多进程，需要从 `threading.RLock` 迁移到分布式锁
2. **事务日志化**: 为关键操作添加幂等性保障的事务日志
3. **监控告警**: 建立数据一致性指标的监控告警机制

---

## 11. 结论

本次审计确认 `polaris/kernelone/` 和 `polaris/cells/` 下已建立较为完善的数据一致性保障机制：

1. **状态机转换**: 清晰的转换规则和强制验证
2. **Session/Turn 持久化**: SSOT 原则严格遵守
3. **缓存管理**: 5 层架构 + 明确的边界定义
4. **并发控制**: RLock + 全局锁 + ResourceQuotaManager
5. **事务边界**: 清晰的新架构事务模型

TOP6 Critical Fixes 已全部落地，Stream/Non-Stream 一致性问题已解决。仍需关注历史遗留的异常吞噬问题和环境变量前缀混用问题。

---

**审计员**: Claude Code Agent
**审计工具**: Static Analysis + Code Review
**报告版本**: 1.0