# Polaris 后端事件推送增强方案

## 背景

当前前端 UI 实时感知不足，用户只能看到 "正在请求 generic..."，但看不到任务编排的详细过程。

**根因**：后端发送的事件不够详细，缺少任务编排各步骤的过程事件。

## 目标

在任务编排的每个关键步骤发送详细事件，让前端能够实时显示：
- 开始规划
- 任务生成
- 任务验证
- LLM 调用
- 工具执行
- 结果返回

## 当前已有事件机制

### 后端事件发送函数

```python
# src/backend/core/polaris_loop/io_utils.py
def emit_event(event_path: str, ...): ...
def emit_llm_event(llm_events_path: str, ...): ...
```

### 当前发送的事件类型

| Channel | 用途 |
|---------|------|
| pm_llm | PM LLM 调用 |
| director_llm | Director LLM 调用 |
| pm_subprocess | PM 子进程输出 |
| director_console | Director 控制台输出 |
| runtime_events | 运行时事件 |
| dialogue | 对话事件 |

## 增强方案

### 1. PM 规划阶段增强

在 `src/backend/scripts/pm/orchestration_engine.py` 中添加详细事件：

```python
# 当前代码
emit_event(run_events, "planning_iteration", {...})

# 增强后 - 添加更多步骤事件
emit_event(run_events, "planning_start", {
    "iteration": iteration,
    "workspace": workspace,
    "timestamp": datetime.now().isoformat()
})

emit_event(run_events, "task_generation_start", {
    "iteration": iteration,
    "goal": goal,
    "timestamp": datetime.now().isoformat()
})

emit_event(run_events, "task_generation_complete", {
    "iteration": iteration,
    "task_count": len(tasks),
    "tasks": [{"id": t.id, "title": t.title} for t in tasks],
    "timestamp": datetime.now().isoformat()
})

emit_event(run_events, "quality_gate_start", {
    "iteration": iteration,
    "attempt": attempt,
    "timestamp": datetime.now().isoformat()
})

emit_event(run_events, "quality_gate_complete", {
    "iteration": iteration,
    "score": score,
    "passed": passed,
    "issues": issues,
    "timestamp": datetime.now().isoformat()
})
```

### 2. Director 执行阶段增强

在 `src/backend/core/polaris_loop/director_tooling/executor_core.py` 中添加：

```python
emit_event(events_path, "director_task_start", {
    "task_id": task.id,
    "task_title": task.title,
    "timestamp": datetime.now().isoformat()
})

emit_event(events_path, "tool_invocation", {
    "task_id": task.id,
    "tool_name": tool_name,
    "tool_input": sanitize_tool_input(tool_input),
    "timestamp": datetime.now().isoformat()
})

emit_event(events_path, "tool_completion", {
    "task_id": task.id,
    "tool_name": tool_name,
    "duration_ms": duration_ms,
    "success": success,
    "timestamp": datetime.now().isoformat()
})

emit_event(events_path, "director_task_complete", {
    "task_id": task.id,
    "status": "completed" | "failed" | "blocked",
    "output": output_summary,
    "timestamp": datetime.now().isoformat()
})
```

### 3. LLM 调用阶段增强

在 LLM 调用前后添加事件：

```python
# src/backend/core/polaris_loop/io_utils.py 或具体调用位置

emit_llm_event(llm_events_path, "invoke_start", {
    "backend": backend_name,
    "model": model_name,
    "prompt_length": len(prompt),
    "timestamp": datetime.now().isoformat()
})

emit_llm_event(llm_events_path, "invoke_tokens", {
    "prompt_tokens": prompt_tokens,
    "completion_tokens": completion_tokens,
    "total_tokens": total_tokens,
    "timestamp": datetime.now().isoformat()
})

emit_llm_event(llm_events_path, "invoke_complete", {
    "duration_ms": duration_ms,
    "output_length": len(output),
    "timestamp": datetime.now().isoformat()
})

emit_llm_event(llm_events_path, "invoke_error", {
    "error": error_message,
    "duration_ms": duration_ms,
    "timestamp": datetime.now().isoformat()
})
```

## 实施计划

### 阶段1：PM 规划阶段事件增强

1. 修改 `src/backend/scripts/pm/orchestration_engine.py`
2. 在每个关键步骤添加 `emit_event` 调用
3. 确保事件包含足够的上下文信息

### 阶段2：Director 执行阶段事件增强

1. 修改 `src/backend/core/polaris_loop/director_tooling/executor_core.py`
2. 在任务开始、工具调用、任务完成时发送事件

### 阶段3：LLM 调用事件增强

1. 在 LLM 调用封装层添加事件
2. 确保所有 LLM 调用都有对应的事件

### 阶段4：前端适配

1. 确认前端能正确解析新事件格式
2. 在 LlmRuntimeOverlay 中显示更多信息

## 事件格式规范

```typescript
interface RealtimeEvent {
  event: string;          // 事件类型
  timestamp: string;      // ISO 格式时间戳
  actor: string;          // PM | Director | QA
  data: {
    // 事件具体数据
  };
}
```

## 需要修改的文件

1. `src/backend/scripts/pm/orchestration_engine.py`
2. `src/backend/core/polaris_loop/director_tooling/executor_core.py`
3. `src/backend/core/polaris_loop/io_utils.py`
4. `src/backend/scripts/pm/backend.py` (PM LLM 事件)

## 验收标准

- [ ] PM 规划阶段：能看到任务生成、质量检查等详细步骤
- [ ] Director 执行阶段：能看到每个任务的开始、工具调用、完成
- [ ] LLM 调用阶段：能看到调用开始、token使用、完成/失败
- [ ] 前端 LlmRuntimeOverlay 能正确显示新事件
