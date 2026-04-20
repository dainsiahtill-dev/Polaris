---
status: accepted
date: 2026-04-17
---

# ADR-0078: Development Workflow Handoff in TurnTransactionController

## 背景

`TurnTransactionController` 已经实现了严格的单决策约束（Single-Decision Constraint）：一个 turn 内只能产生一个 `TurnDecision`，且最多执行一次 `ToolBatch`。对于需要多步探索的复杂任务，系统通过 `HANDOFF_WORKFLOW` 将控制权移交给 `ExplorationWorkflowRuntime`。

然而，代码开发场景具有明显不同的工作流特征：
- 它不是开放式的“探索”，而是目标明确的 `read → write → test` 闭环。
- 它需要反复应用补丁、运行测试、分析失败日志、再次修复，直到测试通过或达到最大重试次数。
- 它可以直接调用 `tool_executor`，不需要每轮都经过 LLM 决策。
- 它与 `StreamShadowEngine` 的推测补丁消费高度耦合（零延迟 patch 应用）。

因此，我们需要一种与 `ExplorationWorkflowRuntime` **平级但语义不同**的 handoff 目标。

## 决策

### 1. 新增 TurnDecisionKind.HANDOFF_DEVELOPMENT

在 `polaris/cells/roles/kernel/public/turn_contracts.py` 中扩展 `TurnDecisionKind`：

```python
class TurnDecisionKind(str, Enum):
    FINAL_ANSWER = "final_answer"
    TOOL_BATCH = "tool_batch"
    ASK_USER = "ask_user"
    HANDOFF_WORKFLOW = "handoff_workflow"
    HANDOFF_DEVELOPMENT = "handoff_development"
```

当 `TurnTransactionController` 解码到 `HANDOFF_DEVELOPMENT` 时，不再路由到 `ExplorationWorkflowRuntime`，而是路由到 `DevelopmentWorkflowRuntime`。

### 2. DevelopmentWorkflowRuntime 不继承 ExplorationWorkflowRuntime

两者是**同级 handoff 目标**，职责正交：

| 维度 | ExplorationWorkflowRuntime | DevelopmentWorkflowRuntime |
|------|---------------------------|---------------------------|
| 核心模式 | read → explore → synthesize | read → write → test → retry |
| 是否需要 LLM 决策 | 是（每步可能请求 LLM） | 否（直接调用 tool_executor） |
| 与 ShadowEngine 的关系 | 预热只读工具 | 消费推测好的 patch |
| 输出产物 | discoveries / synthesis | patch result / test result |
| 典型触发 | 工具数量过多、异步 pending | 测试失败、需要自动修复代码 |

继承会导致不必要的耦合和命名空间污染。两者各自独立实现，统一通过 `TurnTransactionController` 的 handoff 机制被调用。

### 3. 内核零修改原则

`TurnTransactionController` 本身的单决策循环逻辑**不做任何修改**。新增的仅是在决策解码后的路由分支：

```python
elif decision_kind == TurnDecisionKind.HANDOFF_DEVELOPMENT:
    return await self._handle_development_handoff(decision, state_machine, ledger)
```

以及流式路径中的事件透传：

```python
elif decision_kind == TurnDecisionKind.HANDOFF_DEVELOPMENT:
    async for event in self._handle_development_handoff_stream(decision, state_machine, ledger):
        yield event
    return
```

所有 handoff 上下文构建、状态机转换、事件流封装都在新增的两个方法中完成。

### 4. TurnStateMachine 扩展 HANDOFF_DEVELOPMENT 状态

在 `turn_state_machine.py` 的 `_VALID_TRANSITIONS` 中：

- `DECISION_DECODED` 可以转入 `HANDOFF_DEVELOPMENT`
- `TOOL_BATCH_EXECUTED` 可以转入 `HANDOFF_DEVELOPMENT`（例如测试失败后内核决定 handoff）
- `FINALIZATION_REQUESTED` 可以转入 `HANDOFF_DEVELOPMENT`
- `HANDOFF_DEVELOPMENT` 只能转出到 `COMPLETED`

