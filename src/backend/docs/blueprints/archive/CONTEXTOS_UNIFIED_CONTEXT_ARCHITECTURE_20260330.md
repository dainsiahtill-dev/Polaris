# ContextOS 统一上下文架构重构蓝图

**版本**: 1.0
**日期**: 2026-03-30
**状态**: 待落地
**负责人**: 架构治理实验室
**影响范围**: `polaris/kernelone/context/`, `polaris/cells/roles/kernel/internal/`

---

## 1. 背景与问题

### 1.1 当前架构状态

系统正处于从"基于消息数组（Message Array）"向"基于状态快照（State-Driven Snapshot）"的 Phase 5 过渡阶段，但底层管道仍遗留大量技术债：

```
┌──────────────────────────────────────────────────────────────────────┐
│                    当前系统架构（存在双轨制）                           │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  User Message                                                        │
│       ↓                                                              │
│  RoleTurnRequest                                                    │
│       ↓                                                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  ToolLoopController.__post_init__()                          │   │
│  │  ┌──────────────────────────────────────────────────────┐  │   │
│  │  │ Path A: context_os_snapshot → _history (Phase 5)      │  │   │
│  │  │ Path B: request.history → _history (Legacy)           │  │   │
│  │  └──────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│       ↓                                                              │
│  ContextRequest                                                     │
│       ↓                                                              │
│  RoleContextGateway.build_context()                                 │
│       ↓                                                              │
│  LLMCaller._prepare_llm_request()                                  │
│       ↓                                                              │
│  AIExecutor.invoke()                                               │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心问题列表

| 问题编号 | 严重程度 | 问题描述 |
|----------|----------|----------|
| P0-1 | 严重 | `ToolLoopController._history` 存在双轨来源，ContextOS 无法成为 SSOT |
| P0-2 | 严重 | `_extract_snapshot_history()` 丢失 event_id/sequence/route/dialog_act 元数据 |
| P1-1 | 高 | State-First 模式下压缩策略双轨，导致 token 超限后门 |
| P1-2 | 高 | `ContextRequest` 在多处定义（contracts.py vs context_gateway.py） |
| P2-1 | 中 | `_format_context_os_snapshot()` 截断事件内容至 60 字符 |
| P2-2 | 中 | `_messages_to_input()` 将结构化数据降级为不可逆字符串 |

---

## 2. 目标架构

### 2.1 单一真相来源（SSOT）

```
┌──────────────────────────────────────────────────────────────────────┐
│                    重构后架构（SSOT）                                 │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  User Message                                                        │
│       ↓                                                              │
│  RoleTurnRequest                                                    │
│       ↓                                                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  ToolLoopController.__post_init__()                          │   │
│  │  ┌──────────────────────────────────────────────────────┐  │   │
│  │  │ ONLY: context_os_snapshot → _history (强制路径)      │  │   │
│  │  │ 废除 request.history 作为回退机制                       │  │   │
│  │  └──────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│       ↓                                                              │
│  ContextRequest (统一类型)                                          │
│       ↓                                                              │
│  RoleContextGateway.build_context()                                 │
│       ↓                                                              │
│  LLMCaller._prepare_llm_request()                                  │
│       ↓                                                              │
│  AIExecutor.invoke()                                               │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 事件溯源模型

```
┌─────────────────────────────────────────────────────────────────────┐
│                      事件溯源架构                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ToolResult (Command)                                               │
│       ↓ apply()                                                     │
│  ContextOS.project() [纯函数]                                       │
│       ↓                                                              │
│  ContextOSSnapshot (不可变状态)                                      │
│       ↓ project()                                                    │
│  ContextOSProjection (只读投影视图)                                  │
│       ↓                                                              │
│  LLM Messages                                                       │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 重构任务清单

### 3.1 P0-1: 消除双轨制历史注入

**文件**: `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

**改动**:

```python
# Before (Line 78-94)
def __post_init__(self) -> None:
    self._pending_user_message = str(self.request.message or "")
    snapshot_history = self._extract_snapshot_history()
    if snapshot_history is not self._NO_SNAPSHOT:
        self._history = snapshot_history
        self._seed_tool_results(self.request.tool_results)
    else:
        self._history = self._normalize_history(self.request.history)

# After
def __post_init__(self) -> None:
    self._pending_user_message = str(self.request.message or "")

    # 强制要求 context_os_snapshot，废除 request.history 回退
    snapshot_history = self._extract_snapshot_history()
    if snapshot_history is self._NO_SNAPSHOT:
        raise ValueError(
            "ToolLoopController requires context_os_snapshot. "
            "request.history fallback is deprecated. "
            "Use ContextOS.project() to initialize a baseline snapshot."
        )

    self._history = snapshot_history
    self._seed_tool_results(self.request.tool_results)
```

**验证**: 运行 `test_context_os_ssot_constraint.py`

---

### 3.2 P0-2: 保留完整事件元数据

