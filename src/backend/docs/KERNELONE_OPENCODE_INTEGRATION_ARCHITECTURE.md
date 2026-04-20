# OpenCode 机制集成架构文档

## 1. 概述

本文档描述从 OpenCode 项目引入的机制如何在 Polaris 中实现和集成。

### 1.1 引入的模块

| 模块 | 来源 | 功能 | 状态 |
|------|------|------|------|
| **Typed Events** | `packages/opencode/src/bus/index.ts` | 带 Schema 验证和 wildcard 订阅的事件系统 | ✅ 已实现 |
| **ToolState FSM** | `packages/opencode/src/session/message-v2.ts` | 完整的工具状态机追踪 | ✅ 已实现 |
| **Part Types** | `packages/opencode/src/session/message-v2.ts` | Discriminated Union 消息类型 | ✅ 已实现 |
| **Edit Replacers** | `packages/opencode/src/tool/edit.ts` | 9 种模糊匹配策略链 | ✅ 已实现 |

### 1.2 与 Polaris 现有系统的关系

```
┌─────────────────────────────────────────────────────────────┐
│                    Polaris 架构                         │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────────────────┐  │
│  │   MessageBus    │    │    Typed Events (NEW)       │  │
│  │  (Actor Model)  │◄──►│   EventRegistry + Schema    │  │
│  │  + NATS/JetStream│   │   Wildcard 订阅             │  │
│  └─────────────────┘    └─────────────────────────────┘  │
│                              │                             │
│  ┌─────────────────┐        │                             │
│  │  Tool Executor   │◄───────┼───────────────────────────→ │
│  │  (现有)          │        │                             │
│  └─────────────────┘        ▼                             │
│                    ┌─────────────────────┐                 │
│                    │   ToolState FSM     │                 │
│                    │ (NEW) + Tracker    │                 │
│                    └─────────────────────┘                 │
│                              │                             │
│  ┌─────────────────┐        │                             │
│  │  Search/Replace │◄───────┼───────────────────────────→ │
│  │  Engine (现有)   │        │                             │
│  └─────────────────┘        ▼                             │
│                    ┌─────────────────────┐                 │
│                    │  Edit Replacers     │                 │
│                    │ (NEW) 9 策略链     │                 │
│                    └─────────────────────┘                 │
│                              │                             │
│                              ▼                             │
│                    ┌─────────────────────┐                 │
│                    │    Part Types      │                 │
│                    │ (NEW) Message Parts │                 │
│                    └─────────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
```

## 2. Typed Events 系统

### 2.1 架构设计

```
polaris/kernelone/events/typed/
├── __init__.py              # 模块导出
├── schemas.py               # EventBase, TypedEvent union, EventCategory
├── registry.py             # EventRegistry (wildcard 订阅)
├── bus_adapter.py          # MessageBus 适配器
└── tests/
    ├── test_schemas.py     # 52 tests
    ├── test_registry.py     # 22 tests
    └── test_bus_adapter.py  # 17 tests
```

### 2.2 核心类型

```python
# EventCategory 用于 wildcard 订阅
class EventCategory(str, Enum):
    LIFECYCLE = "lifecycle"
    TOOL = "tool"
    TURN = "turn"
    DIRECTOR = "director"
    CONTEXT = "context"
    AUDIT = "audit"
    SYSTEM = "system"

# TypedEvent discriminated union
TypedEvent = Annotated[
    InstanceStarted | InstanceDisposed | ToolInvoked | ToolCompleted | ToolError | ...,
    Discriminator(_event_discriminator)
]
```

### 2.3 EventRegistry 特性

- **Wildcard 订阅**: `registry.subscribe("tool.*", handler)` 匹配 `tool_invoked`, `tool_completed` 等
- **Priority 排序**: 高优先级 handler 先执行
- **同步/异步支持**: 同时支持 sync 和 async handlers
- **向后兼容**: 通过 `BusAdapter` 与现有 MessageBus 桥接

### 2.4 使用示例

```python
from polaris.kernelone.events.typed.registry import EventRegistry
from polaris.kernelone.events.typed.schemas import ToolInvoked

registry = EventRegistry()

# 订阅特定事件
registry.subscribe("tool_invoked", on_tool_invoked)

# 订阅 wildcard
registry.subscribe("tool.*", on_all_tool_events)

# 订阅所有错误
registry.subscribe("*.error", on_error)

# 发射事件
await registry.emit(ToolInvoked.create(
    tool_name="read_file",
    tool_call_id="call_123"
))
```

## 3. ToolState FSM

### 3.1 状态机设计

```
┌──────────┐    ┌──────────┐    ┌────────────┐
│ PENDING  │───►│ RUNNING  │───►│ COMPLETED  │
└──────────┘    └──────────┘    └────────────┘
     │               │
     │               ├──────────►┌──────┐
     │               │           │ERROR │
     │               │           └──────┘
     │               │
     │               ├──────────►┌────────┐
     │               │           │TIMEOUT │
     │               │           └────────┘
     │               │
     │               ├──────────►┌─────────┐
     │               │           │BLOCKED  │
     │               │           └─────────┘
     │               │
     └───────────────┴──────────►┌───────────┐
                                 │ CANCELLED │
                                 └───────────┘
```

