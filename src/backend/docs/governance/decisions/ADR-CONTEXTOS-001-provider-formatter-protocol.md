# ADR-CONTEXTOS-001: ProviderFormatter 架构决策

**状态**: 已接受
**日期**: 2026-03-31
**决策者**: Python 架构与代码治理实验室
**影响范围**: `polaris/cells/roles/kernel/internal/`, `polaris/kernelone/context/`

---

## 背景

ContextOS 当前存在结构化数据降级为不可逆字符串的问题（P2-2）。`llm_caller.py` 中的 `_messages_to_input()` 方法将结构化的 `ContextEvent` 列表转换为字符串，丢失了事件元数据（event_id、sequence、route、dialog_act）。

此外，不同 Provider（OpenAI/Anthropic/annotated）需要不同的消息格式，但当前格式化逻辑散落在 `llm_caller.py` 中，难以扩展和维护。

## 问题

1. **结构化降级**: `ContextEvent` (含完整元数据) 被序列化为 `(role, content)` 元组，再降级为字符串
2. **格式不透明**: Provider 特异性格式化逻辑与调用逻辑耦合
3. **难以测试**: 无法单独测试特定 Provider 的格式化行为
4. **扩展性差**: 新增 Provider 需要修改核心调用逻辑

## 决策

引入 `ProviderFormatter` Protocol 接口，作为 ContextOS 与 Provider 之间的格式化契约。

### 1. Protocol 定义

```python
# polaris/kernelone/context/contracts.py

from typing import Protocol, Any

class ProviderFormatter(Protocol):
    """Provider 特异性格式化接口

    定义 Provider 如何将结构化 ContextEvent 序列化为 LLM 消息格式。
    每个 Provider 实现负责：
    - 将 ContextEvent 转换为 Provider 兼容的消息格式
    - 处理 Provider 特定的工具调用格式
    - 管理 Provider 特定的系统提示词
    """

    @property
    def provider_id(self) -> str:
        """Provider 唯一标识"""
        ...

    def format_messages(
        self,
        events: list["ContextEvent"],
    ) -> list[dict[str, Any]]:
        """将上下文事件格式化为 LLM 消息

        Args:
            events: 结构化上下文事件列表

        Returns:
            Provider 兼容的消息字典列表
        """
        ...

    def format_tool_result(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> str:
        """格式化工具结果为字符串

        Args:
            tool_name: 工具名称
            result: 工具执行结果

        Returns:
            格式化的工具结果字符串
        """
        ...

    def format_system_prompt(
        self,
        system_context: dict[str, Any],
    ) -> str:
        """格式化系统提示词上下文

        Args:
            system_context: 系统上下文字典

        Returns:
            格式化的系统提示词字符串
        """
        ...
```

### 2. ContextEvent 类型定义

```python
# polaris/kernelone/context/contracts.py

from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True, slots=True)
class ContextEvent:
    """标准上下文事件类型

    替代 (role, content) 元组，保留完整事件元数据。
    """
    event_id: str
    role: str  # "user" | "assistant" | "tool" | "system"
    content: str
    sequence: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_tuple(self) -> tuple[str, str]:
        """向后兼容接口"""
        return (self.role, self.content)
```

### 3. 实现类

#### 3.1 NativeProviderFormatter

支持原生消息数组的 Provider（OpenAI/Anthropic）:

```python
# polaris/kernelone/context/formatters/native_formatter.py

@dataclass(frozen=True)
class NativeProviderFormatter:
    """原生支持消息数组的 Provider 格式化器"""
    provider_id: str = "native"

    def format_messages(
        self,
        events: list[ContextEvent],
    ) -> list[dict[str, Any]]:
        result = []
        for event in events:
            msg = {"role": event.role, "content": event.content}
            # 保留 metadata 中的 tool 信息
            if event.metadata.get("tool"):
                msg["name"] = event.metadata["tool"]
            result.append(msg)
        return result

    def format_tool_result(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> str:
        import json
        return json.dumps(result, ensure_ascii=False, default=str)

    def format_system_prompt(
        self,
        system_context: dict[str, Any],
    ) -> str:
        return system_context.get("prompt", "")
```

#### 3.2 AnnotatedProviderFormatter

使用中文注释的 Provider:

```python
# polaris/kernelone/context/formatters/annotated_formatter.py

@dataclass(frozen=True)
class AnnotatedProviderFormatter:
    """中文注释 Provider 格式化器"""
    provider_id: str = "annotated"

    def format_messages(
        self,
        events: list[ContextEvent],
    ) -> list[dict[str, Any]]:
        result = []
        for event in events:
            content = event.content
            # 为 tool 角色添加中文标注
            if event.role == "tool":
                tool_name = event.metadata.get("tool", "unknown")
                content = f"[{tool_name} 执行结果]\n{event.content}"
            result.append({"role": event.role, "content": content})
        return result

    def format_tool_result(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> str:
        import json
        return f"[{tool_name}]\n{json.dumps(result, ensure_ascii=False, default=str)}"

    def format_system_prompt(
        self,
        system_context: dict[str, Any],
    ) -> str:
        parts = []
        if prompt := system_context.get("prompt"):
            parts.append(prompt)
        if rules := system_context.get("rules"):
            parts.append(f"[规则]\n{rules}")
        return "\n\n".join(parts)
```

### 4. 集成点

#### 4.1 LLMCaller 改造

```python
# polaris/cells/roles/kernel/internal/llm_caller.py

from polaris.kernelone.context.contracts import ProviderFormatter, ContextEvent
from polaris.kernelone.context.formatters import NativeProviderFormatter, AnnotatedProviderFormatter

_FORMATTER_REGISTRY: dict[str, ProviderFormatter] = {
    "native": NativeProviderFormatter(),
    "annotated": AnnotatedProviderFormatter(),
}

class LLMCaller:
    def __init__(
        self,
        provider_id: str = "native",
        formatter: ProviderFormatter | None = None,
    ) -> None:
        self._formatter = formatter or _FORMATTER_REGISTRY.get(provider_id, NativeProviderFormatter())

    def _format_events(
        self,
        events: list[ContextEvent],
    ) -> list[dict[str, Any]]:
        """使用 ProviderFormatter 格式化事件"""
        return self._formatter.format_messages(events)

    def _format_tool_result(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> str:
        """使用 ProviderFormatter 格式化工具结果"""
        return self._formatter.format_tool_result(tool_name, result)
```

#### 4.2 延迟序列化保证

```
ContextEvent (结构化)
    ↓ format_messages() [ProviderFormatter]
    ↓
list[dict[str, Any]] (Provider 消息格式)
    ↓
LLM 请求体 (JSON 序列化由 Provider SDK 完成)
```

## 修改的文件

| 文件 | 修改内容 |
|------|---------|
| `polaris/kernelone/context/contracts.py` | 添加 `ProviderFormatter` Protocol 和 `ContextEvent` dataclass |
| `polaris/kernelone/context/formatters/__init__.py` | 新建格式化器模块 |
| `polaris/kernelone/context/formatters/native_formatter.py` | 新建原生格式化器 |
| `polaris/kernelone/context/formatters/annotated_formatter.py` | 新建注释格式化器 |
| `polaris/cells/roles/kernel/internal/llm_caller.py` | 集成 ProviderFormatter，替换 `_messages_to_input()` |

## 后果

### 正面

- **元数据保留**: ContextEvent 完整元数据通过 ProviderFormatter 传递，不丢失
- **职责分离**: 格式化逻辑与调用逻辑解耦
- **可测试性**: 可独立测试每个 ProviderFormatter 的行为
- **扩展性**: 新增 Provider 只需实现 Protocol，无需修改核心逻辑
- **可追踪性**: 格式化过程可审计

### 负面

- 引入新抽象层，增加代码复杂度
- 需要维护 Formatter 注册表

### 风险缓解

- Protocol 使用 `Protocol` 类型，静态类型检查器可验证实现
- 提供默认 `NativeProviderFormatter` 保证向后兼容
- 旧 `_messages_to_input()` 接口作为兼容层保留

## 验证

1. `pytest polaris/kernelone/context/tests/test_provider_formatter.py -v`
2. `mypy polaris/kernelone/context/formatters/ -v`
3. `ruff check polaris/kernelone/context/formatters/ --fix`

## 参考

- `docs/blueprints/CONTEXTOS_UNIFIED_CONTEXT_ARCHITECTURE_20260330.md` - ContextOS 重构蓝图
- `polaris/cells/roles/kernel/internal/llm_caller.py` - 现有 LLM 调用器
- `polaris/kernelone/context/contracts.py` - 现有契约定义
