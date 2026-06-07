# Blueprint: Textual Tool-Call Compatibility for Non-Function-Calling Models (2026-06-08)

## 1. 现象 (Symptom)

`agentic-eval --suite tool_calling_matrix` 针对本地 `openai_compat` 绑定
(gemma-4-12B @ `http://127.0.0.1:8000/v1`) 运行时，**全部用例 0 个工具调用**：

```
observed_tools=none tool_calls=0
output_preview=<|tool_call>call:repo_read_head{count:50,file:<|"|>src/utils/helpers.py<|"|>}<tool_call|>
```

## 2. 根因 (Root Cause)

在裸 HTTP 层验证（正确的 OpenAI `tools` + `tool_choice:auto` 请求）：

```
content:    "<|tool_call>call:repo_read_head{file:<|"|>src/utils/helpers.py<|"|>,n:50}<tool_call|>"
tool_calls: null
finish:     stop
```

- **Polaris 行为正确**：原生发送 14 个 OpenAI 函数 schema、`tool_choice:auto`，
  并通过 `StreamExecutor` 解码原生 `delta.tool_calls`。
- **模型/推理服务端不支持原生 function-calling**：把 Gemma 的文本工具调用语法
  `<|tool_call>call:NAME{args}<tool_call|>`（`<|"|>` 为引号分隔符）当作普通
  `content` 文本返回，`tool_calls` 始终为 `null`。

因此这是 **模型能力 / 推理服务端** 层面的非合规输出，不是 Polaris 源码缺陷。
但需求是：**对不支持 function-calling 的模型提供兼容方案**，使其也能跑通工具调用矩阵，
同时 **不影响** 原生 FC 模型（deepseek / kimi）。

## 3. 设计 (Design)

### 3.1 文本工具调用恢复解析器 (Recovery Parser)

新增 `polaris/kernelone/llm/toolkit/parsers/textual_tool_recovery.py`：

- `recover_textual_tool_calls(text, allowed_tool_names=None) -> list[RecoveredToolCall]`
  - 识别 Gemma 文本格式 `<|tool_call>call:NAME{k:v,...}<tool_call|>`（闭合标记可选）；
    值支持 `<|"|>...<|"|>` 字符串与裸数字/布尔；支持单条/多条。
  - 当提供 `allowed_tool_names` 时，仅恢复名字在白名单内的调用（防误报/幻觉）。
  - 返回规范结构 `{"tool", "arguments", "call_id"}`。
- `strip_textual_tool_call_markers(text) -> str`：从可见文本中剥离已恢复的工具调用片段。

纯函数、无副作用、完整类型注解、单测覆盖正常/边界/异常。

### 3.2 接入点 (Integration) —— 仅在"无原生工具调用"时兜底

**Stream 路径**：`StreamExecutor.invoke_stream`
(`polaris/kernelone/llm/engine/stream/executor.py`) 结构化流循环结束后、
`AIStreamEvent.complete` 之前：

```
if not emitted_tool_calls:
    recovered = recover_textual_tool_calls(collected_output, allowed_tool_names)
    for rc in recovered:
        emitted_tool_calls.append(rc); yield AIStreamEvent.tool_call_event(rc, ...)
    if recovered:
        collected_output = strip_textual_tool_call_markers(collected_output)
```

`allowed_tool_names` 来自 `invoke_cfg["tools"]`（请求实际下发的工具集）。

**Non-stream 路径**：在角色内核解析非流式响应工具调用处，同样在
"原生 `tool_calls` 为空" 时调用恢复解析器补齐。

### 3.3 门禁 / 安全 (Gating)

兜底**只**在以下全部成立时触发，确保对原生 FC 模型零影响：

1. 本次响应 **没有** 原生工具调用（`emitted_tool_calls` 为空）。
2. 请求实际下发了工具（`invoke_cfg["tools"]` 非空）。
3. 恢复出的工具名 ∈ 已下发工具集。

deepseek / kimi 等返回原生 `tool_calls` → 条件 1 不成立 → 兜底不触发。

## 4. 数据流 (Data Flow)

```
provider.invoke_stream_events (原始 OpenAI SSE: content=<|tool_call>...)
  → StreamExecutor._invoke_structured_stream (decode → AssistantMessage→chunk, tool_calls=[])
  → StreamExecutor.invoke_stream 累积 collected_output / emitted_tool_calls(空)
  → [NEW] 恢复解析: collected_output → tool_call 事件 + emitted_tool_calls
  → AIStreamEvent.complete{ tool_calls }
  → StreamEngine.run_stream → 规范 tool_call 事件 → 矩阵 _collect_stream_observation 计数
```

## 5. 验证 (Verification)

1. 解析器单测（fail-on-baseline）。
2. `ruff check --fix` / `ruff format` / `mypy` / `pytest`（最小改动面）。
3. CLI：`agentic-eval --suite tool_calling_matrix`（gemma）分数尽量满分/全通过。
4. CLI：同矩阵跑 deepseek / kimi（原生 FC），验证兜底不触发且通过。

## 6. 范围与边界 (Risks & Boundaries)

- 不修改角色内核 turn 语义、不复活已废弃的"提示词文本工具协议"（role 层 `[TOOL_CALL]`）。
  本方案是 **provider/解码层** 对非合规服务端输出的兼容恢复，门禁严格、默认不触发。
- 嵌套花括号参数不在 v1 范围（实测 Gemma 输出为扁平参数）。
- 不在 Polaris 主仓加入任何业务/目标项目代码（§8）。
