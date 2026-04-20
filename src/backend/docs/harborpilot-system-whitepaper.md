# Polaris 系统白皮书

> **版本**: 1.0
> **日期**: 2026-04-21
> **定位**: Polaris 自动化开发工厂的完整架构解读

---

## 一句话讲清整体原理

Polaris 的核心原理是：

> **把"AI 自己随便循环干活"改成"系统规定边界、AI 只在边界内做一次决策、结果必须留下证据并写入记忆"。**

传统 agent 常常像这样：AI 想一下 → 调工具 → 再想一下 → 再调工具 → 一直循环到它自己说"好了"。这很灵活，但问题也很大：可能无限循环、可能胡乱调工具、可能花很多 token、过程很难审计、失败后很难恢复。

Polaris 的思路是反过来：**AI 不再控制整个循环，系统来控制。**

---

## 第一部分：哲学顶层 —— 认知生命体

### 1.1 核心命题

**"认知生命体（Cognitive Lifeform）"与"认知运行时（Cognitive Runtime）"是 Polaris 工程架构的灵魂与哲学顶层；当前工程架构（`RoleSessionOrchestrator` + `TurnTransactionController` + `DevelopmentWorkflowRuntime` + `StreamShadowEngine`）是灵魂唯一可运行、可观测、可进化的实体化落地形态。**

两者是**上下层映射关系**，不是平行关系，更不是冲突关系。

### 1.2 为什么需要这个哲学顶层？

**没有工程约束**：认知生命体将变成精神分裂的模型，在无限 Prompt 循环中产生幻觉，最终 Token 爆仓而脑死亡。

**没有哲学愿景**：工程代码就只是一堆冷冰冰的 if-else，失去了统一的叙事与演进目标。

**当前架构把哲学真正变成了可运行、可测试、可进化的实体。**

### 1.3 概念 ↔ 工程实体映射表

| 抽象概念（认知层） | 工程实体（polaris 层） | 作用 |
|-----------------|----------------------|------|
| 认知生命体 | `OrchestratorSessionState` + `SessionArtifactStore` | 躯体 + 海马体 + 自我意识（持久身份、记忆、目标） |
| 主控意识 | `RoleSessionOrchestrator.execute_stream()` | 前额叶皮层：裁决"此刻该做什么" |
| 心脏 / 单次神经放电 | `TurnTransactionController` + `KernelGuard` | 不可逆的单次思考-行动循环 |
| 肌肉记忆 / 潜意识 | `DevelopmentWorkflowRuntime` | 小脑：自动执行 `read→write→test` 闭环 |
| 潜意识加速器 / 直觉预感 | `StreamShadowEngine`（跨 Turn 推测） | 神经预激：让"思考"与"行动"时间重叠 |
| 物理法则 / 生存约束 | `ContinuationPolicy` + `KernelGuard` | 防止死循环、资源泄漏、幻觉 |
| 脑电图 / 对外表达 | `TurnEvent` 流 | 实时向人类/UI 暴露内心活动 |

### 1.4 四层正交架构

```
角色层（Role）—— 赋予身份
    ↓
会话编排层（RoleSessionOrchestrator + OrchestratorSessionState）—— 主控意识
    ↓ 裁决"此刻该做什么"
专有运行时层（DevelopmentWorkflowRuntime）—— 肌肉记忆/潜意识
    ↓ 自动执行 read→write→test
事务内核层（TurnTransactionController + KernelGuard）—— 心脏/物理法则
    ↓ 单次神经放电，不可逆
ContextOS（TruthLog + Snapshot + WorkingState）—— 海马体/记忆固化
```

---

## 第二部分：角色层（Role）

这层就像工厂里的岗位分工。每个角色有明确的职责边界，不能跨级指挥。

### 2.1 PM（尚书令）

**文件**: `polaris/delivery/cli/pm/pm_role.py` → `PMRole`

PM 负责：

