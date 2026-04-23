# 认知生命体与工程架构对齐备忘录

**文档编号**: MEMO-2026-0417-COGNITIVE-ALIGNMENT
**日期**: 2026-04-17
**状态**: 已定稿，可直接写入 AGENTS.md / ADR
**适用范围**: `src/backend/polaris/cells/roles/*` 及所有依赖事务内核与编排层的模块

> **工程注释**：本文档使用生物学隐喻作为记忆辅助。
> 所有隐喻均可在 [TERMINOLOGY.md](../TERMINOLOGY.md) 中找到对应的工程实体。
> 代码实现中使用的是工程实体名称，而非隐喻。

---

## 1. 目的

消除"认知生命体（Cognitive Lifeform）"、"认知运行时（Cognitive Runtime）"等顶层哲学概念与当前落地的工程代码之间可能出现的理解歧义。本文档提供**一一对应的映射关系**，并标注**真实代码路径**与**关键代码片段**，确保后续开发者不会在"抽象愿景"与"具体实现"之间产生脱节。

---

## 2. 核心命题

> **"认知生命体"与"认知运行时"是这套工程架构的灵魂与哲学顶层；**
> **当前工程架构（`RoleSessionOrchestrator` + `TurnTransactionController` + `DevelopmentWorkflowRuntime` + `StreamShadowEngine`）是灵魂唯一可运行、可观测、可进化的实体化落地形态。**

两者是**上下层映射关系**，不是平行关系，更不是冲突关系。

---

## 3. 详细映射表（概念 ↔ 代码实体）

| 抽象概念 | 工程实体（代码基线） | 生物学类比 | 关键代码片段（已确认） |
|---------|-------------------|-----------|---------------------|
| **认知生命体** | `OrchestratorSessionState` + `SessionArtifactStore` | 躯体 + 海马体 + 自我意识（持久身份、记忆、目标） | `self.state = OrchestratorSessionState(...)` 与 `_checkpoint_session()` |
| **主控意识** | `RoleSessionOrchestrator.execute_stream()` 中的 `while True` 循环 | 前额叶皮层（裁决"此刻该做什么"） | `can_continue, reason = self.policy.can_continue(...)` |
| **心脏 / 单次神经放电** | `TurnTransactionController` + `KernelGuard` | 不可逆的单次思考-行动循环 | `CONTEXT_BUILT → DECISION_DECODED → TOOL_BATCH_EXECUTED → COMPLETED` |
| **肌肉记忆 / 潜意识** | `DevelopmentWorkflowRuntime` | 小脑（自动执行 `read→write→test` 闭环） | `for attempt in range(self.max_retries): _execute_patch() → _run_tests()` |
| **潜意识加速器 / 直觉预感** | `StreamShadowEngine`（跨 Turn 推测 + Patch 缓存） | 神经预激（让"思考"与"行动"时间重叠） | `start_cross_turn_speculation()` + `consume_speculation()` |
| **物理法则 / 生存约束** | `ContinuationPolicy` + `KernelGuard` + ShadowEngine CancellationToken | 防止死循环、资源泄漏、幻觉的理智守护者 | `max_auto_turns` + `_detect_stagnation_v2()` + `_guard_assert_single_decision()` |
| **脑电图 / 对外表达** | `TurnEvent` 流（`TurnPhaseEvent`, `ToolBatchEvent`, `CompletionEvent`） | 实时向人类/UI 暴露内心活动 | `yield` 事件链（Orchestrator 透传 + Runtime 补充） |

---

## 4. 代码级执行流程（生命体的一次"苏醒-思考-行动"周期）

### 4.1 苏醒与记忆固化
**文件**: `polaris/cells/roles/runtime/internal/session_orchestrator.py:58-84`

```python
class RoleSessionOrchestrator:
    def __init__(self, session_id: str, kernel: Any, workspace: str, ...):
        self.state = OrchestratorSessionState(
            session_id=session_id,
            goal="",
            turn_count=0,
            max_turns=max_auto_turns,
            artifacts={},
        )
        self._artifact_store = SessionArtifactStore(
            workspace=workspace,
            session_id=session_id,
        )
```

- `OrchestratorSessionState` 维护 `goal`（生命体目标）、`turn_count`（年龄/阅历）、`artifacts`（积累的知识财富）。
- `SessionArtifactStore`（`polaris/cells/roles/runtime/internal/session_artifact_store.py`）负责将 `artifacts` 增量持久化到磁盘，支持 `diff` 去重和 `compress_if_changed` 触发。
- `_checkpoint_session()` 每次 Turn 前执行，相当于"记忆固化"，防止进程崩溃导致失忆。

### 4.2 主控意识的时间流逝
**文件**: `polaris/cells/roles/runtime/internal/session_orchestrator.py:85-209`