### 3.2 子状态

```python
# PENDING 子状态
class ToolPendingSubState(str, Enum):
    QUEUED = "queued"           # 在执行队列中
    SCHEDULED = "scheduled"    # 调度执行
    WAITING_INPUT = "waiting_input"  # 等待输入

# RUNNING 子状态
class ToolRunningSubState(str, Enum):
    INITIALIZING = "initializing"  # 设置执行环境
    EXECUTING = "executing"          # 正在执行
    FINALIZING = "finalizing"        # 清理中
```

### 3.3 错误分类

```python
class ToolErrorKind(str, Enum):
    EXCEPTION = "exception"     # 未处理异常
    VALIDATION = "validation"   # 参数校验失败
    PERMISSION = "permission"   # 权限拒绝
    NOT_FOUND = "not_found"     # 资源未找到
    RUNTIME = "runtime"         # 运行时错误
    TIMEOUT = "timeout"         # 执行超时
    CANCELLED = "cancelled"     # 执行取消
    NETWORK = "network"         # 网络错误
    RATE_LIMIT = "rate_limit"   # 速率限制
    UNKNOWN = "unknown"         # 未知错误
```

### 3.4 文件结构

```
polaris/kernelone/tool/
├── __init__.py              # 模块导出
├── state_machine.py         # ToolState FSM + 转换验证
├── tracker.py               # ToolCallTracker
└── tests/
    ├── __init__.py
    ├── test_state_machine.py  # 57 tests
    └── test_tracker.py        # 43 tests
```

### 3.5 使用示例

```python
from polaris.kernelone.tool.state_machine import (
    ToolState, ToolStateStatus, ToolErrorKind
)
from polaris.kernelone.tool.tracker import ToolCallTracker

tracker = ToolCallTracker()

# 创建工具调用
state = await tracker.create(
    "read_file",
    tool_call_id="call_123",
    correlation_id="parent_op"
)

# 状态转换
await tracker.start("call_123")  # PENDING -> RUNNING
await tracker.complete("call_123", result={"content": "..."})  # RUNNING -> COMPLETED

# 错误处理
await tracker.fail(
    "call_456",
    "Permission denied",
    error_kind=ToolErrorKind.PERMISSION
)

# 重试
await tracker.retry("call_456")  # 重置为 PENDING
```

## 4. Part Types 系统

### 4.1 Discriminated Union 设计

```python
class PartType(str, Enum):
    TEXT = "text"
    TOOL = "tool"
    FILE = "file"
    REASONING = "reasoning"
    SUBTASK = "subtask"
    STEP_START = "step-start"
    STEP_FINISH = "step-finish"
    SNAPSHOT = "snapshot"
    PATCH = "patch"
    AGENT = "agent"
    RETRY = "retry"
    COMPACTION = "compaction"

# Part discriminated union
Part = Annotated[
    TextPart | SubtaskPart | ReasoningPart | FilePart | ToolPart | ...,
    Field(discriminator="part_type")
]
```

### 4.2 核心 Part 类型

| 类型 | 用途 | 关键字段 |
|------|------|----------|
| `TextPart` | 文本内容 | `text`, `synthetic`, `ignored` |
| `ToolPart` | 工具调用 | `call_id`, `tool`, `state` |
| `FilePart` | 文件附件 | `mime`, `filename`, `url` |
| `ReasoningPart` | 推理过程 | `text`, `time` |
| `SubtaskPart` | 子任务 | `prompt`, `description`, `agent` |
| `StepStartPart` | 步骤开始 | `snapshot` |
| `StepFinishPart` | 步骤完成 | `reason`, `cost`, `tokens` |

### 4.3 文件结构

```
polaris/kernelone/messages/
├── __init__.py              # 模块导出
├── part_types.py            # 所有 Part 类型定义
└── tests/
    ├── __init__.py
    └── test_part_types.py    # 41 tests
```

### 4.4 使用示例

```python
from polaris.kernelone.messages.part_types import (
    TextPart, ToolPart, MessageContent, MessageRole,
    ToolStatePending, ToolStateCompleted
)

# 创建消息内容
message = MessageContent(role=MessageRole.ASSISTANT)

# 添加文本部分
message = message.add_text("让我来分析这段代码...")

# 添加工具部分
tool_state = ToolStateCompleted(
    input={"path": "/file.py"},
    output="分析结果",
    title="代码分析",
    time={"start": 100, "end": 200}
)
message = message.add_tool(
    "analyze_code",
    "call_analyze",
    tool_state
)
```

## 5. Edit Replacers 策略链

### 5.1 策略优先级

