# 日志审计任务 #85: 系统事件流分析报告

**审计日期**: 2026-04-13
**审计范围**: `polaris/kernelone/events/` 和 `polaris/cells/roles/kernel/internal/`
**审计类型**: 事件流系统完整性 + 静默失败检测

---

## 1. 事件类型定义完整性检查

### 1.1 常量定义层 (`constants.py`)

定义了 **27 个事件类型常量**，分为以下类别：

| 类别 | 事件类型 | 状态 |
|------|----------|------|
| Tool 生命周期 | `tool_call`, `tool_result`, `tool_error`, `tool_start`, `tool_end` | 完整 |
| Content 事件 | `content_chunk`, `thinking_chunk`, `complete` | 完整 |
| LLM 事件 | `llm_start`, `llm_end`, `llm_error` | 完整 |
| LLM Call 生命周期 | `llm_call_start`, `llm_call_end`, `llm_retry` | 完整 (兼容性别名) |
| LLM Realtime | `llm_waiting`, `llm_completed`, `llm_failed` | 完整 |
| Session 事件 | `session_start`, `session_end` | 完整 |
| Task 事件 | `task.created`, `task.updated`, `task.completed`, `task.failed` | 完整 (dot notation) |
| Audit 事件 | `fingerprint`, `state.snapshot`, `error` | 完整 |

**问题**: Task 事件使用 dot notation (`task.created`)，与其他事件使用 underscore notation (`tool_call`) 不一致。

### 1.2 TypedEvent Schema 层 (`typed/schemas.py`)

定义了 **50+ TypedEvent 类**，组成 discriminated union `TypedEvent`。

| 类别 | 事件类数量 | 状态 |
|------|------------|------|
| Lifecycle | 2 | 完整 |
| Tool | 5 (Invoked/Completed/Error/Blocked/Timeout) | 完整 |
| Turn | 3 (Started/Completed/Failed) | 完整 |
| Director | 20+ | 完整 |
| Context | 4 | 完整 |
| System | 2 | 完整 |
| Audit | 7+ (AUDIT_EXTENDED) | 完整 |
| Cognitive | 12+ | 完整 |

**Schema 版本控制**: `EventBase.event_version: int = Field(default=1, ge=1)` 已定义，但**无版本迁移逻辑**。

### 1.3 UEP -> TypedEvent 转换映射 (`uep_typed_converter.py`)

```python
# _UEP_STREAM_TO_TYPED 映射
tool_call -> ToolInvoked      # OK
tool_result -> ToolCompleted   # OK
tool_error -> ToolError       # OK
content_chunk -> TurnStarted   # 语义混淆
thinking_chunk -> TurnStarted  # 语义混淆
complete -> TurnCompleted      # 语义混淆
error -> TurnFailed           # 语义混淆

# _UEP_LIFECYCLE_TO_TYPED 映射
llm_call_start -> InstanceStarted    # 语义勉强
llm_call_end -> InstanceDisposed     # 语义勉强
llm_error -> SystemError            # OK
llm_retry -> TaskRetry              # OK
```

**问题**:
- `content_chunk` 映射到 `TurnStarted` 语义不直观
- `llm_call_start/end` 映射到 `InstanceStarted/Disposed` 语义勉强

---

## 2. 事件发布/订阅链路错误处理分析

### 2.1 MessageBus (`message_bus.py`)

**publish() 方法** (L387-454):
- 同步 handler 调用在 try/catch 中
- 异步 handler 通过 `asyncio.gather(*handler_tasks, return_exceptions=True)` 处理
- 异常被捕获并记录为 `logger.warning`
- **死信队列**已实现 (`DeadLetterMessage`)

**问题**:
- L424: `except (RuntimeError, ValueError) as e:` - 捕获过于宽泛
- 异步 handler 超时后 (L438-446) 只 cancel task，但未记录失败计数

### 2.2 EventRegistry (`typed/registry.py`)

**_invoke_handlers() 方法** (L419-488):
- 同步 handler 直接调用，异常捕获记录 warning
- 异步 handler 通过 `asyncio.gather(*pending_tasks, return_exceptions=True)`
- `CancelledError` 被正确处理

**问题**:
- L450: `except (RuntimeError, ValueError) as e:` - 捕获宽泛
- L465: 异步异常只记录 warning，不增加 `_handler_invocation_count`

### 2.3 TypedEventBusAdapter (`typed/bus_adapter.py`)

**emit_to_both() 方法** (L205-290):
- 尝试 Registry emit，失败记录 error
- 尝试 MessageBus emit，失败记录 error
- 返回 `EmitResult` 包含成功/失败状态

**问题**:
- L247-250, L257-260: 两处异常捕获仅记录 error，未追踪到死信队列
- L320: `logger.warning` 对于无映射的情况，但不影响主流程

---

## 3. 静默失败点识别 (Fire-and-Forget)

### 3.1 高风险静默失败点

