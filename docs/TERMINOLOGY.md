# Polaris 术语表 (Terminology Glossary)

> **本文档是 Polaris 项目唯一权威术语参考。**
>
> **强制规则：**
> - **面向用户的 UI 必须使用工程术语**（PM, Architect, Director, QA 等）。
> - **隐喻（官职、生物学）仅用于教育辅助和记忆，禁止进入面向用户的 UI、代码注释和公开契约。**
> - 代码实现、变量名、类名：**只使用工程实体名称**。
> - 架构文档、设计讨论：可引用隐喻作为记忆辅助，但必须同时给出工程实体名称。
> - 对外沟通：避免使用隐喻，使用标准技术术语。

---

## 1. 角色映射：工程术语 → 官职隐喻（仅教育辅助）

| 工程术语 (PRIMARY) | 官职隐喻（教育辅助） | 职责 |
|-------------------|---------------------|------|
| PM (Project Manager) | 尚书令 | 项目规划、任务拆分、需求管理、路由调度 |
| Architect | 中书令 | 架构设计、技术选型、系统架构 |
| Chief Engineer | 工部尚书 | 技术分析、代码审查、技术决策 |
| Director | 工部侍郎 | 代码执行、文件操作、按图施工 |
| QA (Quality Assurance) | 门下侍中 | 质量审查、测试验证、独立审计、否决权 |
| Scout | 探子 | 只读代码探索 |

> **注意：** `Chief Engineer`（工部尚书）的 rank 高于 `Director`（工部侍郎），不要被英文 "Director" 一词误导。

---

## 2. 运行时组件映射：工程实体 → 生物学隐喻（仅教育辅助）

| 工程实体 (PRIMARY) | 隐喻（教育辅助） | 工程职责 |
|-------------------|-----------------|---------|
| TurnTransactionController | 心脏 / 单次神经放电 | 单次事务化 turn 执行，不可逆的思考-行动循环 |
| RoleSessionOrchestrator | 主控意识 / 前额叶皮层 | 裁决"此刻该做什么"，编排 turn 级执行流 |
| DevelopmentWorkflowRuntime | 肌肉记忆 / 小脑 | read→write→test 自动闭环 |
| StreamShadowEngine | 直觉预感 / 神经预激 | 跨 turn 推测执行，让思考与行动时间重叠 |
| ContinuationPolicy + KernelGuard | 免疫系统 / 痛觉 | 防止死循环、资源泄漏、幻觉 |
| TurnEvent 流 | 脑电图 | 实时向人类/UI 暴露内心活动 |
| SessionArtifactStore | 海马体 | 持久记忆固化（Artifact 记忆固化） |
| OrchestratorSessionState | 躯体 / 自我意识 | 持久身份、会话状态 |

---

## 3. 核心技术术语

| 术语 (PRIMARY) | 定义 | 代码位置 |
|---------------|------|----------|
| KernelOne | AI 运行时 OS 基座 | `polaris/kernelone/` |
| Cell | 最小自治边界单元 | `polaris/cells/` |
| ACGA (Agent Cognitive Governance Architecture) | Agent 认知治理架构 | `docs/` |
| ContextOS | 上下文操作系统 | `polaris/kernelone/context/context_os/` |
| TruthLog | 追溯日志（append-only） | ContextOS 子组件 |
| Akashic Memory | 三层融合记忆系统 | `polaris/kernelone/akashic/` |
| StreamShadowEngine | 跨 turn 推测执行引擎 | `polaris/cells/roles/kernel/internal/` |
| Fitness Rules | 架构约束规则集 | `docs/governance/ci/fitness-rules.yaml` |
| Cognitive Runtime | 认知运行时（哲学层概念，禁止进入代码注释） | — |
| Cognitive Lifeform | 认知生命体（哲学层概念，禁止进入代码注释） | — |

---

## 4. ContextOS 四层架构

| 组件 (PRIMARY) | 工程文件 | 说明 |
|---------------|---------|------|
| TruthLog (TurnTruthLogRecorder) | `truthlog_recorder.py` | 单次 Turn 的追加-only 审计日志 |
| WorkingState (ConversationState) | `context_gateway/` | 运行时可变状态，绝不直接喂给 LLM |
| ReceiptStore | `receipt_store.py` | 大文件只存引用，不重复 |
| ProjectionEngine | `projection_engine.py` | 严格只读投影生成 |
| ContextGateway | `context_gateway/` | ContextOS 四层统一入口 |

---

## 5. TransactionKernel 组件

| 组件 (PRIMARY) | 工程文件 | 说明 |
|---------------|---------|------|
| TransactionKernel | `transaction_kernel.py` | 唯一 turn 事务执行内核，唯一 commit point |
| ToolBatchExecutor | `tool_batch_executor.py` | 单次工具批次执行 |
| StreamOrchestrator | `stream_orchestrator.py` | 工具调用流式编排 |
| ExplorationWorkflow | `exploration_workflow.py` | 探索模式工作流 |
| DebugStrategyEngine | `debug_strategy/` | 故障调试策略 |
| SpeculativeExecutor | `speculative_executor.py` | 跨 Turn 推测执行 |
| QualityChecker | `quality_checker.py` | Turn 输出质量检查 |
| KernelGuard | `kernel_guard.py` | 内核守卫，强制执行物理法则 |

---

## 6. 架构组件与效果

