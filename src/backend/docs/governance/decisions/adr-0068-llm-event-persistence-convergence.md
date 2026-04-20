# ADR-0068: LLM 事件落盘收敛

**状态**: 已接受
**日期**: 2026-03-29
**决策者**: Python 架构与代码治理实验室

---

## 背景

项目存在多套 LLM 事件落盘逻辑，导致：

1. **`kernel.py`** 中的 `_emit_llm_event_to_disk()` 方法
2. **`events.py`** 中的 `emit_llm_event()` 函数（原本只发到内存和 realtime bridge）
3. **`llm_caller.py`** 调用 `emit_llm_event()` 但不直接落盘

这种分散导致：
- 落盘路径不一致
- benchmark 运行后看不到 LLM 事件落盘
- 难以通过 run_id 做交叉校验审计

## 决策

**收敛为单一落盘入口**：`events.py` 的 `emit_llm_event()` 函数。

### 调用链路

```
llm_caller.py ──→ emit_llm_event() ──→ get_global_emitter().emit()  (内存)
                         │
                         ├──→ _publish_to_realtime_bridge()  (实时推送)
                         │
                         └──→ _emit_llm_event_to_disk()  (落盘)
                                    │
                                    └──→ kernel_io_events.emit_llm_event()
                                                │
                                                └──→ runtime/events/{role}.llm.events.jsonl
```

### 落盘路径

```
{runtime_root}/events/{role}.llm.events.jsonl
```

示例：
- `X:\.polaris\projects\benchmarktest-279a449b23f2\runtime\events\director.llm.events.jsonl`
- `{workspace}/.polaris/projects/{workspace_key}/runtime/events/director.llm.events.jsonl`

### 事件格式

```json
{
  "schema_version": 1,
  "ts": "2026-03-29T12:00:00.000Z",
  "ts_epoch": 1743254400.0,
  "seq": 1,
  "event_id": "abc123",
  "run_id": "6d7f127b",
  "iteration": 0,
  "role": "director",
  "source": "roles.kernel.events",
  "event": "llm_call_start",
  "data": {
    "event_type": "llm_call_start",
    "role": "director",
    "run_id": "6d7f127b",
    "task_id": "task-001",
    "attempt": 0,
    "model": "claude-3-opus",
    "prompt_tokens": 1500,
    "metadata": {
      "call_id": "abc12345",
      "workspace": "/path/to/workspace",
      "messages": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."}
      ],
      "temperature": 0.7,
      "max_tokens": 4000,
      "native_tool_mode": "enabled",
      "response_format_mode": "plain_text"
    }
  }
}
```

### 事件类型

| 事件类型 | 说明 | 关键字段 |
|---------|------|---------|
| `llm_call_start` | LLM 调用开始 | `messages`, `model`, `temperature`, `max_tokens` |
| `llm_call_end` | LLM 调用结束 | `response_content`, `completion_tokens`, `elapsed_ms` |
| `llm_retry` | LLM 重试 | `retry_reason`, `attempt` |
| `llm_error` | LLM 错误 | `error_category`, `error_message` |

## 修改的文件

| 文件 | 修改内容 |
|------|---------|
| `polaris/cells/roles/kernel/internal/events.py` | 添加 `_emit_llm_event_to_disk()` 函数，在 `emit_llm_event()` 末尾调用 |
| `polaris/cells/roles/kernel/internal/kernel.py` | 移除 `_emit_llm_event_to_disk()` 方法，避免重复落盘 |
| `polaris/cells/roles/kernel/internal/llm_caller.py` | `_emit_call_start_event()` 添加 `messages` 参数；`_emit_call_end_event()` 添加 `response_content` 参数 |

## 后果

### 正面

- 单一落盘入口，易于维护
- 所有 LLM 调用都有完整审计轨迹
- 支持通过 `run_id` 做交叉校验
- 完整的请求/返回内容可用于审计

### 负面

- 每次调用都会写磁盘，有轻微性能开销
- 大型对话的 `messages` 可能较大

### 风险缓解

- 落盘失败不阻塞主流程（try-except 包裹）
- 使用 JSONL 格式，支持增量追加

## 审计查询示例

```bash
# 查询特定 run_id 的所有 LLM 事件
python -m polaris.delivery.cli audit_quick triage -r 6d7f127b

# 直接读取事件文件
cat runtime/events/director.llm.events.jsonl | jq 'select(.run_id == "6d7f127b")'
```

## 参考

- `polaris/kernelone/events/io_events.py` - 底层事件写入
- `polaris/kernelone/storage/layout.py` - 路径解析
- `polaris/cells/roles/kernel/internal/llm_caller.py` - LLM 调用器