```python
async def execute_stream(self, prompt: str, *, context: dict | None = None):
    yield SessionStartedEvent(session_id=self.session_id)
    while True:
        await self._checkpoint_session()          # 记忆固化

        # 1. 心脏跳动一次（单次干净 Turn）
        async for event in self.kernel.execute_stream(...):
            yield event

        self.state.turn_count += 1

        # 2. 主意识裁决：继续、求助、还是 Handoff？
        can_continue, reason = self.policy.can_continue(self.state, envelope)
        if not can_continue:
            yield SessionCompletedEvent(...)
            break
```

- `while True` 循环 = 认知运行时的"主时钟"。
- 每一次迭代 = 生命体"活过了一个瞬间"。
- `ContinuationPolicy.can_continue()` = 前额叶皮层的高级裁决：判断是否陷入死循环、是否重复失败、是否 stagnation。

### 4.3 单次神经放电（心脏跳动）
**文件**: `polaris/cells/roles/kernel/internal/turn_transaction_controller.py:518-594`

```python
async def _execute_turn(self, turn_id: str, context: list[dict], ...):
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    llm_response = await self._call_llm_for_decision(context, tool_definitions, ledger)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)

    decision = self.decoder.decode(llm_response, TurnId(turn_id))
    self._guard_assert_single_decision(...)
    state_machine.transition_to(TurnState.DECISION_DECODED)

    # 分支：直接回答 / 工具调用 / Handoff
    if decision_kind == TurnDecisionKind.TOOL_BATCH:
        return await self._execute_tool_batch(...)
```

- `TurnTransactionController` 严禁 continuation loop。
- `KernelGuard` 强制约束：
  - `assert_single_decision`：每个 Turn 只能有 **1 个**决策。
  - `assert_single_tool_batch`：每个 Turn 最多 **1 个**工具批次。
  - `assert_no_hidden_continuation`：禁止状态轨迹中出现非法循环。
- 这是为了防止模型在同一个 prompt 里无限自我对话，导致**精神分裂症**与**Token 爆仓脑死亡**。

### 4.4 主意识 → 潜意识 Handoff
**文件**: `polaris/cells/roles/runtime/internal/session_orchestrator.py:161-176`

```python
if envelope.continuation_mode == TurnContinuationMode.HANDOFF_DEVELOPMENT:
    yield TurnPhaseEvent.create(..., phase="workflow_handoff", ...)
    runtime = DevelopmentWorkflowRuntime(
        tool_executor=self.kernel.tool_runtime,
        shadow_engine=self._shadow_engine,
    )
    async for dev_event in runtime.execute_stream(
        intent=envelope.next_intent or "",
        session_state=self.state,
    ):
        yield dev_event
    break
```

- 主意识产生高级意图（"修复这个 bug"），然后将机械劳动交给潜意识 `DevelopmentWorkflowRuntime`。
- `DevelopmentWorkflowRuntime` 执行 `read → write → test` 的 TDD 闭环（`max_retries` 次自我修复）。
- 主意识无需消耗宝贵 token 去关注每一次语法错误，Token 效率大幅提升。

### 4.5 潜意识预感（ShadowEngine 跨 Turn 推测）
**文件**: `polaris/cells/roles/runtime/internal/session_orchestrator.py:109-119` 与 `polaris/cells/roles/kernel/internal/stream_shadow_engine.py:138-170`

```python
# Orchestrator 侧：消费推测结果
if self._shadow_engine.has_valid_speculation(self.session_id):
    pre_warmed = await self._shadow_engine.consume_speculation(self.session_id)
    async for event in self._yield_pre_warmed_events(pre_warmed):
        yield event

# Orchestrator 侧：触发下一 Turn 的推测预热
if self._shadow_engine and can_continue:
    start_cross_turn = getattr(self._shadow_engine, "start_cross_turn_speculation", None)
    if callable(start_cross_turn):
        start_cross_turn(
            session_id=self.session_id,
            predicted_next_tools=self._predict_next_tools(envelope),
            hints=envelope.speculative_hints,
        )
```

- `StreamShadowEngine` 在**当前 Turn 还在执行时**，就基于 hints 预测下一 Turn 可能需要的工具调用，并提前执行。
- 下一 Turn 开始时，直接 `consume_speculation()`，实现**零延迟的神经预激**。
- 这正是人类"直觉"在工程上的完美实现。

---

## 5. 各组件现状与真实文件路径

