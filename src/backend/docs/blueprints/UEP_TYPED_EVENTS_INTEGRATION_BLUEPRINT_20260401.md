# UEP v2.0 与 TypedEventBusAdapter 深度整合蓝图

**版本**: v1.0
**日期**: 2026-04-01
**状态**: Stage 1 Blueprint / Ready for Execution
**执行团队**: KernelOne 基础设施组

---

## 1. 问题陈述

### 1.1 现状分析

当前 Polaris 存在两套独立的事件发射机制，共用同一个 MessageBus 但没有经过 TypedEventBusAdapter 桥接：

```
当前架构（问题）
================

Producer A (UEP v2.0)
  └─► UEPEventPublisher
        └─► MessageBus.publish(Message)
              ├─► JournalSink ──► journal.jsonl ✓
              ├─► ArchiveSink ──► stream_events.gz ✓
              └─► AuditHashSink ──► HMAC chain ✓
                    │
                    │ TypedEventBusAdapter 被绕过！
                    ▼
              EventRegistry (Typed Events) ──► 前端订阅 ✗ (收不到)

Producer B (Typed Events)
  └─► TypedEvent.create()
        └─► TypedEventBusAdapter.emit_to_both()
              ├─► EventRegistry ✓
              └─► MessageBus ✓
```

### 1.2 问题清单

| 问题 | 影响 | 严重度 |
|------|------|--------|
| UEP 事件不走 TypedEventBusAdapter | 前端实时订阅收不到 UEP 事件 | P0 |
| EventRegistry 和 MessageBus 订阅状态不同步 | 内存状态不一致 | P1 |
| TypedEvent 无法触发持久化 | 审计日志缺失 | P1 |
| 双重发射时 Schema 不统一 | 数据格式混乱 | P2 |

### 1.3 根因

`UEPEventPublisher` 直接使用 `MessageBus.publish()`，绕过了 `TypedEventBusAdapter` 的桥接逻辑。

---

## 2. 目标架构

### 2.1 整合后的架构

```
目标架构（整合后）
==================

Producer ──► UEPEventPublisher
                    │
                    ▼
           TypedEventBusAdapter.emit_to_both()
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
   EventRegistry          MessageBus
   (Typed Events)         (底层总线)
          │                   │
          │ 实时订阅           │ 持久化消费者
          ▼                   ├─► JournalSink ──► journal.jsonl
   前端/CLI 实时显示        ├─► ArchiveSink ──► stream_events.gz
                            └─► AuditHashSink ──► HMAC chain
```

### 2.2 核心原则

1. **单一发射点**：所有运行时事件统一通过 `UEPEventPublisher` 发射
2. **双重写入**：`TypedEventBusAdapter.emit_to_both()` 确保 EventRegistry 和 MessageBus 同时收到事件
3. **Schema 统一**：UEP payload 通过 TypedEvent schema 验证后双向转换
4. **向后兼容**：不影响现有 TypedEvent 直接发射的代码

---

## 3. 技术方案

### 3.1 方案 A：UEPEventPublisher 作为 TypedEvent 发射器（推荐）

**核心思路**：让 UEPEventPublisher 将 UEP payload 转换为 TypedEvent，然后通过 TypedEventBusAdapter 发送。

```
步骤：
1. UEPEventPublisher 持有 TypedEventBusAdapter 引用
2. UEP payload → TypedEvent 转换
3. 调用 adapter.emit_to_both(typed_event)
4. adapter 内部自动分发到 EventRegistry 和 MessageBus
```

**优点**：
- 完全复用 TypedEventBusAdapter 的双重写入逻辑
- TypedEvent schema 验证保证类型安全
- 前端订阅自然生效

**缺点**：
- 需要为每种 UEP 事件类型定义对应的 TypedEvent

### 3.2 方案 B：MessageBus 订阅桥接

**核心思路**：在 MessageBus 层面添加订阅，当收到 RUNTIME_EVENT 时转发到 EventRegistry。

```
步骤：
1. 注册 MessageBus → EventRegistry 桥接订阅
2. MessageBus 收到 RUNTIME_EVENT
3. 转换为对应的 TypedEvent
4. 发射到 EventRegistry
```

**优点**：
- 不修改 UEPEventPublisher
- 透明桥接

**缺点**：
- 额外的转换层增加延迟
- 需要维护反向映射表

**推荐方案 A**，原因：
1. TypedEventBusAdapter 已实现完整双重写入
2. UEP payload 可映射到现有 TypedEvent
3. 更符合"单一发射点"原则

---

## 4. 详细设计

### 4.1 UEP 事件类型 → TypedEvent 映射

| UEP 事件 | TypedEvent 类型 | Topic |
|----------|----------------|-------|
| `runtime.event.stream` | `ToolInvoked` / `ToolCompleted` | stream |
| `runtime.event.llm` | `InstanceStarted` / `InstanceDisposed` | lifecycle |
| `runtime.event.fingerprint` | `PlanCreated` | strategy |
| `runtime.event.audit` | `AuditCompleted` | audit |

### 4.2 核心修改

#### 4.2.1 修改 UEPEventPublisher