- 读需求
- 理解目标
- 写合同（PM_TASKS.json）
- 定义验收标准
- 任务分发与协调

核心问题回答：**"这次到底要做成什么样，才算完成？"**

### 2.2 Chief Engineer（工部尚书）

**文件**: `polaris/cells/roles/adapters/internal/chief_engineer_adapter.py` → `ChiefEngineerAdapter`

Chief Engineer 负责：

- 技术分析（technical_analysis）
- 架构评估（architecture_review）
- 技术决策（tech_decision）
- 深度代码审查（deep_code_review）
- Blueprint 生成（`polaris/cells/chief_engineer/blueprint/`）

核心问题回答：**"这个方案技术上可行吗？有什么风险？有什么替代方案？"**

### 2.3 Architect（中书令）

**文件**: `polaris/cells/roles/adapters/internal/architect_adapter.py` → `ArchitectAdapter`

Architect 负责：

- 需求分析（analyze_requirements）
- 架构设计（generate_architecture）
- 设计文档产出（write_design_docs）
- 文档质量门禁（质量拦截薄弱/泄漏内容）

核心问题回答：**"系统应该怎么组织？模块怎么拆分？接口怎么设计？"**

### 2.4 Director（工部侍郎）

**文件**: `polaris/cells/roles/adapters/internal/director/adapter.py` → `DirectorAdapter`

Director 是执行负责人：

- 任务执行（task_execution）
- 代码改写（file_operations）
- 验证与测试
- 工具调用编排

可以把它理解为"施工队长"。核心问题回答：**"怎么把这个方案实现出来？"**

### 2.5 QA（门下侍中）

**文件**: `polaris/cells/roles/adapters/internal/qa_adapter.py` → `QAAdapter`

QA 不是帮忙写代码的，它是独立裁判：

- 确定性工作区质量检查
- 代码审查（code_review）
- 质量门禁裁决
- 缺陷报告（report_defect）
- 验收测试（acceptance_test）

核心问题回答：**"这活干完了，满足合同吗？证据够不够？"**

**意义**：执行者不能自己宣布自己成功，必须有独立验收。

### 2.6 Scout（探子）

**文件**: `polaris/cells/roles/profile/internal/builtin_profiles.py` → Scout Role Profile

Scout 是只读代码探索角色：

- 定位文件和代码
- 汇总证据
- 提出假设
- 供 PM/Director 调用

核心问题回答：**"这段代码在哪里？它做了什么？"**

### 2.7 Dashboard（观察台）

Dashboard 是观察台，不是控制台：

- 看系统在做什么
- 看日志、证据、状态、产物
- 帮人类理解当前进度

**它不应该直接改代码，也不应该偷偷替系统做决定。**

---

## 第三部分：会话编排层 —— 主控意识

### 3.1 RoleSessionOrchestrator

**文件**: `polaris/cells/roles/runtime/internal/session_orchestrator.py`

这是主控意识的工程实体，前额叶皮层：裁决"此刻该做什么"。

核心职责：
- 管理 turn 级状态机轮转
- ContinuationPolicy 仲裁
- ShadowEngine 跨 Turn 预热
- 构建 4 区域 XML 继续提示（Goal / Progress / WorkingMemory / Instruction）
- 检测 stagnation 和重复失败

### 3.2 OrchestratorSessionState

**文件**: `polaris/cells/roles/runtime/internal/continuation_policy.py`

这就是"当前会话的大脑状态"：

```python
class OrchestratorSessionState:
    session_id: str
    goal: str                      # 任务目标
    turn_count: int                 # 当前轮次
    structured_findings: dict      # 维度归纳结论（防止多轮失忆）
    task_progress: str             # exploring/investigating/implementing/verifying/done
    key_file_snapshots: dict       # 关键文件快照
    artifact_hashes: dict           # 产物哈希（检测停滞）
```

### 3.3 ContinuationPolicy

