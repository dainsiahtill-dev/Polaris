# Polaris Kernel 流式执行架构重构蓝图

**文档编号**: ARCH-2026-0331-POLARIS-KERNEL-REFACTOR
**版本**: v1.1 (已应用关键补丁)
**日期**: 2026-03-31
**状态**: 架构设计阶段（已修复关键盲区）
**负责人**: Principal Architect
**执行团队**: 10x 资深 Python 工程师团队

---

## 1. 执行摘要（Executive Summary）

### 1.1 问题陈述
当前 Polaris Kernel 流式执行系统（`TurnEngine.run_stream`）存在系统性架构缺陷：

1. **职责混乱**: `TurnEngine`、`RoleExecutionKernel`、`LLMCaller` 三者边界模糊
2. **类型系统崩溃**: async generator 与 coroutine 混用导致运行时错误
3. **缺失方法**: `RoleExecutionKernel` 缺少 `_execute_single_tool` 等关键方法
4. **测试债务**: 测试无法运行（`__slots__` 导致 monkeypatch 失败）
5. **状态管理**: `ToolLoopController` 与 transcript 管理存在竞态条件

### 1.2 重构目标
- **可靠性**: 消除所有运行时类型错误
- **可测试性**: 支持无 monkeypatch 的依赖注入测试
- **可维护性**: 单一职责、清晰边界、完整类型注解
- **兼容性**: 100% 保持外部行为不变

### 1.3 关键补丁（Critical Patches Applied）

本蓝图已应用以下关键架构补丁：

| 补丁 | 问题 | 解决方案 |
|------|------|----------|
| **Stream-First Architecture** | Dual-Path Trap（流式/非流式双路径维护导致 parity bugs） | 仅实现 `invoke_stream()`，非流式包装为聚合器 |
| **ToolCallAccumulator** | 流式工具调用 JSON 碎片化边界未定义 | 专用组件缓冲碎片，原子化发射完整 ToolCall |
| **Streaming State Commit Rules** | SSOT 冲突（ContextOS vs ToolLoopController._history） | 明确定义 Commit 时机和双写规则 |
| **Exception Boundaries** | 流取消/资源泄漏处理缺失 | asynccontextmanager 保证清理 |

---

## 2. 系统架构图（System Architecture）

### 2.1 高层架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Client Layer (External API)                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────────┐  │
│   │  kernel.run()   │     │kernel.run_stream│     │  TurnEngine Facade  │  │
│   │   (Non-stream)  │     │   (Streaming)   │     │                     │  │
│   └────────┬────────┘     └────────┬────────┘     └──────────┬──────────┘  │
│            │                       │                         │              │
│            └───────────────────────┼─────────────────────────┘              │
│                                    │                                        │
│                                    ▼                                        │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                      TurnEngine (Orchestrator)                      │  │
│   │  ┌─────────────────────────────────────────────────────────────┐   │  │
│   │  │  Responsibilities:                                          │   │  │
│   │  │  • Turn lifecycle management (round_index, budget checks)   │   │  │
│   │  │  • PolicyLayer integration                                  │   │  │
│   │  │  • Event streaming coordination                             │   │  │
│   │  │  • Error handling & graceful degradation                    │   │  │
│   │  └─────────────────────────────────────────────────────────────┘   │  │
│   └────────────────────────────────┬────────────────────────────────────┘  │
│                                    │                                        │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     RoleExecutionKernel (Core Services)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐    │
│   │   LLMInvoker    │  │   ToolExecutor  │  │    ContextAssembler     │    │
│   │  ┌───────────┐  │  │  ┌───────────┐  │  │  ┌─────────────────┐   │    │
│   │  │invoke_stream│  │  │  │execute()  │  │  │  │build_context()  │   │    │
│   │  │(Stream-First)│ │  │  │batch_exec │  │  │  │compress_if_needed│   │    │
│   │  └───────────┘  │  │  └───────────┘  │  │  └─────────────────┘   │    │
│   └─────────────────┘  └─────────────────┘  └─────────────────────────┘    │
│                                                                             │
│   ┌───────────────────────────────────────────────────────────────────┐    │
│   │                     ToolCallAccumulator (NEW)                     │    │
│   │  ┌─────────────────────────────────────────────────────────────┐ │    │
│   │  │ Responsibilities:                                           │ │    │
│   │  │ • Buffer fragmented tool JSON chunks during streaming       │ │    │
│   │  │ • Emit complete ToolCall objects only when fully assembled  │ │    │
│   │  │ • Handle partial chunks across multiple stream events       │ │    │
│   │  └─────────────────────────────────────────────────────────────┘ │    │
│   └───────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Adapters & Infrastructure                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐    │
│   │  AIExecutor     │  │  ToolGateway    │  │  StreamingPatchBuffer   │    │
│   │  (kernelone)    │  │  (contracts)    │  │  (chunk processing)     │    │
│   └─────────────────┘  └─────────────────┘  └─────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心设计决策（Core Design Decisions）