这保证了 handoff 完成后当前 turn 立即结束，不会触发 continuation loop。

### 5. 流式事件透传

`_handle_development_handoff_stream()` 将 `DevelopmentWorkflowRuntime.execute_stream()` 产生的事件原样透传给上游消费者。事件序列：

```
TurnPhaseEvent(phase="workflow_handoff")
RuntimeStartedEvent(name="DevelopmentWorkflow")
TurnPhaseEvent(phase="tool_batch_started", metadata={"development_phase": "patching_code"})
ToolBatchEvent(tool_name="apply_patch", status="success")
TurnPhaseEvent(phase="tool_batch_started", metadata={"development_phase": "running_tests"})
ToolBatchEvent(tool_name="run_tests", status="success|error")
ContentChunkEvent(chunk="...")
RuntimeCompletedEvent()
CompletionEvent(status="handoff")
```

### 6. Orchestrator 层包装：TurnOutcomeEnvelope

由于 `TurnResult` 是内核契约，不能破坏性修改，Orchestrator 层使用新引入的 `TurnOutcomeEnvelope` 包装 `TurnResult`，并附加继续执行所需的元数据：

```python
class TurnOutcomeEnvelope(BaseModel):
    turn_result: TurnResult
    continuation_mode: TurnContinuationMode
    next_intent: str | None = None
    session_patch: dict[str, Any] = Field(default_factory=dict)
    artifacts_to_persist: list[dict[str, Any]] = Field(default_factory=list)
    speculative_hints: dict[str, Any] = Field(default_factory=dict)
```

这样 `RoleSessionOrchestrator` 可以在不修改内核的情况下，基于 `CompletionEvent` 推断出是否需要 `HANDOFF_DEVELOPMENT`。

## 影响

### 修改的文件

1. `polaris/cells/roles/kernel/public/turn_contracts.py` — 新增 `HANDOFF_DEVELOPMENT` 和 `TurnOutcomeEnvelope`
2. `polaris/cells/roles/kernel/public/turn_events.py` — 新增 orchestrator/runtime 层事件
3. `polaris/cells/roles/kernel/internal/turn_state_machine.py` — 扩展状态转移规则
4. `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` — 新增 `development_runtime` 参数与 handoff 处理方法
5. `polaris/cells/roles/kernel/internal/development_workflow_runtime.py` — 新增（kernel 侧 TDD 运行时）
6. `polaris/cells/roles/runtime/internal/session_orchestrator.py` — 新增（Orchestrator 层入口）

### 向后兼容性

- `TurnDecisionKind` 新增枚举值不会影响旧代码，因为旧代码只处理前四个值。
- `TurnTransactionController.__init__` 新增的 `development_runtime` 参数有默认值 `None`，不影响现有实例化方式。
- 当 `development_runtime` 为 `None` 时，`_handle_development_handoff()` 仍然正常完成并返回 `handoff_development` 结果，只是不会实际执行开发工作流（graceful fallback）。

### 测试要求

- 新增 `test_transaction_controller_development_handoff.py`：覆盖非流式 handoff、流式 handoff、无 runtime fallback、runtime error 容错。
- 新增 `test_development_workflow_runtime.py`：覆盖 TDD 闭环、ShadowEngine patch 消费、重试机制。
- 新增 `test_session_orchestrator.py`：覆盖多 turn 编排、handoff 路由、ContinuationPolicy 仲裁。

## 相关文档

- `docs/blueprints/SESSION_ORCHESTRATOR_AND_DEVELOPMENT_WORKFLOW_RUNTIME_BLUEPRINT_20260417.md`
- `docs/governance/templates/verification-cards/vc-20260417-session-orchestrator-and-development-workflow.yaml`
- `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`
