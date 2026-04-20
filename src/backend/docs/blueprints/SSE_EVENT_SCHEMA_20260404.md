# SSE Event Schema Specification

**状态**: 已实现
**日期**: 2026-04-04
**版本**: 1.0.0

---

## 概述

本规范定义了 Polaris 神经织网（Neural Weave）全双工流式架构中 Server-Sent Events (SSE) 的事件契约。所有 SSE 事件均为 JSON 格式，通过 `data:` 字段传输。

## 事件类型映射

### StreamEventType → SSE event field

| `StreamEventType` 值 | SSE `event:` 字段 | 说明 |
|---------------------|------------------|------|
| `chunk` | `chunk` | 文本块 |
| `reasoning_chunk` | `reasoning` | 推理/思考过程 |
| `tool_start` | `tool_start` | 工具执行开始 |
| `tool_call` | `tool_call` | 结构化工具调用载荷 |
| `tool_end` | `tool_end` | 工具执行结束 |
| `tool_result` | `tool_result` | 工具执行结果 |
| `meta` | `meta` | 元事件（如 keep-alive ping 响应） |
| `complete` | `complete` | 流结束 |
| `error` | `error` | 错误 |

---

## 事件 Schema

### 1. CHUNK 事件 (`event: chunk`)

文本块事件，用于实时 token 推送。

```json
{
  "type": "chunk",
  "chunk": "Hello, world!",
  "meta": {
    "provider": "openai",
    "trace_id": "abc123"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"chunk"` |
| `chunk` | string | 是 | 文本内容 |
| `meta` | object | 否 | 元数据（如 provider, trace_id） |

### 2. REASONING_CHUNK 事件 (`event: reasoning`)

推理/思考过程事件，用于展示 LLM 的思维链。

```json
{
  "type": "reasoning_chunk",
  "reasoning": "Let me think about this...",
  "meta": {
    "provider": "anthropic",
    "trace_id": "def456"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"reasoning_chunk"` |
| `reasoning` | string | 是 | 推理内容 |
| `meta` | object | 否 | 元数据 |

### 3. TOOL_START 事件 (`event: tool_start`)

工具执行开始事件，标记工具生命周期的开始。

```json
{
  "type": "tool_start",
  "tool_call": {
    "tool": "repo_read_head",
    "call_id": "call_123"
  },
  "meta": {
    "provider": "kernelone",
    "trace_id": "ghi789"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"tool_start"` |
| `tool_call.tool` | string | 是 | 工具名称 |
| `tool_call.call_id` | string | 否 | 调用 ID |
| `meta` | object | 否 | 元数据 |

### 4. TOOL_CALL 事件 (`event: tool_call`)

结构化工具调用事件，包含完整的工具调用信息。

```json
{
  "type": "tool_call",
  "tool_call": {
    "tool": "repo_read_head",
    "arguments": {
      "path": "src/main.py",
      "limit": 100
    },
    "call_id": "call_123"
  },
  "meta": {
    "provider": "kernelone",
    "trace_id": "ghi789",
    "streaming": true
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"tool_call"` |
| `tool_call.tool` | string | 是 | 工具名称 |
| `tool_call.arguments` | object | 是 | 工具参数 |
| `tool_call.call_id` | string | 否 | 调用 ID |
| `meta` | object | 否 | 元数据 |

### 5. TOOL_END 事件 (`event: tool_end`)

工具执行结束事件，标记工具生命周期的结束。

```json
{
  "type": "tool_end",
  "tool_call": {
    "tool": "repo_read_head",
    "call_id": "call_123"
  },
  "meta": {
    "success": true,
    "duration_ms": 150,
    "provider": "kernelone"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"tool_end"` |
| `tool_call.tool` | string | 是 | 工具名称 |
| `tool_call.call_id` | string | 否 | 调用 ID |
| `meta.success` | boolean | 是 | 是否成功 |
| `meta.error` | string | 否 | 错误信息（当 success=false 时） |
| `meta.duration_ms` | number | 否 | 执行耗时（毫秒） |

### 6. TOOL_RESULT 事件 (`event: tool_result`)

工具执行结果事件，包含工具的返回数据。