### 3.1 Stream-First Architecture（关键补丁 #1）

**问题**: Dual-Path Trap
同时维护 `invoke()` 和 `invoke_stream()` 两条路径会导致：
- 代码重复
- 行为不一致（parity bugs）
- 维护负担翻倍

**解决方案**:
仅实现 `invoke_stream()`，非流式需求包装为聚合器：

```python
class LLMInvoker:
    """LLM调用器 - Stream-First架构"""

    async def invoke_stream(self, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        """唯一核心实现 - 流式调用

        所有LLM交互都通过此方法，非流式需求使用包装器。
        """
        # ... 实现逻辑 ...
        async for chunk in self._executor.invoke_stream(prepared.ai_request):
            normalized = self._normalize_chunk(chunk)
            yield normalized

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """非流式包装器 - 100%代码复用

        将流式结果聚合为单一响应，确保行为一致性。
        """
        chunks: list[str] = []
        tool_calls: list[ToolCall] = []

        async for event in self.invoke_stream(request):
            if event.type == "content_chunk":
                chunks.append(event.content)
            elif event.type == "tool_call":
                tool_calls.append(ToolCall.from_event(event))
            elif event.type == "error":
                return LLMResponse(error=event.error)

        return LLMResponse(
            content="".join(chunks),
            tool_calls=[tc.to_dict() for tc in tool_calls],
        )
```

**优势**:
- 100% 代码路径共享
- 零 parity bugs
- 单一 truth source

---

### 3.2 ToolCallAccumulator（关键补丁 #2）

**问题**: 流式工具调用 JSON 碎片化
LLM 流式输出中，工具调用 JSON 可能被分割成多个 chunk：

```
Chunk 1: {"tool": "read_file",
Chunk 2:  "args": {"path": "
Chunk 3:  "test.py"}}
```

**解决方案**: 专用缓冲组件

