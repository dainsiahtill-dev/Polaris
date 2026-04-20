# ADR-CONTEXTOS-002: 延迟序列化架构决策

**状态**: 已接受
**日期**: 2026-03-31
**决策者**: Python 架构与代码治理实验室
**影响范围**: `polaris/kernelone/context/`, `polaris/cells/roles/kernel/internal/`

---

## 背景

当前 ContextOS 架构中，结构化事件数据过早降级为字符串，导致：

1. **信息丢失**: event_id、sequence、route、dialog_act 等元数据在序列化时丢失
2. **不可逆转换**: `(role, content)` 元组无法还原为完整事件
3. **压缩受限**: 字符串级别的压缩无法利用结构化信息做智能裁剪
4. **调试困难**: 问题发生时只能看到最终字符串，无法追溯原始事件

这是 ContextOS 重构蓝图 P2-2 问题（`_messages_to_input()` 将结构化数据降级为不可逆字符串）。

## 问题

### 2.1 当前序列化路径

```
ContextEvent (含 metadata)
    ↓ _extract_snapshot_history() [丢失元数据]
tuple(role, content)
    ↓ _messages_to_input()
str (不可逆)
    ↓ format()
LLM 消息
```

### 2.2 具体问题点

| 问题 | 位置 | 影响 |
|------|------|------|
| 元数据丢失 | `_extract_snapshot_history()` | event_id、sequence、route、dialog_act 丢失 |
| 类型降级 | 返回 `list[tuple[str, str]]` | 无法携带 tool_name 等信息 |
| 字符串截断 | `_format_context_os_snapshot()` | 内容被截断至 60/80 字符 |
| 格式耦合 | `_messages_to_input()` | Provider 格式硬编码 |

## 决策

采用**延迟序列化**（Delayed Serialization）模式：保持结构化数据在内存中流转，直到必须与 Provider 交互时才进行序列化。

### 3.1 核心原则

1. **结构化不降级**: `ContextEvent` 贯穿整个处理链路
2. **延迟序列化**: 序列化推迟到 Provider 交互边界
3. **元数据可追踪**: 所有事件元数据端到端保留
4. **Provider 无关**: 核心逻辑不依赖特定 Provider 格式

### 3.2 延迟序列化架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                    延迟序列化架构                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ContextOSSnapshot.transcript_log  [结构化 ContextEvent 列表]       │
│       │                                                              │
│       ↓ project()                                                    │
│       │                                                              │
│  ToolLoopController._history  [list[ContextEvent]]                   │
│       │                                                              │
│       ↓ append_tool_cycle()                                          │
│       │                                                              │
│  TurnEngine.run() → ContextResult                                    │
│       │                                                              │
│       ↓ _build_context_request()                                     │
│       │                                                              │
│  RoleContextGateway.build_context()                                  │
│       │                                                              │
│       ↓ format_events() [延迟序列化点]                               │
│       │                                                              │
│  ProviderFormatter.format_messages()                                 │
│       │                                                              │
│       ↓                                                              │
│  LLM Provider SDK (JSON 序列化)                                      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.3 关键实现

#### 3.3.1 ContextEvent 作为唯一事件类型

```python
# polaris/kernelone/context/contracts.py

@dataclass(frozen=True, slots=True)
class ContextEvent:
    """不可变上下文事件，贯穿整个处理链路"""
    event_id: str
    role: str  # "user" | "assistant" | "tool" | "system"
    content: str
    sequence: int
    metadata: dict[str, Any] = field(default_factory=dict, hash=False)

    def to_tuple(self) -> tuple[str, str]:
        """向后兼容接口，仅在边界处使用"""
        return (self.role, self.content)

    def with_content(self, new_content: str) -> "ContextEvent":
        """创建内容更新后的副本"""
        return ContextEvent(
            event_id=self.event_id,
            role=self.role,
            content=new_content,
            sequence=self.sequence,
            metadata=self.metadata,
        )
```

#### 3.3.2 ToolLoopController 保留结构化历史

```python
# polaris/cells/roles/kernel/internal/tool_loop_controller.py

from polaris.kernelone.context.contracts import ContextEvent

@dataclass
class ToolLoopController:
    _history: list[ContextEvent] = field(default_factory=list)  # 改为 ContextEvent 列表

    def append_tool_cycle(
        self,
        *,
        assistant_message: str,
        tool_results: list[dict[str, Any]],
    ) -> None:
        # 追加 user 消息
        if self._last_consumed_message.strip():
            event = ContextEvent(
                event_id=f"user_{len(self._history)}",
                role="user",
                content=self._last_consumed_message,
                sequence=len(self._history),
                metadata={},
            )
            self._history.append(event)

        # 追加 assistant 消息
        if assistant_message.strip():
            event = ContextEvent(
                event_id=f"assistant_{len(self._history)}",
                role="assistant",
                content=assistant_message,
                sequence=len(self._history),
                metadata={},
            )
            self._history.append(event)

        # 追加 tool 结果
        for item in tool_results:
            event = ContextEvent(
                event_id=f"tool_{len(self._history)}",
                role="tool",
                content=self._format_tool_history_result(item),
                sequence=len(self._history),
                metadata={"tool": item.get("tool_name", "unknown")},
            )
            self._history.append(event)
```

#### 3.3.3 ContextGateway 延迟格式化

