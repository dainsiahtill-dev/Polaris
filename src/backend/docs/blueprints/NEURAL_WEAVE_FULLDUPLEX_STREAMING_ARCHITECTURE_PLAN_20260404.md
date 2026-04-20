# 神经织网（Neural Weave）全双工流式架构重构蓝图

**状态**: 已完成
**日期**: 2026-04-04
**负责人**: Python 架构十人委员会
**依赖**: `polaris/kernelone/stream/`, `polaris/kernelone/llm/engine/stream/`

---

## ✅ 实施状态

| Phase | 状态 | 文件 |
|-------|------|------|
| Phase 1: 核心流式协议层 | ✅ 完成 | `polaris/kernelone/stream/sse_streamer.py` |
| Phase 2: 背压控制升级 | ✅ 完成 | `AsyncBackpressureBuffer` + `EventStreamer` |
| Phase 3: FastAPI 集成 | ✅ 完成 | `polaris/delivery/http/routers/stream_router.py` |
| Phase 4: 验证与文档 | ✅ 完成 | `test_sse_streamer.py` + `SSE_EVENT_SCHEMA_20260404.md` |
| 扩展: 工具生命周期 | ✅ 完成 | `StreamEventType.TOOL_START/TOOL_END` |
| 扩展: 优雅关闭 | ✅ 完成 | `EventStreamer.close(timeout=...)` |
| 扩展: 订阅者限制 | ✅ 完成 | `EventStreamer(max_subscriptions=N)` |
| 扩展: 背压统计 | ✅ 完成 | `get_stats()` 含 `total_dropped` |
| 废弃: 旧 BackpressureBuffer | ✅ 完成 | 添加 `DeprecationWarning` |

---

## 1. 阻塞式 I/O 诊断报告

### 1.1 当前架构缺陷

| 缺陷 | 位置 | 影响 |
|------|------|------|
| 无 SSE HTTP 序列化层 | `stream/executor.py` 输出 `AIStreamGenerator` 但无 SSE 端点 | 前端无法直接消费 `AIStreamEvent` |
| `threading.Lock` 而非 `asyncio.Queue` | `backpressure.py:42` `BackpressureBuffer._buffer_lock` | GIL 竞争，async 上下文效率低 |
| 单一消费者模式 | `StreamExecutor.invoke_stream()` | 无法多路复用（thinking + tool_log + 最终答案同时推送） |
| 无 `EventStreamer` 类 | 缺失 | `AIStreamEvent` → SSE `data: {...}\n\n` 转换缺失 |

### 1.2 `threading.Lock` vs `asyncio.Queue` 对比

```python
# 当前问题代码 (backpressure.py:42)
self._buffer_lock = threading.Lock()

# async 上下文中使用 threading.Lock 的问题:
# 1. GIL 竞争：Lock 阻塞事件循环
# 2. 无法 await：feed() 内部无法真正让出控制权
# 3. 背压失效：threading 的 sleep 不如 asyncio.sleep 高效
```

### 1.3 结构化流 vs 文本流差异

| 维度 | `_invoke_structured_stream` | `_invoke_text_stream` |
|------|----------------------------|----------------------|
| Provider 接口 | `invoke_stream_events()` | `invoke_stream()` |
| 解析器 | `provider_adapter.decode_stream_event()` | `StreamThinkingParser` + `XMLToolParser` |
| Tool Call 组装 | `_ToolCallAccumulator` 增量组装 | XML 解析回退 |
| 状态 | 实时 `StreamState.IN_TOOL_CALL` | 无状态追踪 |

---

## 2. 神经织网拓扑蓝图

### 2.1 数据流向图