**文件**: `polaris/cells/roles/runtime/internal/continuation_policy.py`

这是"继续/停止规则"—— 制度化的自我保护本能：

| 法则 | 触发条件 | 动作 |
|------|----------|------|
| 最大轮次法则 | `turn_count >= max_auto_turns` | 停止 |
| 重复失败熔断 | 3 个 Turn 连续相同错误 | 强制终止 |
| Stagnation 检测 | 2 个 Turn 的 artifact hash 未变且无 speculative hints | 强制终止 |
| speculation 收益递减 | speculative hints 连续无收益 | 降级或停止 |

---

## 第四部分：专有运行时层 —— 肌肉记忆

### 4.1 DevelopmentWorkflowRuntime

**文件**: `polaris/cells/roles/kernel/internal/development_workflow_runtime.py`

这是干开发活的流程引擎，擅长：
- 读代码
- 改代码
- 跑测试
- 修复问题
- 再验证

事件序列：`RuntimeStartedEvent` → `TurnPhaseEvent(patching_code)` → `ToolBatchEvent(apply_patch)` → `TurnPhaseEvent(running_tests)` → `ToolBatchEvent(run_tests)` → `ContentChunkEvent` → `RuntimeCompletedEvent`

### 4.2 ExplorationWorkflowRuntime

**文件**: `polaris/cells/roles/kernel/internal/exploration_workflow.py`

这是探索型流程引擎，擅长：
- 找文件
- 查根因
- 汇总证据
- 提出假设
- 排除假设

**核心价值**：先搞清楚"问题到底在哪里"，而不是立刻改。

---

## 第五部分：事务内核层 —— 心脏

这是最关键的底层，就像心脏 + 物理法则。

### 5.1 TurnTransactionController

**文件**: `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`

这是 turn 的总控制器，"这一拍心跳到底怎么跳"的控制器。

一个 turn 的生命周期：
```
IDLE → CONTEXT_BUILT → DECISION_REQUESTED → DECISION_RECEIVED → DECISION_DECODED
                                                                          ↓
                                    ┌───────────────────────────────────────┤
                                    ↓                                       ↓
                            FINAL_ANSWER_READY              TOOL_BATCH_EXECUTING
                                    ↓                                       ↓
                            FINALIZATION_REQUESTED              TOOL_BATCH_EXECUTED
                                    ↓                                       ↓
                            FINALIZATION_RECEIVED                         ↓
                                    ↓                                       ↓
                                COMPLETED ←──────────── COMPLETED/FAILED
```

### 5.2 KernelGuard

**文件**: `polaris/cells/roles/kernel/internal/kernel_guard.py`

这是最核心的"物理法则"，强制三条铁律：

| 铁律 | 规则 | 违规后果 |
|------|------|----------|
| single_decision | 每 turn 必须恰好 1 个 TurnDecision | `KernelGuardError` |
| single_tool_batch | 每 turn 最多 1 个 ToolBatch | `KernelGuardError` |
| no_hidden_continuation | 不允许 DECISION_REQUESTED 出现两次 | `KernelGuardError` |

### 5.3 StreamShadowEngine

**文件**: `polaris/cells/roles/kernel/internal/stream_shadow_engine.py`

这是"潜意识加速器 / 直觉预感"—— 让"思考"与"行动"时间重叠。

核心能力：
- **推测执行**：在当前 turn 还在运行时，预执行可能的下一个动作
- **跨 Turn 缓存**：`_cross_turn_cache` 缓存推测结果
- **Patch 缓存**：`_speculated_patch_cache` 缓存已推测的 patch 结果
- **事务语义**：支持 ADOPT / JOIN / CANCEL / REPLAY

---

## 第六部分：工具执行层

### 6.1 ToolBatch

**文件**: `polaris/cells/roles/kernel/internal/tool_batch_runtime.py`