```python
@dataclass
class ToolCallAccumulator:
    """工具调用缓冲累加器

    在流式执行期间缓冲碎片化的工具调用JSON，
    仅当完整解析后才发射 ToolCall 对象。

    Attributes:
        _buffer: 当前累积的原始内容
        _pending_calls: 已识别但未完成的工具调用
        _completed_calls: 已完成解析的工具调用队列
    """

    _buffer: str = field(default="")
    _pending_calls: list[PartialToolCall] = field(default_factory=list)
    _completed_calls: deque[ToolCall] = field(default_factory=deque)

    def feed(self, chunk: str) -> list[ToolCall]:
        """喂入新 chunk，返回已完成的 ToolCall 列表

        Args:
            chunk: 流式输出的文本片段

        Returns:
            本次喂入后新完成的 ToolCall 列表（可能为空）
        """
        self._buffer += chunk
        completed = []

        # 尝试解析缓冲区中的工具调用
        while True:
            result = self._try_extract_tool_call()
            if result is None:
                break
            call, consumed = result
            completed.append(call)
            self._buffer = self._buffer[consumed:]

        return completed

    def flush(self) -> list[ToolCall]:
        """强制刷新，丢弃不完整内容

        Returns:
            最后时刻完成的 ToolCall 列表
        """
        completed = list(self._completed_calls)
        self._completed_calls.clear()
        self._buffer = ""
        self._pending_calls.clear()
        return completed

    def _try_extract_tool_call(self) -> tuple[ToolCall, int] | None:
        """尝试从缓冲区提取一个完整的工具调用

        Returns:
            (ToolCall, consumed_length) 或 None
        """
        # 1. 查找 TOOL_CALL 标记
        start = self._buffer.find(TOOL_CALL_OPEN)
        if start == -1:
            return None

        # 2. 查找对应的闭合标记
        end = self._buffer.find(TOOL_CALL_CLOSE, start)
        if end == -1:
            return None  # 不完整，等待更多数据

        # 3. 提取并解析 JSON
        json_str = self._buffer[start:end + len(TOOL_CALL_CLOSE)]
        try:
            parsed = json.loads(json_str)
            call = ToolCall(
                tool=parsed["tool"],
                args=parsed.get("args", {}),
                call_id=str(uuid4()),
            )
            return call, end + len(TOOL_CALL_CLOSE)
        except (json.JSONDecodeError, KeyError) as e:
            # 无效 JSON，跳过此标记
            logger.warning(f"Invalid tool call JSON: {e}")
            return None
```

**边界规则**:
1. **原子性**: 只发射完整解析的 ToolCall，不发射部分
2. **顺序性**: 保持工具调用在流中的原始顺序
3. **超时保护**: 长时间不完整时强制 flush

---

### 3.3 Streaming State Commit Rules（关键补丁 #3）

**问题**: SSOT 冲突
`ToolLoopController._history` 和 `ContextOS` 存在双写风险。

**解决方案**: 明确定义 Commit 规则

```python
class StreamingStateManager:
    """流式状态管理器 - 明确定义 Commit 规则

    SSOT原则:
    1. ContextOS.transcript_log 是 prior turns 的唯一真相来源
    2. ToolLoopController._history 是当前 turn 的 scratchpad
    3. 当前 turn 结束时，原子化地将 _history 写入 ContextOS

    Commit 时机:
    - A: 每轮 LLM 响应结束（assistant message）
    - B: 每个工具执行完成（tool result）
    - C: Turn 完全结束（final persistence）
    """

    def __init__(
        self,
        context_os: ContextOS,
        controller: ToolLoopController,
    ):
        self._context_os = context_os
        self._controller = controller
        # 从 ContextOS 种子化 scratchpad
        self._controller._history = list(context_os.transcript_log)

    def commit_after_llm_response(self, turn: Turn) -> None:
        """Commit Point A: LLM响应后

        将 assistant message 追加到 scratchpad，
        但不立即写入 ContextOS（等待工具结果）。
        """
        self._controller.append_assistant_message(turn)
        # 注意: 此时不写入 ContextOS

    def commit_after_tool_execution(
        self,
        tool_results: list[ToolResult],
    ) -> None:
        """Commit Point B: 工具执行后

        将工具结果增量追加到 scratchpad。
        这是与 non-stream 保持一致的关键。
        """
        for result in tool_results:
            self._controller.append_tool_result(result)
            # 增量可见: 后续工具能看到前面工具的结果

    def final_commit(self) -> None:
        """Commit Point C: Turn 结束

        原子化地将完整 scratchpad 写入 ContextOS，
        成为新的唯一真相来源。
        """
        final_transcript = tuple(self._controller._history)
        self._context_os.replace_transcript(final_transcript)
```

**关键规则**:

| 阶段 | 写入位置 | ContextOS 可见性 |
|------|----------|------------------|
| LLM 响应中 | `_history` (scratchpad) | 不可见 |
| 工具执行中 | `_history` (incremental) | 不可见 |
| Turn 结束 | `ContextOS.transcript_log` | 可见（新 SSOT）|

---

### 3.4 Exception Boundaries（关键补丁 #4）

**问题**: 流取消和资源泄漏
异步生成器被突然取消时，可能留下未清理的资源。