```
LLM Token Stream
       │
       ▼
┌─────────────────────────┐
│ Provider.invoke_stream() │
│   or invoke_stream_events()│
└────────────┬────────────┘
             │ raw events (token/thinking/tool delta)
             ▼
┌─────────────────────────────────────────┐
│ StreamExecutor._invoke_structured_stream │
│         or _invoke_text_stream          │
│  → 输出 AIStreamEvent 生成器              │
└────────────┬────────────────────────────┘
             │ AIStreamEvent (CHUNK/REASONING/TOOL_CALL/COMPLETE/ERROR)
             ▼
┌─────────────────────────────────────────┐
│          EventStreamer.sse_events()      │
│  - 转换为 SSE 格式 data: {...}\n\n        │
│  - 多路复用广播（asyncio.Queue）          │
│  - 背压控制（asyncio.Queue maxsize）     │
└────────────┬────────────────────────────┘
             │ SSE bytes stream
             ▼
┌─────────────────────────────────────────┐
│     FastAPI StreamingResponse           │
│   Content-Type: text/event-stream        │
└────────────┬────────────────────────────┘
             │ HTTP SSE
             ▼
         Frontend
```

### 2.2 多路复用关键点

```
EventStreamer 使用 asyncio.Queue 作为广播中枢:

Producer (StreamExecutor)          Consumer 1 (Thinking Display)
        │                                    │
        │  asyncio.Queue                    │
        ├──────────────────────────────────►│
        │                                    │
        │                           Consumer 2 (Tool Log)
        ├──────────────────────────────────────────────────►│
        │                                    │
        │                           Consumer 3 (Final Answer)
        └──────────────────────────────────────────────────►│
```

### 2.3 复用清单

| 现有组件 | 复用方式 |
|----------|----------|
| `kernelone/stream/ports.py::stream_from_async_gen()` | 最终 SSE 编码的生成器封装 |
| `kernelone/llm/engine/stream/executor.py::AIStreamEvent` | 事件类型定义 |
| `kernelone/llm/engine/stream/executor.py::StreamEventType` | 事件类型枚举 |
| `kernelone/llm/engine/stream/config.py::StreamConfig` | 配置注入 |
| `kernelone/llm/engine/stream/tool_accumulator.py::_ToolCallAccumulator` | Tool Call 增量组装（直接使用） |
| `kernelone/llm/engine/stream/backpressure.py::BackpressureBuffer` | 替换为 asyncio.Queue |

---

## 3. 核心代码设计

### 3.1 EventStreamer 类

```python
# polaris/kernelone/stream/sse_streamer.py

class EventStreamer:
    """SSE 序列化 + 多路复用广播器

    将 AIStreamEvent 序列化为 SSE 格式，使用 asyncio.Queue 支持多消费者。
    """

    def __init__(
        self,
        config: StreamConfig | None = None,
        max_queue_size: int = 100,
    ) -> None:
        self._config = config or StreamConfig.from_env()
        self._queue: asyncio.Queue[AIStreamEvent | None] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._consumers: list[asyncio.Queue[AIStreamEvent | None]] = []
        self._closed = False

    def sse_serialize(self, event: AIStreamEvent) -> bytes:
        """将 AIStreamEvent 序列化为 SSE data 格式."""
        import json
        data = event.to_dict()
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

    async def publish(self, event: AIStreamEvent) -> None:
        """发布事件到所有消费者队列（背压感知）."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # 背压：队列满时等待而非丢弃
            await self._queue.put(event)

    async def subscribe(self) -> AsyncGenerator[AIStreamEvent, None]:
        """创建新的消费者订阅，返回独立队列."""
        q: asyncio.Queue[AIStreamEvent | None] = asyncio.Queue(maxsize=50)
        self._consumers.append(q)
        try:
            while True:
                event = await q.get()
                if event is None:  # 结束信号
                    break
                yield event
        finally:
            self._consumers.remove(q)

    async def broadcast(self, events: AIStreamGenerator) -> None:
        """消费事件流并广播到所有订阅者."""
        async for event in events:
            await self.publish(event)
            # 广播到所有消费者队列
            for consumer_q in self._consumers:
                try:
                    consumer_q.put_nowait(event)
                except asyncio.QueueFull:
                    # 消费者慢时丢弃最旧的
                    try:
                        consumer_q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        consumer_q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

    async def close(self) -> None:
        """关闭广播，发送结束信号."""
        self._closed = True
        for consumer_q in self._consumers:
            await consumer_q.put(None)  # 结束信号
```