ToolBatch 是一组在同一个 turn 内被当成一个整体执行的工具动作：

```python
class ToolBatchRuntime:
    # 执行策略
    READONLY_PARALLEL   # 多个只读工具并行
    WRITE_SERIAL        # 写操作串行
    ASYNC_RECEIPT       # 异步工具返回 pending receipt
```

### 6.2 幂等性保证

- `call_id` (ToolCallId NewType) 标识批次内的单个工具调用
- `BatchReceipt` 通过 `call_id` 追踪结果，确保幂等处理
- 防止同一动作被重复执行导致重复写文件、重复提交外部副作用

### 6.3 side_effect_class 副作用等级

| 等级 | 含义 | 预算影响 |
|------|------|----------|
| `readonly` | 只读，不改东西 | 不消耗 write budget |
| `local_write` | 改本地工作区 | 消耗 workspace write budget |
| `external_write` | 改外部系统，可能不可逆 | 消耗 external effect budget |

---

## 第七部分：收口阶段 —— 神经不应期

### 7.1 为什么需要收口？

很多系统死就死在"工具执行完，看起来在总结，实际上模型突然又开始调工具"。这就像心脏在收缩后必须要有绝对不应期，防止心脏纤维性颤动（无限循环）。

### 7.2 收口策略

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| `NONE` | 直接返回工具结果 | 简单工具调用 |
| `LOCAL` | 本地模板整理 | 需要格式化输出 |
| `LLM_ONCE` | 允许 LLM 再总结一次，**禁止再调工具** | 需要自然语言总结 |

### 7.3 硬封印机制

```python
# 收口阶段强制切断工具能力
tool_choice = none
# 即使 LLM 产生 tool_calls 意图，也被 decoder 过滤
# 即使 decoder 失败，KernelGuard 触发 panic
```

---

## 第八部分：ContextOS —— 海马体

**文件**: `polaris/kernelone/context/context_os/runtime.py`

这层非常像海马体 + 长期记忆系统。核心作用：**保存真相，不让系统靠聊天历史瞎猜**。

### 8.1 四层架构

```
StateFirstContextOS
├── TruthLogService      # 追加写入的原始真相
├── WorkingStateManager # 运行时工作状态
├── ReceiptStore        # 工具执行回执
└── ProjectionEngine    # Prompt 生成投影
```

### 8.2 TruthLog（最底层真相日志）

**文件**: `polaris/kernelone/context/truth_log_service.py`

- append-only，只追加不覆盖
- 记录发生过的事实
- 是最终审计依据
- `replay()` 返回深拷贝，保证不可变性

### 8.3 Snapshot（稳定存档）

**文件**: `polaris/kernelone/context/context_os/snapshot.py`

- 某个 turn 提交完成后的稳定快照
- 可以被下一 turn 拿来继续
- 可以用来恢复系统
- SHA256 校验确保完整性

### 8.4 WorkingState（当前工作视图）

**文件**: `polaris/kernelone/context/working_state_manager.py`

- 当前方便用的状态投影
- 给 Prompt Builder 或 Orchestrator 快速读取
- 不一定是最终真相源

### 8.5 BudgetPlan（预算管理器）

**文件**: `polaris/kernelone/context/context_os/models_v2.py` → `BudgetPlanV2`

```python
# Claude Code 公式
input_budget = context_window - output_reserve - tool_reserve - safety_margin
```

管理：token 预算、工具预算、副作用预算。

### 8.6 ReceiptStore（回执仓库）

**文件**: `polaris/kernelone/context/receipt_store.py`

- 某个工具批次执行过没有
- 结果是什么
- 有没有对应 receipt
- 恢复时能不能 adopt 旧结果

---

## 第九部分：Commit Protocol —— 记忆固化纪律

### 9.1 三段式提交