| 组件 | 文件路径 | 状态 | 备注 |
|-----|---------|------|------|
| **会话编排器（主控意识）** | `polaris/cells/roles/runtime/internal/session_orchestrator.py` | **已落地**，骨架完整 | 已融合 `ShadowEngine` 跨 Turn 推测、`ArtifactStore` 记忆固化、`ContinuationPolicy` 裁决 |
| **事务内核（心脏）** | `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` | **已落地**，约 1500+ 行 | 包含 `TurnLedger` 审计账本、`KernelGuard` 断言、流式与非流式双入口 |
| **开发运行时（肌肉记忆）** | `polaris/cells/roles/kernel/internal/development_workflow_runtime.py` | **已落地**，骨架完整 | 支持 `ShadowEngine` Patch 消费、`max_retries` 自我修复循环 |
| **推测引擎（直觉预感）** | `polaris/cells/roles/kernel/internal/stream_shadow_engine.py` | **已落地** | 包含 `Registry` + `Resolver` 事务语义、跨 Turn Cache、Patch Cache |
| **Artifact 存储（海马体）** | `polaris/cells/roles/runtime/internal/session_artifact_store.py` | **已落地** | 支持增量 `diff`、去重、`compress_if_changed` 触发 |
| **Continuation Policy（理智中枢）** | `polaris/cells/roles/runtime/internal/continuation_policy.py` | **已落地** | 包含重复失败检测、`stagnation_v2` 检测、Speculative worthwhile 判断 |
| **Turn 契约与事件** | `polaris/cells/roles/kernel/public/turn_contracts.py`<br>`polaris/cells/roles/kernel/public/turn_events.py` | **已落地** | 所有跨层通信的 Public Contract |

---

## 6. 关键约束（物理法则）

这些约束直接来自代码中的 `KernelGuard` 与 `ContinuationPolicy`，是维持认知生命体理智与存续的**不可违背的物理法则**：

1. **单次决策法则**（`KernelGuard.assert_single_decision`）：每个 Turn 只能产生 **1 个**决策。防止精神分裂。
2. **单次工具批次法则**（`KernelGuard.assert_single_tool_batch`）：每个 Turn 最多执行 **1 个**工具批次。防止无限工具调用风暴。
3. **无隐藏连续法则**（`KernelGuard.assert_no_hidden_continuation`）：禁止状态轨迹中出现非法循环。防止模型在收口阶段偷偷发起新工具调用。
4. **最大自动回合法则**（`ContinuationPolicy.max_auto_turns`）：超过阈值必须停止，转为 `WAITING_HUMAN` 或 `END_SESSION`。防止僵尸死循环。
5. **Stagnation 检测法则**（`ContinuationPolicy._detect_stagnation_v2`）：最近 2 个 Turn 的 artifact hash 未变化且无 speculative hints 时，判定为停滞，强制终止。防止无意义空转。
6. **重复失败熔断法则**（`ContinuationPolicy._detect_repetitive_failure`）：最近 3 个 Turn 连续发生相同错误时，强制终止。防止在已知死胡同里无限钻探。

---

## 7. 结论

### 7.1 对齐结论
**"认知生命体 / 认知运行时"与当前工程架构零冲突、相互成就。**

- **没有工程约束**：认知生命体将变成精神分裂的模型，在无限 Prompt 循环中产生幻觉，最终 Token 爆仓而脑死亡。
- **没有哲学愿景**：工程代码就只是一堆冷冰冰的 if-else，失去了统一的叙事与演进目标。

### 7.2 可写入 AGENTS.md / ADR 的定稿描述

> **Polaris 终极愿景**：构建高可用、可进化、可观测的认知生命体，让每一个 Role 都成为拥有持久记忆、自我反思能力和潜意识加速器的数字实体。
>
> **实现路径（四层正交架构）**：
> 1. **角色层（Role）** —— 赋予身份；
> 2. **会话编排层（`RoleSessionOrchestrator` + `OrchestratorSessionState`）** —— 赋予主控意识与记忆中枢；
> 3. **专有运行时层（`DevelopmentWorkflowRuntime`）** —— 赋予肌肉记忆与潜意识闭环；
> 4. **事务内核层（`TurnTransactionController` + `StreamShadowEngine` + `KernelGuard`）** —— 赋予心脏跳动、神经预激与物理法则。
>
> 认知生命体 / 认知运行时正是这套架构的灵魂；`RoleSessionOrchestrator` + `TurnTransactionController` + `DevelopmentWorkflowRuntime` 则是灵魂唯一可运行的躯体。两者不是平行关系，而是上下层完美映射——哲学指导工程，工程反过来让哲学落地。

---

## 8. 相关引用

- `src/backend/polaris/cells/roles/runtime/internal/session_orchestrator.py`
- `src/backend/polaris/cells/roles/runtime/internal/continuation_policy.py`
- `src/backend/polaris/cells/roles/runtime/internal/session_artifact_store.py`
- `src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
- `src/backend/polaris/cells/roles/kernel/internal/development_workflow_runtime.py`
- `src/backend/polaris/cells/roles/kernel/internal/stream_shadow_engine.py`
- `src/backend/polaris/cells/roles/kernel/public/turn_contracts.py`
- `src/backend/polaris/cells/roles/kernel/public/turn_events.py`
- `src/backend/AGENTS.md`