**解决方案**: asynccontextmanager 保证清理

```python
from contextlib import asynccontextmanager
from typing import AsyncIterator


class StreamContext:
    """流执行上下文 - 保证资源清理"""

    def __init__(self, invoker: LLMInvoker, request: LLMRequest):
        self._invoker = invoker
        self._request = request
        self._active_stream: AsyncIterator[StreamEvent] | None = None
        self._closed = False

    @asynccontextmanager
    async def stream(self) -> AsyncIterator[StreamEvent]:
        """受保护的流式上下文

        保证即使通过 asyncio.CancelledError 取消，
        也能正确清理 HTTP 连接和适配器资源。
        """
        self._active_stream = self._invoker.invoke_stream(self._request)
        try:
            async for event in self._active_stream:
                yield event
        except asyncio.CancelledError:
            # 用户取消 - 正常清理
            await self._cleanup()
            raise  # 重新抛出，让调用者知道被取消
        except Exception:
            # 其他异常 - 记录并清理
            logger.exception("Stream error")
            await self._cleanup()
            raise
        finally:
            # 正常结束或异常都执行清理
            if not self._closed:
                await self._cleanup()

    async def _cleanup(self) -> None:
        """资源清理"""
        if self._closed:
            return
        self._closed = True

        # 1. 关闭活跃流（如果有）
        if self._active_stream is not None:
            try:
                await self._active_stream.aclose()
            except Exception:
                logger.warning("Error closing stream", exc_info=True)

        # 2. 释放连接池
        try:
            await self._invoker.release_connections()
        except Exception:
            logger.warning("Error releasing connections", exc_info=True)

        # 3. 回滚未持久化的状态
        try:
            await self._invoker.rollback_pending_state()
        except Exception:
            logger.warning("Error rolling back state", exc_info=True)


# TurnEngine 中的使用
class TurnEngine:
    async def run_stream(self, request, role, controller) -> AsyncIterator[Event]:
        stream_ctx = StreamContext(self._llm_invoker, llm_request)
        async with stream_ctx.stream() as events:
            async for event in events:
                yield self._transform_event(event)
```

**清理保证**:
1. **aclose()**: 强制关闭异步生成器
2. **finally 块**: 确保清理代码执行
3. **幂等性**: cleanup 可安全多次调用

---

## 4. 模块职责划分（Module Responsibilities）

### 4.1 TurnEngine（编排器）

```python
class TurnEngine:
    """Turn-level orchestrator implementing Stream-First Architecture.

    Responsibilities:
        - Turn lifecycle: round_index tracking, budget enforcement
        - PolicyLayer integration: pre/post call validation
        - Event streaming: yield events for streaming mode
        - Error handling: catch, classify, and recover from failures
        - Exception boundary: guarantee cleanup via StreamContext

    Dependencies (injected):
        - llm_invoker: LLMInvokerProtocol (stream-first)
        - tool_executor: ToolExecutorProtocol
        - state_manager: StreamingStateManager (commit rules)
    """

    async def run(self, request, role, controller) -> TurnResult:
        """Non-streaming execution - delegates to run_stream with aggregation."""
        aggregator = EventAggregator()
        async for event in self.run_stream(request, role, controller):
            aggregator.feed(event)
        return aggregator.to_result()

    async def run_stream(
        self,
        request: RoleTurnRequest,
        role: str,
        controller: ToolLoopController,
    ) -> AsyncIterator[Event]:
        """Streaming execution with proper exception boundaries."""
        stream_ctx = StreamContext(self._llm_invoker, llm_request)
        async with stream_ctx.stream() as llm_events:
            async for event in self._process_events(llm_events, controller):
                yield event
```

### 4.2 RoleExecutionKernel（核心服务 Facade）