```python
# polaris/cells/roles/kernel/internal/context_gateway.py

from polaris.kernelone.context.contracts import ContextEvent, ProviderFormatter

class RoleContextGateway:
    def build_context(
        self,
        events: list[ContextEvent],
        formatter: ProviderFormatter,
        format_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建上下文，延迟到 ProviderFormatter 才进行序列化

        Args:
            events: 结构化事件列表（不降级）
            formatter: Provider 格式化器
            format_config: 格式化配置

        Returns:
            包含结构化事件和已格式化消息的上下文
        """
        # 智能压缩（基于结构化数据）
        compressed_events = self._smart_compress(events)

        # 延迟序列化：只在这里转换为 Provider 格式
        formatted_messages = formatter.format_messages(compressed_events)

        return {
            "events": compressed_events,  # 保留结构化引用
            "messages": formatted_messages,  # Provider 格式化结果
            "metadata": {
                "original_count": len(events),
                "compressed_count": len(compressed_events),
                "formatter": formatter.provider_id,
            },
        }

    def _smart_compress(
        self,
        events: list[ContextEvent],
    ) -> list[ContextEvent]:
        """基于结构化元数据的智能压缩

        可以在保留关键元数据的同时进行内容裁剪
        """
        if len(events) <= self.policy.min_events:
            return events

        # 保留策略：首尾消息 + 关键中间消息
        preserved: list[ContextEvent] = []
        for i, event in enumerate(events):
            if i == 0 or i == len(events) - 1:
                preserved.append(event)
            elif event.role == "assistant" and event.metadata.get("has_tool_call"):
                preserved.append(event)
            elif event.metadata.get("is_decision_point"):
                preserved.append(event)
        return preserved
```

#### 3.3.4 ProviderFormatter 边界序列化

```python
# polaris/kernelone/context/formatters/native_formatter.py

class NativeProviderFormatter:
    """在 Provider 交互边界进行最终序列化"""

    def format_messages(
        self,
        events: list[ContextEvent],
    ) -> list[dict[str, Any]]:
        """将结构化事件转换为 Provider 消息格式

        这是延迟序列化的最终点，之后由 Provider SDK 处理
        """
        return [
            {
                "role": event.role,
                "content": event.content,
                # Provider 特定字段（可选）
                **({"name": event.metadata["tool"]} if event.metadata.get("tool") else {}),
            }
            for event in events
        ]
```

### 3.4 压缩策略升级

延迟序列化允许更智能的压缩：

```python
# 基于结构化元数据的压缩策略

COMPRESSION_RULES = {
    "user": {"preserve": "all", "truncate_if": 10000},
    "assistant": {"preserve": ["has_tool_call", "is_decision_point"], "truncate_if": 8000},
    "tool": {"preserve": ["failed"], "truncate_if": 6000},
    "system": {"preserve": "all", "truncate_if": 4000},
}
```

## 修改的文件

| 文件 | 修改内容 |
|------|---------|
| `polaris/kernelone/context/contracts.py` | 添加 `ContextEvent.with_content()` 方法 |
| `polaris/cells/roles/kernel/internal/tool_loop_controller.py` | `_history` 类型改为 `list[ContextEvent]` |
| `polaris/cells/roles/kernel/internal/context_gateway.py` | 添加延迟序列化 `build_context()` |
| `polaris/kernelone/context/formatters/native_formatter.py` | 实现边界序列化 |

## 后果

### 正面

- **元数据端到端保留**: event_id、sequence、route、dialog_act 可追踪
- **智能压缩**: 可基于事件类型和元数据做智能裁剪
- **调试友好**: 可在任意点检查原始结构化事件
- **格式解耦**: 核心逻辑与 Provider 格式隔离

### 负面

- 结构化事件占用更多内存（但可通过压缩缓解）
- 需要确保所有边界点正确序列化

### 权衡

| 方面 | 之前 | 之后 |
|------|------|------|
| 内存占用 | 低（字符串） | 中（结构化 + 压缩） |
| 信息保留 | 丢失 | 完整 |
| 压缩能力 | 字符串级别 | 结构化感知 |
| 调试成本 | 高（只能看字符串） | 低（可追溯） |

## 验证

1. `pytest polaris/kernelone/context/tests/test_delayed_serialization.py -v`
2. `pytest polaris/cells/roles/kernel/internal/tests/test_context_event_metadata.py -v`
3. 验证 `ContextEvent` 元数据在 `ToolLoopController._history` → `ContextGateway.build_context()` → `ProviderFormatter.format_messages()` 链路中完整保留
4. `mypy polaris/kernelone/context/contracts.py -v`
5. `ruff check polaris/kernelone/context/ --fix`

## 回滚计划

若发现阻塞性问题：

1. 恢复 `request.history` 回退路径（添加 DEPRECATION_WARNING）
2. 使用 `(role, content)` 元组作为临时类型
3. 切换回 `feature/enhanced-logger` 分支

## 参考

- `ADR-CONTEXTOS-001-provider-formatter-protocol.md` - ProviderFormatter Protocol
- `docs/blueprints/CONTEXTOS_UNIFIED_CONTEXT_ARCHITECTURE_20260330.md` - ContextOS 重构蓝图
- `polaris/kernelone/context/contracts.py` - 契约定义
- `polaris/cells/roles/kernel/internal/tool_loop_controller.py` - 控制器实现
- `polaris/cells/roles/kernel/internal/context_gateway.py` - 网关实现