**文件**: `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

**新增类型**:

```python
@dataclass(frozen=True, slots=True)
class ContextEvent:
    """标准上下文事件类型，替代 (role, content) 元组"""
    event_id: str
    role: str  # "user" | "assistant" | "tool" | "system"
    content: str
    sequence: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_tuple(self) -> tuple[str, str]:
        """兼容旧接口"""
        return (self.role, self.content)
```

**改动 `_extract_snapshot_history()`**:

```python
# Before (Line 96-125)
def _extract_snapshot_history(self) -> list[tuple[str, str]] | object:
    # 返回 List[Tuple[str, str]]

# After
def _extract_snapshot_history(self) -> list[ContextEvent] | object:
    context_override = getattr(self.request, "context_override", None)
    if not isinstance(context_override, dict):
        return self._NO_SNAPSHOT

    snapshot = context_override.get("context_os_snapshot")
    if not isinstance(snapshot, dict):
        return self._NO_SNAPSHOT

    transcript = snapshot.get("transcript_log")
    if not isinstance(transcript, list):
        return self._NO_SNAPSHOT

    result: list[ContextEvent] = []
    for event in transcript:
        if not isinstance(event, dict):
            continue
        # 保留完整元数据
        result.append(ContextEvent(
            event_id=str(event.get("event_id") or "").strip(),
            role=str(event.get("role") or "").strip(),
            content=str(event.get("content") or ""),
            sequence=int(event.get("sequence") or 0),
            metadata=dict(event.get("metadata") or {}),
        ))

    return result if result else self._NO_SNAPSHOT
```

**改动 `_history` 类型**:

```python
# Before
_history: list[tuple[str, str]] = field(default_factory=list)

# After
_history: list[ContextEvent] = field(default_factory=list)
```

**改动 `append_tool_cycle()`**:

```python
def append_tool_cycle(
    self,
    *,
    assistant_message: str,
    tool_results: list[dict[str, Any]],
) -> None:
    if self._last_consumed_message.strip():
        # 构造 ContextEvent 而非元组
        event = ContextEvent(
            event_id=f"user_{len(self._history)}",
            role="user",
            content=self._last_consumed_message,
            sequence=len(self._history),
            metadata={},
        )
        self._history.append(event)

    if assistant_message.strip():
        event = ContextEvent(
            event_id=f"assistant_{len(self._history)}",
            role="assistant",
            content=assistant_message,
            sequence=len(self._history),
            metadata={},
        )
        self._history.append(event)

    for item in tool_results:
        # ... tool result handling
        event = ContextEvent(
            event_id=f"tool_{len(self._history)}",
            role="tool",
            content=self._format_tool_history_result(...),
            sequence=len(self._history),
            metadata={"tool": tool_name},
        )
        self._history.append(event)
```

**验证**: 运行 `test_context_event_metadata.py`

---

### 3.3 P1-1: 统一压缩策略

**文件**: `polaris/cells/roles/kernel/internal/context_gateway.py`

**新增异常**:

```python
class ContextOverflowError(Exception):
    """上下文超出 token 限制且无法进一步压缩"""
    pass
```

**改动 `_apply_compression()`:

```python
# Before (Line 697-752)
def _apply_compression(
    self,
    messages: list[dict[str, str]],
    current_tokens: int,
) -> tuple[list[dict[str, str]], int]:
    # State-First 模式下直接跳过

# After
def _apply_compression(
    self,
    messages: list[dict[str, str]],
    current_tokens: int,
) -> tuple[list[dict[str, str]], int]:
    """统一压缩策略：L1 语义压缩 + L2 物理截断"""
    max_tokens = self.policy.max_context_tokens

    # L2 物理截断：作为绝对安全网
    if current_tokens > max_tokens:
        messages, new_tokens = self._smart_content_truncation(
            messages,
            current_tokens - int(max_tokens * 0.9),
        )
        if new_tokens > max_tokens:
            # 最后手段：紧急截断
            messages = self._emergency_fallback(messages)
            new_tokens = self._estimate_tokens(messages)
            if new_tokens > max_tokens:
                raise ContextOverflowError(
                    f"Context overflow after compression: "
                    f"{new_tokens} tokens > {max_tokens} limit"
                )
        return messages, new_tokens

    return messages, current_tokens
```

**验证**: 运行 `test_context_overflow_guard.py`

---

### 3.4 P1-2: 统一 ContextRequest 定义

**文件**: `polaris/kernelone/context/contracts.py`

**迁移**:

```python
# 删除 `polaris/cells/roles/kernel/internal/context_gateway.py` 中的局部定义
# 统一使用 `polaris/kernelone/context/contracts.py` 中的定义

# TurnEngineContextRequest 已存在，需补充完整字段
@dataclass(frozen=True)
class TurnEngineContextRequest:
    """不可变上下文请求（已统一）"""
    run_id: str = ""
    step: int = 0
    role: str = ""
    mode: str = "default"
    query: str = ""
    # 以下为 Phase 5 新增
    history: tuple[tuple[str, str], ...] = ()  # 保留兼容，但优先级降低
    task_id: str | None = None
    strategy_receipt: Any = None
    # Phase 5: 单一历史来源
    context_os_snapshot: dict[str, Any] | None = None