### 3.2 SSE StreamingResponse 集成

```python
# polaris/delivery/http/routers/stream_router.py

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

@router.post("/v2/stream/chat")
async def stream_chat(request: ChatRequest) -> StreamingResponse:
    """流式聊天端点，返回 SSE 格式."""
    streamer = EventStreamer()

    async def sse_generator() -> AsyncGenerator[bytes, None]:
        async for event in streamer.subscribe():
            yield streamer.sse_serialize(event)

    # 后台启动广播任务
    asyncio.create_task(
        streamer.broadcast(
            stream_executor.invoke_stream(convert_to_ai_request(request))
        )
    )

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )
```

### 3.3 工具调用流信令

```python
# 工具调用状态信令扩展

class EventStreamer:
    """扩展：工具调用流信令"""

    async def broadcast(self, events: AIStreamGenerator) -> None:
        """增强版广播：tool_call 开始/结束发送状态信令."""
        async for event in events:
            # 工具调用开始信令
            if event.type == StreamEventType.TOOL_CALL:
                tool_start = AIStreamEvent(
                    type=StreamEventType.META,
                    meta={"tool_start": event.tool_call.get("tool"), "call_id": event.tool_call.get("call_id")},
                )
                await self.publish(tool_start)

            await self.publish(event)

            # 工具调用结束信令（在 complete 事件前）
            if event.type == StreamEventType.COMPLETE:
                tool_end = AIStreamEvent(
                    type=StreamEventType.META,
                    meta={"stream_end": True},
                )
                await self.publish(tool_end)
```

---

## 4. 实施计划

### Phase 1: 核心流式协议层
- [x] 创建 `polaris/kernelone/stream/sse_streamer.py`
- [x] 实现 `EventStreamer` 类（复用 `AIStreamEvent`、`StreamConfig`）
- [x] 实现 `sse_serialize()` 方法
- [x] 实现 `asyncio.Queue` 基础多路复用

### Phase 2: 背压控制升级
- [x] 将 `BackpressureBuffer` 迁移到 `asyncio.Queue`
- [x] 保持 `feed()`/`drain()` 接口兼容
- [x] 添加 `maxsize` 参数到 `EventStreamer`

### Phase 3: FastAPI 集成
- [x] 创建 `polaris/delivery/http/routers/stream_router.py`
- [x] 实现 `/v2/stream/chat` 端点
- [x] 配置 `StreamingResponse` + SSE Content-Type
- [x] 实现工具调用状态信令（tool_start/tool_end）

### Phase 4: 验证与文档
- [x] 编写 `test_sse_streamer.py` 单元测试
- [x] 编写 `test_event_streamer_multiplexer.py` 多路复用测试
- [x] 更新 `docs/blueprints/NEURAL_WEAVE_*.md` 为最终版

---

## 5. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| `threading.Lock` 替换为 `asyncio.Queue` 影响现有代码 | 保持 `BackpressureBuffer` 接口兼容，新增 `AsyncBackpressureBuffer` |
| 多路复用引入复杂性 | 先实现单消费者 SSE，Phase 2 再扩展广播 |
| 现有 `StreamExecutor` 不支持广播 | 不修改原类，新增 `BroadcastStreamExecutor` 包装 |

---

## 6. 验收标准

1. **SSE 序列化**: `EventStreamer.sse_serialize()` 输出 `data: {...}\n\n` 格式
2. **多路复用**: 3 个消费者同时订阅同一事件流，都能收到完整事件序列
3. **背压控制**: 消费者慢时，队列满后新事件等待而非丢弃
4. **类型安全**: 100% mypy 检查通过
5. **测试覆盖**: 新增测试 100% PASS
