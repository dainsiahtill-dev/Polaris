# ExplorationWorkflowRuntime 事件流回传 Blueprint (方案 C)

状态: Active Implementation Plan
日期: 2026-04-17
适用范围: `polaris/cells/roles/kernel/internal/exploration_workflow.py`, `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`

---

## 1. 背景与问题

当前 `tool_calling_matrix` benchmark 在新架构下存在 multi-step case (L3/L5/L7) 的观察性 gap：

- **Blueprint 铁律**: TransactionKernel 单 turn 内最多 1 个 ToolBatch，finalize 阶段发现 tool calls → `protocol panic` → `handoff_workflow`
- **Benchmark 期望**: 需要验证 multi-step 场景中的完整工具调用序列
- **现实断层**: `handoff_workflow` 后由 `ExplorationWorkflowRuntime` 内部执行，其执行过程对事件流消费者（包括 benchmark 和真实 UI 客户端）不可见

---

## 2. 设计目标

1. **去除架构退化**: 移除 `_internal_continuation` loop，恢复 TransactionKernel 的单提交点约束
2. **事件流透明化**: `ExplorationWorkflowRuntime` 的执行事件必须能流回原 `execute_stream()` 事件流
3. **保持边界纯洁**: `TransactionKernel` 和 `ExplorationWorkflowRuntime` 仍是两个独立 runtime，但共享同一个事件通道 (event channel)
4. **解决 benchmark 验证**: benchmark 可以像真实客户端一样订阅并验证完整事件流

---

## 3. 架构设计

### 3.1 事件流模型

```
Consumer (Benchmark / UI)
    ↑
    | 订阅 execute_stream()
    |
TurnTransactionController.execute_stream()
    ├── yield TurnPhaseEvent("decision_completed")
    ├── yield ToolBatchEvent(tool_1)
    ├── yield ToolBatchEvent(tool_2)
    ├── yield TurnPhaseEvent("workflow_handoff")  ← handoff_workflow
    ├── yield ToolBatchEvent(tool_3)               ← 来自 ExplorationWorkflowRuntime
    ├── yield ToolBatchEvent(tool_4)               ← 来自 ExplorationWorkflowRuntime
    ├── yield ContentChunkEvent(synthesis)
    └── yield CompletionEvent(status="handoff")
```

### 3.2 核心接口

**ExplorationWorkflowRuntime 新增:**
```python
async def execute_stream(
    self,
    decision: TurnDecision,
    turn_id: TurnId,
) -> AsyncIterator[TurnEvent]:
    """流式执行探索工作流，产出细粒度事件。"""
```

**TurnTransactionController._handle_handoff 重构:**
- stream 路径: `_handle_handoff` 改为 `AsyncIterator` 或返回可 yield 的事件序列
- 如果 `workflow_runtime` 支持 `execute_stream`，调用它并透传所有事件
- non-stream 路径: 保持现有 `execute()` 行为

### 3.3 事件类型映射

| ExplorationWorkflowRuntime 内部行为 | 产出事件 |
|---|---|
| 执行初始工具批次 | `ToolBatchEvent` (每个工具一个) |
| 继续探索执行工具 | `ToolBatchEvent` |
| 产生分析/总结文本 | `ContentChunkEvent` |
| 探索完成 | `TurnPhaseEvent("workflow_completed")` |

---

## 4. 实施步骤

### Step 1: 回滚架构退化

文件: `polaris/cells/roles/kernel/internal/turn_decision_decoder.py`
- 恢复 `decode_for_finalization` 的 protocol panic 行为
- `code` 域恢复 `FinalizeMode.NONE`
- 恢复 `has_writes → FinalizeMode.NONE`

文件: `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
- 移除 `_internal_continuation` loop
- `_execute_llm_once_finalization` 发现 `TOOL_BATCH` → `_handle_handoff` with `handoff_reason="protocol_panic_finalize_tool_reentry"`
- 恢复 `_guard_assert_no_finalization_tool_calls`
- `KernelGuard._MAX_DECISIONS` 恢复为 1（默认值）

### Step 2: ExplorationWorkflowRuntime 流式化

文件: `polaris/cells/roles/kernel/internal/exploration_workflow.py`
- 新增 `execute_stream()` 方法
- 将 `_execute_tools()` 和 `_continue_exploration()` 改造为 yield `ToolBatchEvent`
- 将 `_synthesize()` 结果包装为 `ContentChunkEvent`
- 产出 `CompletionEvent`

### Step 3: TurnTransactionController handoff 事件透传

文件: `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
- 修改 `_execute_turn_stream`:
  - `decision_kind == HANDOFF_WORKFLOW` 时直接 yield from `_handle_handoff_stream()`
  - `TOOL_BATCH` execute 后如果 result.kind == "handoff_workflow" 也 yield from `_handle_handoff_stream()`
- 新增 `_handle_handoff_stream()`:
  - 如果 `workflow_runtime` 有 `execute_stream`，调用并 yield 所有事件
  - 否则 fallback 到现有 `_handle_handoff` 行为，包装为 single CompletionEvent

### Step 4: 测试与回归

- `test_transaction_controller.py`: 更新或移除 multi-step continuation 相关测试
- `test_kernel_guard.py`: 确保协议 panic 断言正常触发
- benchmark: 验证 multi-step case 可以通过事件流验证

---

## 5. 关键决策

### 5.1 为什么不保留 `_internal_continuation`?

`_internal_continuation` 本质上是把旧 TurnEngine 的 `while True` loop 伪装在 `TransactionKernel` 内部。它违反了：
- `len(ToolBatches) <= 1`
- `hidden_continuation == 0`
- finalize 阶段 `tool_choice=none` 的铁律

方案 C 通过**将循环物理移出 TransactionKernel**（交给 ExplorationWorkflowRuntime），既保持了单提交点约束，又解决了可观测性问题。

### 5.2 ExplorationWorkflowRuntime 为什么要 yield TurnEvent?

因为 `TurnEvent` 是 stream 消费者（UI / benchmark）已经理解的标准事件类型。使用统一的事件模型：
- 不需要消费者学习新的事件类型
- benchmark 可以直接复用现有的 `_collect_stream_observation` 逻辑
- UI 可以无缝显示 ExplorationWorkflowRuntime 的进度

---

## 6. 修改文件清单

- `polaris/cells/roles/kernel/internal/turn_decision_decoder.py` — 恢复 protocol panic
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` — 移除退化 loop，增加 handoff stream 透传
- `polaris/cells/roles/kernel/internal/exploration_workflow.py` — 新增 execute_stream
- `polaris/cells/roles/kernel/tests/test_transaction_controller.py` — 测试适配
- `polaris/cells/roles/kernel/tests/test_kernel_guard.py` — 验证协议 panic

---

## 7. 验证门禁

1. `test_transaction_controller.py` 全绿
2. `test_kernel_guard.py` 全绿
3. benchmark 能完整跑完并输出结果
4. `ruff check` / `mypy` 通过