```python
class RoleExecutionKernel:
    """Service provider facade over specialized services.

    This class delegates all operations to injected services.
    All methods are stateless; state lives in StreamingStateManager.

    Services provided:
        - llm_invoker: LLM call coordination (Stream-First)
        - tool_executor: Tool execution
        - context_assembler: Context building and compression
        - tool_accumulator: Fragmented tool call buffering
    """

    def __init__(
        self,
        workspace: str,
        registry: RoleRegistry,
        llm_invoker: LLMInvokerProtocol | None = None,
        tool_executor: ToolExecutorProtocol | None = None,
        context_assembler: ContextAssemblerProtocol | None = None,
        tool_accumulator: ToolCallAccumulator | None = None,
    ):
        """Dependency injection constructor for testability."""
        self._llm_invoker = llm_invoker or LLMInvoker()
        self._tool_executor = tool_executor or ToolExecutor()
        self._context_assembler = context_assembler or ContextAssembler()
        self._tool_accumulator = tool_accumulator or ToolCallAccumulator()
```

### 4.3 LLMInvoker（Stream-First LLM调用器）

```python
class LLMInvoker:
    """Handles all LLM interactions via Stream-First Architecture.

    Responsibilities:
        - invoke_stream(): Core streaming implementation (100% coverage)
        - invoke(): Non-streaming wrapper (aggregates stream events)
        - Request preparation: messages, tools, parameters
        - Error classification: retry decisions
        - Event emission: lifecycle events for observability

    No dual paths - everything builds on invoke_stream().
    """

    async def invoke_stream(
        self,
        request: LLMRequest,
    ) -> AsyncIterator[StreamEvent]:
        """Core streaming invocation - single source of truth."""
        ...

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """Non-streaming wrapper - aggregates stream events."""
        ...
```

### 4.4 ToolCallAccumulator（工具调用缓冲器）

```python
@dataclass
class ToolCallAccumulator:
    """Buffers fragmented tool JSON during streaming.

    Responsibilities:
        - Buffer fragmented tool call JSON chunks
        - Emit complete ToolCall objects atomically
        - Handle partial chunks across stream events

    Boundary:
        - Sits between LLMInvoker and ToolExecutor
        - Only emits when ToolCall is 100% parsed
    """

    def feed(self, chunk: str) -> list[ToolCall]:
        """Feed chunk, return list of completed ToolCalls."""
        ...

    def flush(self) -> list[ToolCall]:
        """Force flush, drop incomplete content."""
        ...
```

### 4.5 StreamingStateManager（流式状态管理器）

```python
class StreamingStateManager:
    """Manages state commit rules for streaming execution.

    Responsibilities:
        - Define Commit Points A/B/C
        - Coordinate scratchpad (_history) and SSOT (ContextOS)
        - Ensure atomic state transitions

    SSOT Rules:
        - Prior turns: ContextOS.transcript_log
        - Current turn: ToolLoopController._history (scratchpad)
        - Commit C: Atomic write scratchpad -> ContextOS
    """

    def commit_after_llm_response(self, turn: Turn) -> None:
        """Commit Point A"""
        ...

    def commit_after_tool_execution(self, results: list[ToolResult]) -> None:
        """Commit Point B"""
        ...

    def final_commit(self) -> None:
        """Commit Point C - Atomic"""
        ...
```

---

## 5. 核心数据流（Data Flows）

### 5.1 Stream-First Execution Flow

```
┌──────────┐     ┌─────────────┐     ┌──────────────────┐
│  Client  │────▶│  run()      │────▶│  run_stream()    │
└──────────┘     └─────────────┘     └────────┬─────────┘
                                              │
                                              ▼
                                      ┌───────────────┐
                                      │ EventAggregator│
                                      │ (wraps stream) │
                                      └───────┬───────┘
                                              │
                                              ▼
┌──────────┐     ┌─────────────┐     ┌──────────────────┐
│  Result  │◀────│  Aggregate  │◀────│  invoke_stream() │
└──────────┘     └─────────────┘     └──────────────────┘
```

### 5.2 Tool Call Accumulation Flow

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│ LLM Stream   │────▶│ ToolCallAccumulator│────▶│ ToolExecutor │
│ (fragments)  │     │ (buffers & parses)  │     │ (full calls) │
└──────────────┘     └──────────────────┘     └──────────────┘