| 优先级 | Replacer | 说明 |
|--------|----------|------|
| 10 | `SimpleReplacer` | 精确字符串匹配 |
| 20 | `LineTrimmedReplacer` | 行级空白忽略 |
| 30 | `BlockAnchorReplacer` | 首尾行锚定 + Levenshtein 相似度 |
| 40 | `WhitespaceNormalizedReplacer` | 空白归一化 |
| 50 | `IndentationFlexibleReplacer` | 相对缩进匹配 |
| 60 | `EscapeNormalizedReplacer` | 转义字符处理 |
| 70 | `TrimmedBoundaryReplacer` | 边界修剪匹配 |
| 80 | `ContextAwareReplacer` | 上下文锚定 + 模糊中间 |
| 90 | `MultiOccurrenceReplacer` | 多匹配处理 |

### 5.2 文件结构

```
polaris/kernelone/editing/replacers/
├── __init__.py                  # 模块导出
├── base.py                      # EditReplacer 抽象基类
├── opencode_replacers.py       # 9 种策略实现
└── tests/
    ├── __init__.py
    └── test_opencode_replacers.py  # 33 tests
```

### 5.3 使用示例

```python
from polaris.kernelone.editing.replacers import (
    SimpleReplacer,
    LineTrimmedReplacer,
    BlockAnchorReplacer,
    get_replacer_chain
)

# 获取默认策略链
chain = get_replacer_chain()

# 手动使用特定策略
content = "    def hello():\n        pass"
search = "def hello():\n    pass"

for match in LineTrimmedReplacer.find(content, search):
    print(f"Found: {repr(match)}")
```

### 5.4 与现有 Polaris 策略的关系

Polaris 已有的 `apply_fuzzy_search_replace` 策略：

| Polaris 策略 | OpenCode Replacer |
|------------------|-------------------|
| `_replace_unique_window` (strip_ws=False) | `SimpleReplacer` |
| `_replace_unique_window` (strip_ws=True) | `LineTrimmedReplacer` |
| `_leading_whitespace_offset_apply` | `IndentationFlexibleReplacer` |
| `_try_dotdot_ellipsis` | Contextual ellipsis (NEW) |
| `_relative_indent_apply` | `IndentationFlexibleReplacer` |
| `_dmp_apply` | Levenshtein similarity |
| `_sequence_match_apply` | Fuzzy matching |

## 6. 测试覆盖

### 6.1 测试统计

| 模块 | 测试数 | 状态 |
|------|--------|------|
| `events/typed/` | 52 | ✅ PASS |
| `tool/` | 100 | ✅ PASS |
| `messages/` | 41 | ✅ PASS |
| `editing/replacers/` | 33 | ✅ PASS |
| **总计** | **226** | **✅ ALL PASS** |

### 6.2 测试命令

```bash
# 运行所有新模块测试
pytest polaris/kernelone/events/typed/tests/ \
       polaris/kernelone/tool/tests/ \
       polaris/kernelone/messages/tests/ \
       polaris/kernelone/editing/replacers/tests/ -v

# 运行特定模块测试
pytest polaris/kernelone/tool/tests/test_state_machine.py -v
pytest polaris/kernelone/editing/replacers/tests/ -v
```

## 7. 迁移指南

### 7.1 从 MessageBus 迁移到 Typed Events

```python
# Before (MessageBus)
from polaris.kernelone.events.message_bus import MessageBus

MessageBus.publish(MessageType.TASK_STARTED, {"tool_name": "read_file"})

# After (Typed Events)
from polaris.kernelone.events.typed.schemas import ToolInvoked
from polaris.kernelone.events.typed.registry import EventRegistry

registry = EventRegistry()
await registry.emit(ToolInvoked.create(
    tool_name="read_file",
    tool_call_id="call_123"
))
```

### 7.2 使用 ToolState FSM

```python
# Before (直接状态管理)
tool_status = {"status": "pending", "result": None}

def complete_tool(result):
    tool_status["status"] = "completed"
    tool_status["result"] = result

# After (ToolState FSM)
from polaris.kernelone.tool.tracker import ToolCallTracker

tracker = ToolCallTracker()
state = await tracker.create("read_file", tool_call_id="call_123")
await tracker.start("call_123")
await tracker.complete("call_123", result={"content": "..."})
```

## 8. 风险与限制

### 8.1 向后兼容

- Typed Events 可与现有 MessageBus 并行使用
- `BusAdapter` 提供双向转换

### 8.2 性能考虑

- EventRegistry 使用 asyncio.Lock 保证线程安全
- 策略链按优先级顺序尝试，找到即停

### 8.3 已知限制

- `ContextAwareReplacer` 要求至少 3 行才能使用锚定
- `EscapeNormalizedReplacer` 仅处理有限转义序列

## 9. 参考资料

- OpenCode: `packages/opencode/src/bus/index.ts`
- OpenCode: `packages/opencode/src/session/message-v2.ts`
- OpenCode: `packages/opencode/src/tool/edit.ts`