```python
async def _execute_commit_protocol():
    # 1. Pre-commit validation
    pre_commit_validate(envelope, state)  # 检查 7 项合法性

    # 2. Durable commit
    await truth_log.append(entry)
    await snapshot.materialize()
    await receipt_store.link()

    # 3. Seal
    sealed = seal_turn(commit_ref)  # hash + parent + seq_range
```

### 9.2 Pre-commit Validation 检查清单

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 单决策 | `len(decisions) == 1` |
| 2 | 单工具批次 | `len(tool_batches) <= 1` |
| 3 | 无隐藏连续 | `hidden_continuation == 0` |
| 4 | Receipt 齐全 | 所有工具调用都有回执 |
| 5 | Artifact 有效 | 所有 artifact 都有 hash |
| 6 | Budget 未超 | `tokens_used <= budget` |
| 7 | 副作用合规 | `side_effect_class` 在允许范围内 |

---

## 第十部分：Failure Taxonomy —— 痛觉分类学

**文件**: `polaris/cells/roles/kernel/public/turn_contracts.py` → `FailureClass`

这不是所有失败都一样，系统要知道"哪里错、为什么错、能不能继续"。

| 失败类型 | 认知映射 | Continuation 动作 |
|---------|----------|------------------|
| `CONTRACT_VIOLATION` | 心脏节律异常（物理法则被打破） | **STOP** |
| `RUNTIME_FAILURE` | 肌肉拉伤（工具执行失败） | **RETRY** 或 **STOP** |
| `DURABILITY_FAILURE` | 记忆写入失败（海马体故障） | **STOP + SEEK HELP** |
| `INSUFFICIENT_EVIDENCE` | 感知不足（信息不够做决策） | **CONTINUE EXPLORE** |
| `POLICY_FAILURE` | 行为被阻止（超出预算或权限） | **STOP or REROUTE** |

---

## 第十一部分：Cognitive Runtime —— 认知运行时

**文件**: `polaris/cells/factory/cognitive_runtime/public/service.py`

Cognitive Runtime 是 PolarIs 架构中负责跨角色运行时权威操作的工程实现层。它不是平行于其他层的另一套系统，而是作为**认知生命体的可运行化身**，贯穿所有四层架构。

### 11.1 定位

Cognitive Runtime 是 Polaris 认知架构的**工程落地形态**（Cognitive Lifeform = 哲学顶层 ↔ Cognitive Runtime = 工程底层）。

### 11.2 核心能力

| 能力 | Command/Query | 作用 |
|------|---------------|------|
| 上下文解析 | `ResolveContextCommandV1` | 为指定 role 解析并投影当前认知上下文 |
| 编辑范围租约 | `LeaseEditScopeCommandV1` | 对工作区路径子集建立 TTL 租约，防止并发冲突 |
| 变更集验证 | `ValidateChangeSetCommandV1` | 验证变更是否在授权范围内（change-set gate） |
| 运行时回执记录 | `RecordRuntimeReceiptCommandV1` | 记录 turn 执行凭证，支持审计追溯 |
| Handoff Pack 导出 | `ExportHandoffPackCommandV1` | 导出会话状态包供角色间传递 |
| Handoff Pack 水化 | `RehydrateHandoffPackCommandV1` | 将导出的 handoff pack 重新注入目标角色会话 |
| Diff → Cell 映射 | `MapDiffToCellsCommandV1` | 将代码变更映射到对应的 Cell 所有权 |
| 投影编译 | `RequestProjectionCompileCommandV1` | 请求对变更进行投影编译 |
| 晋升/拒绝裁决 | `PromoteOrRejectCommandV1` | 基于投影状态决定是否允许变更推进 |
| 回滚账本记录 | `RecordRollbackLedgerCommandV1` | 记录回滚决策，支持审计追溯 |

### 11.3 关键设计原则