Example:
  Stream:  {"tool": "read" ⏸  "_file", ⏸  "args": {...}}
                     ↓
  Accumulator: [buffering...] → [parsing...] → ToolCall(tool="read_file", args={...})
                     ↓
  Executor:  executes complete ToolCall
```

### 5.3 State Commit Flow

```
Turn Start:
  ContextOS.transcript_log ────────┐
                                   ├──► ToolLoopController._history (scratchpad)
  New user message ────────────────┘

During Execution:
  LLM response ──► _history.append(assistant)
  Tool results ──► _history.append(tool_results) [incremental]

Turn End (Commit Point C):
  _history ──► ContextOS.replace_transcript() ──► New SSOT
```

---

## 6. 测试策略（Testing Strategy）

### 6.1 Stream/Non-Stream Parity Test

```python
@pytest.mark.asyncio
async def test_stream_non_stream_parity():
    """Verify run() and run_stream() produce equivalent results."""
    fake_llm = FakeLLMInvoker([
        {"content": "Let me help", "tool_calls": [{"tool": "read_file", "args": {"path": "x.py"}}]},
        {"content": "Done"},
    ])

    # Non-stream
    kernel_ns = RoleExecutionKernel(llm_invoker=fake_llm)
    result_ns = await kernel_ns.run(request, role, controller)

    # Stream (aggregated)
    fake_llm.reset()
    kernel_s = RoleExecutionKernel(llm_invoker=fake_llm)
    chunks = []
    async for event in kernel_s.run_stream(request, role, controller):
        chunks.append(event)
    result_s = aggregate_events(chunks)

    # Assert parity
    assert result_ns.content == result_s.content
    assert result_ns.tool_calls == result_s.tool_calls
```

### 6.2 ToolCallAccumulator Test

```python
def test_accumulator_emits_only_complete_calls():
    """Verify partial chunks don't emit incomplete ToolCalls."""
    acc = ToolCallAccumulator()

    # Fragmented tool call
    result1 = acc.feed('{"tool": "read')
    assert result1 == []  # No complete calls yet

    result2 = acc.feed('_file", "args": {"path": "test.py"}}')
    assert len(result2) == 1
    assert result2[0].tool == "read_file"
```

---

## 7. 迁移计划（Migration Plan）

### Phase 1: 服务层提取（Week 1-2）
1. 创建 `LLMInvoker` with Stream-First Architecture
2. 创建 `ToolCallAccumulator`
3. 创建 `StreamingStateManager`
4. 创建 `StreamContext` exception boundaries

### Phase 2: 测试与验证（Week 3）
1. Stream/Non-Stream parity tests
2. Tool accumulation boundary tests
3. State commit rule tests
4. Exception boundary tests

### Phase 3: Kernel Facade 重构（Week 4）
1. 重构 `RoleExecutionKernel` 注入新服务
2. 更新 `TurnEngine` 使用 `StreamContext`
3. 验证所有测试通过

### Phase 4: 清理与交付（Week 5）
1. 删除旧代码（`call_sync.py`, `call_structured.py` 等）
2. 文档更新
3. 团队知识分享

---

## 8. 成功标准（Success Criteria）

- [ ] 所有现有测试通过（修复 mock 问题后）
- [ ] 新增测试覆盖率达到 90%+
- [ ] `run_stream` 和 `run` 100% 行为等价（parity 测试）
- [ ] 零 monkeypatch（全依赖注入）
- [ ] 完整类型注解（mypy strict 通过）
- [ ] ruff / black 零警告
- [ ] 性能无退化（< 5% 开销）
- [ ] **Stream-First 架构**: 仅 `invoke_stream()` 核心实现，非流式包装
- [ ] **ToolCallAccumulator**: 碎片缓冲边界明确定义
- [ ] **State Commit Rules**: SSOT 双写规则文档化
- [ ] **Exception Boundaries**: 流取消资源清理保证

---

**下一步**: 进入 Phase 1，基于已打补丁的架构实现服务层。

**审批**: _________________
**日期**: _________________