```json
{
  "type": "tool_result",
  "tool_result": {
    "tool": "repo_read_head",
    "call_id": "call_123",
    "output": "file contents..."
  },
  "meta": {}
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"tool_result"` |
| `tool_result.tool` | string | 是 | 工具名称 |
| `tool_result.call_id` | string | 否 | 调用 ID |
| `tool_result.output` | any | 是 | 工具输出 |
| `meta` | object | 否 | 元数据 |

### 7. META 事件 (`event: meta`)

元事件，用于传递非内容性信息（如 keep-alive ping 响应）。

```json
{
  "type": "meta",
  "meta": {
    "stream_end": true
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"meta"` |
| `meta` | object | 是 | 元信息 |

### 8. COMPLETE 事件 (`event: complete`)

流结束事件，标记整个流式响应的完成。

```json
{
  "type": "complete",
  "done": true,
  "meta": {
    "output": "final response text",
    "latency_ms": 1234,
    "usage": {
      "prompt_tokens": 100,
      "completion_tokens": 50
    }
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"complete"` |
| `done` | boolean | 是 | 固定值 `true` |
| `meta` | object | 否 | 最终元数据（usage, latency 等） |

### 9. ERROR 事件 (`event: error`)

错误事件，标记流式响应中的错误。

```json
{
  "type": "error",
  "error": "Provider timeout after 300s",
  "done": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"error"` |
| `error` | string | 是 | 错误信息 |
| `done` | boolean | 是 | 固定值 `true` |

---

## SSE 传输格式

### 标准格式

```
event: <event_type>\n
data: <json_payload>\n
\n
```

示例：

```
event: chunk\n
data: {"type": "chunk", "chunk": "Hello"}\n
\n
```

### 多行 data

如果 JSON 数据包含换行符，每个换行前需要加 `data: ` 前缀：

```
event: chunk\n
data: {"type": "chunk", "chunk": "Line 1"}\n
data: {"type": "chunk", "chunk": "Line 2"}\n
\n
```

### Ping 事件（keep-alive）

服务端定期发送 ping 以保持连接活跃：

```
event: ping\n
data: {}\n
\n
```

---

## 工具生命周期示例

完整的工具调用生命周期事件序列：

```
1. event: tool_start
   data: {"type": "tool_start", "tool_call": {"tool": "repo_read_head", "call_id": "c1"}};

2. event: tool_call
   data: {"type": "tool_call", "tool_call": {"tool": "repo_read_head", "call_id": "c1", "arguments": {"path": "src/..."}}};

3. event: tool_end
   data: {"type": "tool_end", "tool_call": {"tool": "repo_read_head", "call_id": "c1"}, "meta": {"success": true, "duration_ms": 42}};

4. event: tool_result
   data: {"type": "tool_result", "tool_result": {"tool": "repo_read_head", "call_id": "c1", "output": "..."}};
```

---

## 前端集成指南

### JavaScript/TypeScript 消费示例

```typescript
const eventSource = new EventSource('/v2/stream/chat', {
  headers: { 'Authorization': `Bearer ${token}` }
});

eventSource.addEventListener('chunk', (e) => {
  const data = JSON.parse(e.data);
  appendText(data.chunk);
});

eventSource.addEventListener('tool_start', (e) => {
  const data = JSON.parse(e.data);
  showToolIndicator(data.tool_call.tool, 'running');
});

eventSource.addEventListener('tool_end', (e) => {
  const data = JSON.parse(e.data);
  showToolIndicator(data.tool_call.tool, data.meta.success ? 'success' : 'error');
});

eventSource.addEventListener('complete', (e) => {
  // Stream finished
  eventSource.close();
});

eventSource.addEventListener('error', (e) => {
  const data = JSON.parse(e.data);
  showError(data.error);
});
```

---

## 实现文件

- `polaris/kernelone/llm/shared_contracts.py` - `StreamEventType` 枚举定义
- `polaris/kernelone/llm/engine/contracts.py` - `AIStreamEvent` 类定义
- `polaris/kernelone/stream/sse_streamer.py` - `EventStreamer` SSE 序列化器
- `polaris/delivery/http/routers/stream_router.py` - FastAPI SSE 端点