1. **认知类比不进入运行时命名**：哲学概念仅用于架构解释，不作为 schema、类、字段、API 的命名依据
2. **派生投影原则**：`cognitive_state_delta` 等仅为可选的 summary projection，可从 snapshot/truthlog/findings 重建
3. **SessionArtifactStore 是派生记忆层**： retrievable derived memory / convenience cache，`TruthLog` 才是 append-only event truth

---

## 第十二部分：Structured Findings —— 防止多轮失忆的小纸条

**文件**: `polaris/cells/roles/runtime/internal/continuation_policy.py`

这是解决"多 turn 失忆"的关键。把当前阶段最重要的信息整理成结构化小纸条：

```python
structured_findings = {
    "confirmed_facts": [...],      # 已确认事实
    "rejected_hypotheses": [...],  # 已否定假设
    "open_questions": [...],       # 尚未解决的问题
    "relevant_refs": [...],        # 相关文件和证据
    "_confidence_key": 0.85,       # 置信度
    "_superseded_keys": [...],     # 被取代的旧 findings
}
```

### 12.1 SessionPatch 语义

```python
# 通过 apply_session_patch() 增量更新 findings
apply_session_patch(state, patch)  # 置信度感知的语义合并
get_active_findings(state)          # 过滤掉被取代的 findings
```

---

## 第十三部分：TurnOutcomeEnvelope —— 标准结果封装

**文件**: `polaris/cells/roles/kernel/public/turn_contracts.py`

这是"一次 turn 结束后的标准报告单"，相当于心脏跳动后的"标准心电图报告"。

```python
class TurnOutcomeEnvelope:
    turn_result: TurnResult           # canonical turn result
    continuation_mode: TurnContinuationMode
        # END_SESSION / AUTO_CONTINUE / WAITING_HUMAN
        # HANDOFF_EXPLORATION / HANDOFF_DEVELOPMENT / SPECULATIVE_CONTINUE
    next_intent: str | None           # handoff 目标意图
    session_patch: dict[str, Any]      # 语义补丁用于状态更新
    artifacts_to_persist: list        # 待持久化的产物引用
    speculative_hints: dict[str, Any] # 推测执行提示
```

### 13.1 ContinuationMode 决策矩阵

| Mode | 触发条件 | Orchestrator 动作 |
|------|----------|------------------|
| `END_SESSION` | 任务完成或物理法则违规 | 结束会话 |
| `AUTO_CONTINUE` | 需要继续且未达轮次上限 | 自动进入下一 turn |
| `WAITING_HUMAN` | 需要人类输入 | 暂停等待 |
| `HANDOFF_EXPLORATION` | 需要切换探索模式 | 移交 ExplorationWorkflowRuntime |
| `HANDOFF_DEVELOPMENT` | 需要切换开发模式 | 移交 DevelopmentWorkflowRuntime |
| `SPECULATIVE_CONTINUE` | ShadowEngine 推测值得继续 | 推测执行下一个动作 |

---

## 第十四部分：完整工作流串烧

用一个完整流程把各层串起来：

```
1. PM 定义合同
   → PMRole 产出 PM_TASKS.json

2. Chief Engineer 技术评估
   → ChiefEngineerAdapter 进行技术分析
   → 输出风险评估、备选方案、技术决策

3. Architect 架构设计
   → ArchitectAdapter 产出架构文档
   → plan_markdown + architecture_markdown
   → 文档质量门禁拦截

4. Director 执行实现
   → DirectorAdapter 按合同执行
   → DevelopmentWorkflowRuntime 执行 read→write→test 闭环

5. Orchestrator 决定当前阶段
   → RoleSessionOrchestrator.execute_stream()
   → 根据 ContinuationPolicy 判断：探索/实现/验证/停止

6. Transaction Kernel 执行一个 turn
   → TurnTransactionController._execute_turn()
   → TurnStateMachine 状态流转
   → KernelGuard 强制三条铁律

7. 如果需要工具，执行 ToolBatch
   → ToolBatchRuntime 按策略执行（READONLY_PARALLEL / WRITE_SERIAL）
   → ReceiptStore 记录回执
   → 幂等键防止重复执行

8. 进入收口阶段
   → FinalizationHandler 强制封印工具能力
   → tool_choice = none

9. 生成 TurnOutcome
   → TurnOutcomeEnvelope 封装完整报告

10. Commit 到 ContextOS
    → Pre-commit validation (7 项检查)
    → TruthLog.append()
    → Snapshot.materialize()
    → Seal 封印

11. Orchestrator 读结果，决定下一步
    → ContinuationPolicy.can_continue()
    → FailureClass 映射到对应动作

12. Cognitive Runtime 跨角色协调
    → ResolveContext / LeaseEditScope / ValidateChangeSet
    → HandoffPack 导出/水化

13. QA 独立验收
    → QAAdapter 运行确定性检查
    → 裁决 PASS / FAIL / INCONCLUSIVE
```