| 术语 (PRIMARY) | 定义 | 代码位置 |
|---------------|------|----------|
| TransactionKernel | 事务化 Turn 执行内核 | `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` |
| KernelGuard | 内核守卫，强制执行物理法则 | `polaris/cells/roles/kernel/internal/` |
| HandoffPack (ContextHandoffPack) | Cell 间移交契约，canonical handoff contract | `polaris/domain/cognitive_runtime/models.py` |
| Descriptor Pack | Cell 语义检索资产 | `polaris/cells/*/generated/descriptor.pack.json` |
| Context Pack | Cell 工作上下文资产 | `polaris/cells/*/generated/context.pack.json` |
| Verify Pack | Cell 验证资产 | `polaris/cells/*/generated/verify.pack.json` |
| NATS JetStream | 消息总线 | `polaris/infrastructure/messaging/nats/` |
| LanceDB | 向量搜索数据库 | `polaris/infrastructure/db/repositories/lancedb_code_search.py` |
| EDA Task Market | 事件驱动任务市场 | `polaris/cells/runtime/task_market/` |

---

## 7. 治理术语

| 术语 (PRIMARY) | 定义 | 位置 |
|---------------|------|------|
| Fitness Rules | 架构约束规则集 | `docs/governance/ci/fitness-rules.yaml` |
| Catalog Governance Gate | Cell 目录治理门禁 | `docs/governance/ci/scripts/run_catalog_governance_gate.py` |
| Verification Card | 验证卡片，记录修复证据 | `docs/governance/templates/verification-cards/` |
| ADR (Architecture Decision Record) | 架构决策记录 | `docs/governance/decisions/` |
| Pipeline Template | CI 流水线模板 | `docs/governance/ci/pipeline.template.yaml` |

---

## 8. 补充映射：已废弃隐喻 → 工程术语（仅用于历史代码理解）

| 旧隐喻 | 工程术语 (PRIMARY) | 说明 |
|--------|-------------------|------|
| 大理寺 | Policy | 策略闸门、合规检查 |
| 户部 | FinOps | 预算控制、成本管理 |
| 锦衣卫 | Security | 安全审查 |
| 总建筑师 | Architect | 旧隐喻，现统一使用 中书令 → Architect |
| 封驳 | 否决 (veto) | QA 对不合格产出的拒绝权 |
| 章奏 | 需求/任务 | PM 接收的用户输入 |
| 认知生命体 | Cognitive Agent / Role Session | 哲学层概念 |
| 神经预激 | Speculative Execution | 推测执行 |

---

## 9. 英文品牌词使用限制

| 术语 | 允许场景 | 禁止场景 |
|------|---------|---------|
| KernelOne | 基础设施层引用、import 路径、架构文档 | — |
| ACGA | 架构规范文档 | 代码注释中作为简称 |
| AgentAccel | `llm.toolkit` 引用 | — |
| Cognitive Runtime | `AGENTS.md` / `CLAUDE.md` 哲学映射章节 | 代码注释 |
| Cognitive Lifeform | `AGENTS.md` / `CLAUDE.md` 哲学映射章节 | 代码注释 |

---

## 10. 清理规则

1. **代码注释**：禁止使用任何隐喻，必须使用本文件中的工程术语。
2. **变量/类名**：保持现有命名不变（如 `TurnTransactionController` 已是工程术语）。
3. **文档文件**：`AGENTS.md` / `CLAUDE.md` 中的隐喻映射表保留（作为哲学层参考），但需明确标注"哲学层概念，非代码术语"。
4. **Persona 配置**：`prompt_templates.py` 中的默认人设必须使用现代工程风格词汇。

---

## 11. 使用指南

### 代码开发者
- 使用"工程术语 (PRIMARY)"列的命名编写代码
- 避免在代码注释中使用"隐喻"列的术语

### 文档阅读者
- 遇到隐喻时，参考本表找到对应的工程实体
- 理解隐喻仅为记忆辅助，工程实体才是真相来源

### 新成员 onboarding
1. 先阅读工程实体和文件路径
2. 理解职责后再阅读隐喻（如有需要）

### UI 开发者
- **面向用户的 UI 必须使用工程术语**（PM, Architect, Director, QA）
- 隐喻仅供内部文档和教育使用，不得出现在用户可见界面

---

## 12. 验证状态

- 目标目录：`polaris/cells/roles/kernel/internal/`
- 清理文件：
  - `constitution_rules.py` — 古代官职注释已全部替换为工程术语
  - `prompt_templates.py` — 人设默认值已去隐喻化
- 保留文件（原本即清洁）：
  - `turn_transaction_controller.py`
  - `stream_shadow_engine.py`
  - `kernel_guard.py`
  - `development_workflow_runtime.py`
  - `continuation_policy.py`
- 验证结果：
  - `ruff check` — 通过
  - `ruff format` — 通过
  - `mypy` — 通过
  - `pytest` — 54 tests passed
- Squad D/J 新增验证（2026-04-24）：
  - `test_circuit_breaker.py` — 已通过
  - `test_read_strategy.py` — 已通过
  - `test_thinking_validation.py` — 已通过
  - `test_policy.py` — 已通过
  - `test_speculation.py` — 已通过
  - `test_mutation_triggers.py` — 已通过
  - `test_stream_orchestrator.py` — 已通过
  - `test_hypothesis_generator.py` — 已通过
  - `test_llm_caller.py` — 已通过
  - `test_turn_engine.py` — 已通过
  - `test_action_first_integration.py` — 已通过