```

**改动 `context_gateway.py`**:

```python
# 删除局部 ContextRequest 定义，直接导入
from polaris.kernelone.context.contracts import (
    TurnEngineContextRequest as ContextRequest,
    TurnEngineContextResult as ContextResult,
)
```

**验证**: 运行 `test_context_request_unification.py`

---

### 3.5 P2-1: 修复快照摘要展示

**文件**: `polaris/cells/roles/kernel/internal/context_gateway.py`

**改动**:

```python
def _format_context_os_snapshot(
    self,
    snapshot: dict[str, Any],
    verbosity: str = "summary",  # "summary" | "debug"
) -> str:
    """格式化 ContextOS 快照为系统消息

    Args:
        snapshot: 快照字典
        verbosity: "summary" 只显示摘要，"debug" 显示完整元数据
    """
    lines = ["【Context OS State】 (Phase 5 direct path)"]

    transcript = snapshot.get("transcript_log") or []
    if transcript:
        lines.append(f"transcript_events: {len(transcript)} event(s)")

        if verbosity == "debug":
            # 完整打印所有事件，包含元数据
            for event in transcript:
                role = event.get("role", "?")
                content = str(event.get("content", ""))
                event_id = event.get("event_id", "")
                sequence = event.get("sequence", 0)
                metadata = event.get("metadata", {})
                route = metadata.get("route", "")
                dialog_act = metadata.get("dialog_act", "")

                lines.append(
                    f"  [seq={sequence}] {role} (id={event_id[:12]})"
                    f" route={route} act={dialog_act}"
                )
                lines.append(f"    content: {content[:200]}...")
        else:
            # 摘要模式：显示最后 5 个事件
            for event in transcript[-5:]:
                role = event.get("role", "?")
                content = str(event.get("content", ""))[:80]
                lines.append(f"  [{role}] {content}...")

    # ... working_state, artifacts, pending_followup 处理
```

**验证**: 运行 `test_snapshot_verbosity.py`

---

### 3.6 P2-2: 延迟序列化接口

**文件**: `polaris/cells/roles/kernel/internal/llm_caller.py`

**新增接口**:

```python
class ProviderFormatter(Protocol):
    """Provider 特异性格式化接口"""

    def format_messages(
        self,
        messages: list[ContextEvent]
    ) -> list[dict[str, str]]:
        """将上下文事件格式化为 LLM 消息"""
        ...

    def format_tool_result(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> str:
        """格式化工具结果"""
        ...

class NativeProviderFormatter:
    """原生支持消息数组的 Provider（OpenAI/Anthropic）"""
    ...

class AnnotatedProviderFormatter:
    """使用中文注释的 Provider"""
    ...
```

**改动 `_messages_to_input()`:

```python
def _messages_to_input(
    self,
    messages: list[dict[str, str]],
    *,
    format_type: str = "auto",
    provider_id: str = "",
) -> str:
    """延迟序列化：将结构化消息转换为输入

    未来将由 ProviderFormatter 替代
    """
    # 保留现有实现作为向后兼容
    # 新增 ProviderFormatter 路径
```

**验证**: 运行 `test_provider_formatter.py`

---

## 4. 依赖关系

```
P0-2 (元数据保留)
    ↑
P0-1 (消除双轨) ──→ P1-2 (统一 ContextRequest)
    │
    └──→ P1-1 (统一压缩策略)
             │
             └──→ P2-1 (快照摘要修复)
                      │
                      └──→ P2-2 (延迟序列化)
```

---

## 5. 测试清单

| 测试文件 | 覆盖问题 | 状态 |
|----------|----------|------|
| `test_context_os_ssot_constraint.py` | P0-1 | 待编写 |
| `test_context_event_metadata.py` | P0-2 | 待编写 |
| `test_context_overflow_guard.py` | P1-1 | 待编写 |
| `test_context_request_unification.py` | P1-2 | 待编写 |
| `test_snapshot_verbosity.py` | P2-1 | 待编写 |
| `test_provider_formatter.py` | P2-2 | 待编写 |
| `test_turn_engine_run_parity.py` | 回归测试 | 待运行 |
| `test_run_stream_parity.py` | 回归测试 | 待运行 |

---

## 6. 回滚计划

若重构过程中发现阻塞性问题：

1. **P0-1 回滚**: 恢复 `request.history` 回退路径，添加 `DEPRECATION_WARNING`
2. **P0-2 回滚**: 使用 `(role, content)` 元组作为临时类型
3. **P1-1 回滚**: 恢复 State-First 模式跳过压缩逻辑
4. **全部回滚**: 切换回 `feature/enhanced-logger` 分支

---

## 7. 验收标准

- [ ] 所有新增测试 100% 通过
- [ ] 现有回归测试无退化
- [ ] Ruff 检查无 Error/Warning
- [ ] Mypy 检查 Success: no issues found
- [ ] `ContextOS` 成为唯一历史真相来源（可追踪验证）
- [ ] Token 限制在所有模式下生效

---

## 8. 执行团队

详见 `docs/blueprints/TEAM_COMPOSITION_CONTEXTOS_20260330.md`