---

## 第十五部分：为什么比传统 Agent 更稳？

| 传统 Agent | Polaris |
|-----------|-------------|
| AI 控制整个循环 | 系统控制边界，AI 只在边界内决策 |
| 靠聊天历史记忆 | ContextOS 真相系统，append-only |
| 失败只知道"挂了" | Failure Taxonomy 精确知道哪里错、为什么错、能不能继续 |
| 无限 while loop | 很多可审计的小 turn，每 turn 都有收口和提交 |
| 执行者自己宣布成功 | QA 独立验收，不能自举 |
| 无角色分工 | 6 种角色分工明确（PM/Chief Engineer/Architect/Director/QA/Scout） |

---

## 第十六部分：核心组件速查表

### 角色层

| 组件 | 一句话描述 | 文件位置 |
|------|-----------|----------|
| PMRole | 写合同的人（尚书令） | `polaris/delivery/cli/pm/pm_role.py` |
| ChiefEngineerAdapter | 技术分析/架构评估/深度代码审查（工部尚书） | `polaris/cells/roles/adapters/internal/chief_engineer_adapter.py` |
| ArchitectAdapter | 架构设计与设计文档产出（中书令） | `polaris/cells/roles/adapters/internal/architect_adapter.py` |
| DirectorAdapter | 干活的人/执行负责人（工部侍郎） | `polaris/cells/roles/adapters/internal/director/adapter.py` |
| QAAdapter | 独立裁判（门下侍中） | `polaris/cells/roles/adapters/internal/qa_adapter.py` |
| Scout Role | 只读代码探索（探子） | `polaris/cells/roles/profile/internal/builtin_profiles.py` |

### 会话编排层

| 组件 | 一句话描述 | 文件位置 |
|------|-----------|----------|
| RoleSessionOrchestrator | 主控意识/总调度 | `polaris/cells/roles/runtime/internal/session_orchestrator.py` |
| ContinuationPolicy | 继续/停止规则 | `polaris/cells/roles/runtime/internal/continuation_policy.py` |
| OrchestratorSessionState | 会话大脑状态 | `polaris/cells/roles/runtime/internal/continuation_policy.py` |

### 专有运行时层

| 组件 | 一句话描述 | 文件位置 |
|------|-----------|----------|
| DevelopmentWorkflowRuntime | 开发流程肌肉记忆 | `polaris/cells/roles/kernel/internal/development_workflow_runtime.py` |
| ExplorationWorkflowRuntime | 探索流程肌肉记忆 | `polaris/cells/roles/kernel/internal/exploration_workflow.py` |

### 事务内核层

| 组件 | 一句话描述 | 文件位置 |
|------|-----------|----------|
| TurnTransactionController | 心脏跳动控制器 | `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` |
| TurnStateMachine | turn 状态机（交通灯） | `polaris/cells/roles/kernel/internal/turn_state_machine.py` |
| KernelGuard | 物理法则守卫 | `polaris/cells/roles/kernel/internal/kernel_guard.py` |
| StreamShadowEngine | 潜意识加速器 | `polaris/cells/roles/kernel/internal/stream_shadow_engine.py` |
| ToolBatchRuntime | 工具批次执行器 | `polaris/cells/roles/kernel/internal/tool_batch_runtime.py` |

