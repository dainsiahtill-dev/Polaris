# ADR-0062: OpenCode 机制集成

## 状态

已接受 (Accepted)

## 上下文

Polaris 需要从 OpenCode 项目引入以下机制来增强其能力：

1. **Typed Events**: 带 Schema 验证和 wildcard 订阅的事件系统
2. **ToolState FSM**: 完整的工具执行状态追踪
3. **Part Types**: Discriminated Union 消息类型系统
4. **Edit Replacers**: 9 种模糊匹配策略链

### 技术现状分析

| 组件 | Polaris 现状 | OpenCode 实现 | 差距 |
|------|-----------------|--------------|------|
| 事件系统 | MessageBus (Actor Model) | Zod Schema + BusEvent | 无 Schema 验证、无 wildcard |
| 工具状态 | 简单 Status Literal | ToolState + SubState | 无完整状态追踪 |
| 消息类型 | 分离 dataclass | Discriminated Union | 无统一类型联合 |
| 编辑策略 | 6 种模糊策略 | 9 种策略链 | 缺少上下文感知 |

## 决策

### 决策 1: Typed Events 实现

**采用 Pydantic + Annotated Union 实现**

```python
# 方案 A: 使用 Pydantic discriminated unions (已选择)
class EventBase(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_name: str
    category: EventCategory
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

TypedEvent = Annotated[
    InstanceStarted | ToolInvoked | ToolCompleted | ...,
    Field(discriminator="event_name")
]

# 方案 B: Zod-style validation (否决)
# 理由: Pydantic 与项目现有技术栈一致

# 方案 C: dataclass + 手动验证 (否决)
# 理由: 缺少 Schema 验证和类型联合
```

**保留 MessageBus 向后兼容**

```python
class BusAdapter:
    """桥接 Typed Events 和 MessageBus"""

    async def emit_to_both(self, event: TypedEvent) -> None:
        """双向写入"""
        await self.typed_registry.emit(event)
        await self.message_bus.publish(
            self._event_to_message_type(event),
            self._event_to_payload(event)
        )
```

### 决策 2: ToolState FSM 设计

**采用 Enum + Dataclass 组合**

```python
# 状态定义
class ToolStateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

# 子状态追踪
class ToolPendingSubState(str, Enum):
    QUEUED = "queued"
    SCHEDULED = "scheduled"
    WAITING_INPUT = "waiting_input"

# 状态机
@dataclass
class ToolState:
    status: ToolStateStatus = ToolStateStatus.PENDING
    sub_state: ToolPendingSubState | ToolRunningSubState | None = None
    error_kind: ToolErrorKind | None = None
    # ... 完整字段追踪
```

**验证规则**

```
PENDING → RUNNING | CANCELLED
RUNNING → COMPLETED | ERROR | TIMEOUT | BLOCKED | CANCELLED
Terminal → (无)
```

### 决策 3: Part Types 系统

**采用 Pydantic discriminated unions**

```python
class PartType(str, Enum):
    TEXT = "text"
    TOOL = "tool"
    FILE = "file"
    REASONING = "reasoning"
    # ...

class PartBase(BaseModel):
    part_id: str
    session_id: str | None
    message_id: str | None

class TextPart(PartBase):
    part_type: Literal[PartType.TEXT] = PartType.TEXT
    text: str

class ToolPart(PartBase):
    part_type: Literal[PartType.TOOL] = PartType.TOOL
    call_id: str
    tool: str
    state: ToolState

Part = Annotated[
    TextPart | ToolPart | FilePart | ReasoningPart | ...,
    Field(discriminator="part_type")
]
```

### 决策 4: Edit Replacers 策略链

**采用 OpenCode 风格的 Generator 模式**

```python
Replacer = Generator[str, None, None]  # Yields matched text

class SimpleReplacer:
    name = "simple"
    priority = 10

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        if search in content:
            yield search

class BlockAnchorReplacer:
    """首尾锚定 + Levenshtein 相似度"""
    name = "block_anchor"
    priority = 30
    # 使用 string_similarity() 计算中间内容相似度

# 默认策略链
DEFAULT_REPLACERS = [
    SimpleReplacer,       # 10
    LineTrimmedReplacer,  # 20
    BlockAnchorReplacer,  # 30
    WhitespaceNormalizedReplacer,  # 40
    IndentationFlexibleReplacer,   # 50
    EscapeNormalizedReplacer,      # 60
    TrimmedBoundaryReplacer,       # 70
    ContextAwareReplacer,          # 80
    MultiOccurrenceReplacer,       # 90
]
```

**与现有 Polaris 策略的关系**

- 互补而非替代: OpenCode replacers 补充现有 `apply_fuzzy_search_replace`
- 可集成: 可将 OpenCode replacers 作为 pre-processor 使用

## 后果

### 正面

1. **类型安全增强**: Pydantic Schema 验证减少运行时错误
2. **可观测性提升**: ToolState 提供完整执行追踪
3. **灵活性提升**: Wildcard 订阅支持灵活的事件处理
4. **编辑鲁棒性**: 9 种策略提高模糊匹配的准确性

### 负面

1. **学习曲线**: 新开发者需要理解 discriminated unions
2. **测试覆盖**: 需要为新模块编写完整测试
3. **依赖增加**: Pydantic 作为核心依赖

### 中性

1. **代码增加**: 新增约 2000 行代码
2. **迁移成本**: 向后兼容需要适配器层

## 测试策略

| 模块 | 测试数 | 覆盖范围 |
|------|--------|----------|
| `events/typed/` | 52 | Schema 验证、Registry、Adapter |
| `tool/` | 100 | State Machine、Tracker |
| `messages/` | 41 | Part Types、Serialization |
| `editing/replacers/` | 33 | 9 种策略、工具函数 |

**总计: 226 tests, 100% PASS**

## 实现时间线

| 阶段 | 模块 | 工作量 |
|------|------|--------|
| Phase 1 | Typed Events + ToolState | 2 weeks |
| Phase 2 | Part Types + Replacers | 2 weeks |
| Phase 3 | 集成 + 文档 | 1 week |

## 决策者

- Architecture Team
- KernelOne Squad

## 日期

2026-03-27

## 参考资料

- OpenCode `packages/opencode/src/bus/index.ts`
- OpenCode `packages/opencode/src/session/message-v2.ts`
- OpenCode `packages/opencode/src/tool/edit.ts`
- Polaris `polaris/kernelone/events/message_bus.py`
- Polaris `polaris/kernelone/editing/search_replace_engine.py`