```python
class UEPEventPublisher:
    def __init__(self, bus: MessageBus | None = None) -> None:
        self._bus = bus or get_global_bus()
        self._adapter: TypedEventBusAdapter | None = None

    def _get_adapter(self) -> TypedEventBusAdapter | None:
        """获取 TypedEventBusAdapter，单例模式。"""
        if self._adapter is None:
            self._adapter = get_default_typed_event_adapter()
        return self._adapter

    async def publish_stream_event(...) -> bool:
        """发布流式事件到 MessageBus 和 EventRegistry。"""
        adapter = self._get_adapter()
        if adapter:
            # 方案 A：通过 adapter 双重写入
            typed_event = self._convert_to_typed_event(...)
            await adapter.emit_to_both(typed_event)
        else:
            # Fallback：直接发 MessageBus
            await self._publish_to_bus(...)
```

#### 4.2.2 新增转换层

```python
# polaris/kernelone/events/uep_typed_converter.py

class UEPToTypedEventConverter:
    """将 UEP payload 转换为 TypedEvent。"""

    def convert_stream_event(self, payload: dict) -> TypedEvent | None:
        """将 stream event 转换为对应的 TypedEvent。"""
        event_type = payload.get("event_type")
        if event_type == "tool_call":
            return ToolInvoked.create(...)
        elif event_type == "content_chunk":
            return TurnStarted.create(...)
        # ...

    def convert_llm_event(self, payload: dict) -> TypedEvent | None:
        """将 LLM lifecycle event 转换为对应的 TypedEvent。"""
        # ...
```

### 4.3 数据流

```
UEPEventPublisher.publish_stream_event(run_id=xxx, event_type="tool_call", ...)
    │
    ▼
UEPToTypedEventConverter.convert_stream_event(payload)
    │
    ▼
TypedEvent (ToolInvoked.create(...))
    │
    ▼
TypedEventBusAdapter.emit_to_both(typed_event)
    │
    ├─► EventRegistry.emit(typed_event)
    │       │
    │       ▼
    │       前端 WebSocket 订阅 ──► UI 更新 ✓
    │
    └─► MessageBus.publish(Message(type=RUNTIME_EVENT, ...))
            │
            ├─► JournalSink ──► journal.jsonl ✓
            ├─► ArchiveSink ──► stream_events.gz ✓
            └─► AuditHashSink ──► HMAC chain ✓
```

---

## 5. 实现计划

### Phase 1: 基础设施（1人）

**任务**：创建 UEP → TypedEvent 转换层

1. 新建 `polaris/kernelone/events/uep_typed_converter.py`
   - 实现 `UEPToTypedEventConverter` 类
   - 为所有 UEP 事件类型实现转换方法
   - 单元测试覆盖

2. 修改 `polaris/kernelone/events/uep_publisher.py`
   - 添加 TypedEventBusAdapter 引用
   - 实现双重写入逻辑
   - 保留直接发 MessageBus 的 fallback

### Phase 2: 集成与测试（1人）

**任务**：集成测试和回归测试

1. 修改 `polaris/kernelone/events/tests/test_uep_publisher.py`
   - 添加 TypedEvent 转换测试
   - 添加双重写入测试

2. 端到端测试
   - 验证前端订阅能收到 UEP 事件
   - 验证 journal 落盘正常

### Phase 3: 清理与文档（1人）

**任务**：清理和文档化

1. 标记废弃路径
   - 直接发 MessageBus 的旧代码标记 deprecated
   - 移除双重发射的冗余逻辑

2. 更新文档
   - 更新蓝图状态
   - 更新架构图

---

## 6. 需要修改的文件清单

### 6.1 新增文件

| 文件 | 用途 |
|------|------|
| `polaris/kernelone/events/uep_typed_converter.py` | UEP → TypedEvent 转换器 |
| `polaris/kernelone/events/tests/test_uep_typed_converter.py` | 转换器单元测试 |

### 6.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `polaris/kernelone/events/uep_publisher.py` | 集成 TypedEventBusAdapter |
| `polaris/kernelone/events/tests/test_uep_publisher.py` | 添加转换测试 |
| `docs/blueprints/KERNELONE_UNIFIED_EVENT_PIPELINE_V2_BLUEPRINT_20260331.md` | 更新状态 |

---

## 7. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| TypedEventBusAdapter 未初始化 | 中 | 高 | UEPEventPublisher 保留直接发 MessageBus 的 fallback |
| 转换层性能开销 | 低 | 低 | 仅在 adapter 可用时转换 |
| 循环依赖 | 低 | 高 | 转换器只依赖 typed/schemas，不依赖 adapter 内部 |

---

## 8. 验收标准

1. ✅ UEP 事件同时出现在 EventRegistry 和 MessageBus
2. ✅ 前端订阅能收到 UEP 发布的 tool_call 事件
3. ✅ journal.jsonl 落盘正常
4. ✅ TypedEvent 和 MessageBus 事件一一对应
5. ✅ ruff check + mypy 零错误
6. ✅ 单元测试覆盖率 > 80%

---

## 9. 团队分工

| 工程师 | 职责 | 交付物 |
|--------|------|--------|
| 工程师 A | 基础设施 | `uep_typed_converter.py` + 单元测试 |
| 工程师 B | 集成测试 | 修改 `uep_publisher.py` + 端到端测试 |
| 工程师 C | 清理文档 | 废弃标记 + 文档更新 |

---

*Blueprint authored by: Principal Architect*
*Next step: Stage 2 Execution (代码实现)*