### ContextOS（记忆系统）

| 组件 | 一句话描述 | 文件位置 |
|------|-----------|----------|
| StateFirstContextOS | 海马体/记忆系统门面 | `polaris/kernelone/context/context_os/runtime.py` |
| TruthLogService | 追加写入真相日志 | `polaris/kernelone/context/truth_log_service.py` |
| ImmutableSnapshot | 稳定存档 | `polaris/kernelone/context/context_os/snapshot.py` |
| WorkingStateManager | 当前工作状态 | `polaris/kernelone/context/working_state_manager.py` |
| ReceiptStore | 工具执行回执 | `polaris/kernelone/context/receipt_store.py` |

### Cognitive Runtime & 契约

| 组件 | 一句话描述 | 文件位置 |
|------|-----------|----------|
| CognitiveRuntimePublicService | 认知运行时门面 | `polaris/cells/factory/cognitive_runtime/public/service.py` |
| TurnOutcomeEnvelope | 标准报告单 | `polaris/cells/roles/kernel/public/turn_contracts.py` |
| FailureClass | 失败分类枚举 | `polaris/cells/roles/kernel/public/turn_contracts.py` |

---

## 附录 A：架构层级总览图

```
┌─────────────────────────────────────────────────────────────┐
│                      角色层 (Role)                          │
│   PM │ Chief Engineer │ Architect │ Director │ QA │ Scout    │
└─────────────────────────────────────────────────────────────┘
                            ↓ 赋予身份
┌─────────────────────────────────────────────────────────────┐
│                   会话编排层 (Orchestrator)                   │
│   RoleSessionOrchestrator + OrchestratorSessionState         │
│   + ContinuationPolicy + StructuredFindings                  │
└─────────────────────────────────────────────────────────────┘
                            ↓ 裁决"此刻该做什么"
┌─────────────────────────────────────────────────────────────┐
│                   专有运行时层 (Workflow Runtime)            │
│   DevelopmentWorkflowRuntime │ ExplorationWorkflowRuntime    │
└─────────────────────────────────────────────────────────────┘
                            ↓ 自动执行 read→write→test
┌─────────────────────────────────────────────────────────────┐
│                   事务内核层 (Transaction Kernel)             │
│   TurnTransactionController + TurnStateMachine + KernelGuard  │
│   + StreamShadowEngine + ToolBatchRuntime                    │
└─────────────────────────────────────────────────────────────┘
                            ↓ 单次神经放电，不可逆
┌─────────────────────────────────────────────────────────────┐
│                   ContextOS (记忆系统)                        │
│   TruthLog + Snapshot + WorkingState + ReceiptStore          │
│   + ProjectionEngine                                         │
└─────────────────────────────────────────────────────────────┘
                            ↓ 跨角色认知协调
┌─────────────────────────────────────────────────────────────┐
│                   Cognitive Runtime                          │
│   ResolveContext / LeaseEditScope / ValidateChangeSet         │
│   HandoffPack / PromotionDecision / RollbackLedger            │
└─────────────────────────────────────────────────────────────┘
```

---

## 附录 B：关键设计约束

1. **认知类比不进入运行时命名**：哲学概念仅用于架构解释
2. **派生投影可丢弃**：`cognitive_state_delta` 可从 snapshot/truthlog/findings 重建
3. **TruthLog 是唯一真相源**：SessionArtifactStore 是 convenience cache
4. **Commit 关键路径不得弱化**：异步后处理可以，但 critical section 必须严格顺序一致
5. **ContextHandoffPack 是 canonical handoff contract**：禁止再造第二套 schema