| 文件:行号 | 模式 | 风险等级 | 说明 |
|-----------|------|----------|------|
| `io_events.py:404` | `asyncio.create_task(_safe_publish(bus, msg))` | **HIGH** | 任务创建后不等待，失败被 `_safe_publish` 的 warning 记录但调用方不知道 |
| `llm_caller/invoker.py:1525-1528` | `asyncio.create_task(_publish())` + `except RuntimeError: pass` | **HIGH** | 明确静默忽略，无任何日志 |
| `io_events.py:132-138` | `_publish_llm_event_to_realtime_bridge()` | **MEDIUM** | 异常被捕获记录 warning，主流程继续 |
| `task_trace_events.py:104-106` | `except (RuntimeError, ValueError): return` | **MEDIUM** | 无日志，直接返回 |
| `realtime_bridge.py:46-51` | `publish_llm_realtime_event()` | **LOW** | bridge 为 None 时静默返回，不记录 |

### 3.2 UEPEventPublisher 降级路径

`uep_publisher.py` 中多个方法有降级逻辑：

| 方法 | 降级行为 | 问题 |
|------|----------|------|
| `publish_stream_event()` (L161-165) | adapter 不可用时回退到 `_publish_stream_to_bus` | 回退失败返回 False，调用方未检查 |
| `_publish_via_bus_from_payload()` (L218-225) | bus 为 None 时写磁盘 fallback | 失败返回 False，无人检查 |
| `_publish_stream_to_bus()` (L347-351) | bus 为 None 时静默返回 False | **静默失败** |

**问题**: 调用方不检查返回值，失败被静默吞掉。

---

## 4. 事件 Schema 演化兼容性

### 4.1 Schema 版本控制现状

| 层级 | 版本字段 | 迁移逻辑 | 状态 |
|------|----------|----------|------|
| `EventBase` | `event_version: int = 1` | 无 | **缺失** |
| `EventEnvelope` (sourcing) | `event_version: int` | `from_record()` 仅解析，不转换 | **缺失** |
| `UEPStreamEventPayload` | 无 | N/A | **缺失** |
| `UEPLifecycleEventPayload` | 无 | N/A | **缺失** |

### 4.2 向后兼容性风险

1. **TypedEvent 添加新字段**: Pydantic `extra="forbid"` (L55) 会拒绝未知字段，旧版本事件传入会被拒绝
2. **UEP payload 字段缺失**: `UEPStreamEventPayload` 无版本字段，演化后无法区分
3. **EventEnvelope 迁移**: `from_record()` 不执行版本转换

---

## 5. 其他问题

### 5.1 双重写入 (Dual-Write) 一致性

`TypedEventBusAdapter.emit_to_both()` 实现双重写入：
- Registry 失败 -> 记录 error，继续
- MessageBus 失败 -> 记录 error，继续

**问题**: 如果 Registry 成功但 MessageBus 失败，系统处于不一致状态。

### 5.2 事件顺序保证

MessageBus 不保证事件顺序 (`_history` 是 `deque`，后写入先读取)。
如果需要顺序保证，当前实现不满足。

### 5.3 `_PENDING_BROADCAST_TASKS` 追踪 (`file_event_broadcaster.py`)

L21-22 定义了 `_PENDING_BROADCAST_TASKS` 集合用于追踪异步广播任务，但：
- L46-49: `add_done_callback(_cleanup)` - 任务完成时清理
- `shutdown_broadcast_tasks()` (L52-63) 取消所有待处理任务

**问题**: 如果 callback 未触发，任务可能泄漏。

---

## 6. 建议修复

### 6.1 静默失败修复优先级

| 优先级 | 位置 | 建议 |
|--------|------|------|
| **P0** | `llm_caller/invoker.py:1525-1528` | 将 `pass` 改为 `logger.debug` 或 `logger.warning` |
| **P0** | `io_events.py:404` | 追踪 created task，在 `_safe_publish` 失败时增加计数器 |
| **P1** | `task_trace_events.py:104-106` | 添加 `logger.warning` 记录失败 |
| **P1** | `uep_publisher.py:351` | 明确返回 False 而非静默，添加计数器 |
| **P2** | `realtime_bridge.py:46-51` | 添加可选的 debug 日志 |

### 6.2 Schema 演化建议

1. 在 `EventEnvelope` 添加版本迁移方法 `migrate(target_version)`
2. UEP payload 添加 `_uep_version` 字段
3. TypedEvent 的 `extra="forbid"` 改为 `extra="allow"` 或显式定义所有未来字段

### 6.3 双重写入一致性建议

添加 `EmitResult` 检查，在任一失败时：
- 记录到专用指标
- 可选触发告警

---

## 7. 统计摘要

| 指标 | 数值 |
|------|------|
| 事件类型常量 (`constants.py`) | 27 |
| TypedEvent 类 (`schemas.py`) | 50+ |
| Fire-and-Forget 模式 | 5 处 |
| 静默失败点 | 3 处高风险 |
| Schema 版本迁移逻辑 | 0 |
| 异常捕获点 (events/) | 60+ |
| 死信队列实现 | 是 |
| 异步任务追踪 | 部分 |

---

## 8. 结论

事件流系统架构设计合理，具有：
- 多层抽象 (constants -> contracts -> schemas)
- 死信队列处理
- 双重写入降级路径
- TypedEvent discriminated union

但存在以下关键风险：
1. **静默失败**: 3 处高风险静默失败点未被适当记录
2. **Schema 演化**: 无版本迁移逻辑，向后兼容性脆弱
3. **双重写入**: 一致性无保证

建议优先修复 P0 级别的静默失败问题。
