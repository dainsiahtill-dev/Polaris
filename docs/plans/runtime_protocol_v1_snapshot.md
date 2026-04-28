# Runtime Protocol V1 契约快照

本文档记录了当前 Polaris Runtime WebSocket/SSE 的协议契约，作为迁移到 v2 前的基线对比输入。

## 1. WebSocket 端点

### 1.1 `/v2/ws/runtime`

**基础信息**:
- 协议: WebSocket
- 路径: `/v2/ws/runtime`
- 查询参数:
  - `roles`: 可选，逗号分隔的角色过滤 (pm, director, qa)
  - `workspace`: 可选，workspace 路径覆盖
  - `token`: 认证 token

**当前消息类型**:

#### 客户端 -> 服务端

| 类型 | 说明 | 样例 |
|------|------|------|
| SUBSCRIBE | 订阅频道/角色 | `{"type": "SUBSCRIBE", "channels": ["status", "log.llm"], "roles": ["pm", "director"]}` |
| UNSUBSCRIBE | 取消订阅 | `{"type": "UNSUBSCRIBE", "channels": ["log.llm"]}` |
| STATUS / GET_STATUS | 请求状态快照 | `{"type": "STATUS"}` |
| GET_SNAPSHOT / SNAPSHOT | 请求全量快照 | `{"type": "GET_SNAPSHOT"}` |
| PING | 心跳 | `{"type": "PING"}` |
| EVENT | 事件查询 | `{"type": "EVENT", "action": "query", "channel": "log.llm", ...}` |

#### 服务端 -> 客户端

| 类型 | 说明 | 样例 |
|------|------|------|
| dialogue_event | 对话事件 | `{"type": "dialogue_event", "channel": "dialogue", "event": {...}, "snapshot": false}` |
| runtime_event | 运行时事件 | `{"type": "runtime_event", "channel": "runtime_events", "event": {...}, "snapshot": false}` |
| llm_stream | LLM 流式输出 | `{"type": "llm_stream", "channel": "llm", "line": "...", "snapshot": false}` |
| process_stream | 进程流式输出 | `{"type": "process_stream", "channel": "process", "line": "...", "snapshot": false}` |
| file_edit | 文件编辑事件 | `{"type": "file_edit", "event": {...}, "timestamp": "..."}` |
| snapshot | 历史快照 (legacy) | `{"type": "snapshot", "channel": "...", "lines": [...]}` |
| line | 单行推送 (legacy) | `{"type": "line", "channel": "...", "text": "..."}` |
| SUBSCRIBED | 订阅确认 | `{"type": "SUBSCRIBED", "payload": {"roles": [...], "channels": [...]}}` |
| UNSUBSCRIBED | 取消订阅确认 | `{"type": "UNSUBSCRIBED", "payload": {"channels": [...]}}` |
| PONG | 心跳响应 | `{"type": "PONG"}` |
| PING | 心跳保活 | `{"type": "PING"}` |
| docs_wizard_status | Docs Wizard Status | `{"type": "docs_wizard_status", "court_state": {...}}` |
| ERROR | 错误 | `{"type": "ERROR", "payload": {"error": "..."}}` |

### 1.2 状态快照结构

```json
{
  "type": "status",
  "pm_status": {...},
  "director_status": {...},
  "court_state": {...},
  "snapshot_time": "..."
}
```

## 2. SSE 端点

### 2.1 Factory SSE

**路径**: `/factory/sse`

### 2.2 Docs SSE

**路径**: `/docs/sse`

### 2.3 Interview SSE

**路径**: `/interview/sse`

### 2.4 Role Session SSE

**路径**: `/v2/role/{role}/sse`

**当前数据格式**:
- 纯文本行推送
- 无 cursor 机制
- 无断线恢复能力

## 3. 健康检查

### 3.1 `/ready` 端点

```json
{
  "ready": true,
  "checks": {
    "api": "ok",
    "storage": "ok"
  }
}
```

### 3.2 `/health` 端点

```json
{
  "status": "ok",
  "service": "polaris-backend",
  "version": "2.0.0"
}
```

## 4. 当前实现依赖

### 4.1 实时链路组件

1. **RuntimeEventFanout** (`app/services/runtime_event_fanout.py`)
   - 负责 FILE_WRITTEN 事件分发
   - 内存队列 + 连接注册

2. **RealtimeHub** (`app/services/realtime_hub.py`)
   - 文件系统监控信号
   - 连接状态管理

3. **LogRealtimeFanout** (`core/polaris_loop/log_pipeline/realtime.py`)
   - 日志实时推送
   - 队列订阅模式

### 4.2 消息类型定义

从 `application/message_bus.py`:

```python
class MessageType(Enum):
    FILE_WRITTEN = "file_written"
    TASK_TRACE = "task_trace"
    DIRECTOR_STATUS = "director_status"
    PM_STATUS = "pm_status"
    RUNTIME_STATUS = "runtime_status"
    # ... more
```

## 5. 迁移到 V2 的变更点

### 5.1 协议变更

| 项目 | V1 | V2 |
|------|-----|-----|
| 消息包装 | 多类型混合 | 统一 EVENT 包装 |
| Cursor | 无 | JetStream sequence |
| ACK | 无 | 显式 ACK |
| 断线恢复 | 无 | 通过 cursor 恢复 |
| 去重 | 内存签名 | event_id 去重 |

### 5.2 架构变更

| 组件 | V1 | V2 |
|------|-----|-----|
| 事件分发 | RuntimeEventFanout (内存) | JetStream (持久化) |
| 日志推送 | LogRealtimeFanout (内存队列) | JetStream 消费 |
| 状态推送 | 文件轮询 + 信号 | JetStream 推送 |
| SSE | 路由直推 | NATS 消费者桥接 |

## 6. 测试基线

### 6.1 现有测试

- `test_runtime_ws_migration.py`
- `test_websocket_architecture_integration.py`
- `test_log_pipeline_storage_layout.py`

### 6.2 预期新增测试

- `test_nats_client.py`
- `test_jetstream_bridge_ws.py`
- `test_runtime_ws_protocol_v2.py`
- `test_history_archive_service.py`
- `test_storage_migration_v2.py`

---

**快照时间**: 2026-03-12
**版本**: v1.0 (baseline)
