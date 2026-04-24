# Polaris 术语表 (Terminology Glossary)

本文档是 Polaris 项目的唯一权威术语参考。代码实现中使用工程实体名称，隐喻仅用于记忆辅助。

## 表 A：工程实体 → 生物学隐喻映射

| 工程实体 | 隐喻（记忆辅助） | 工程职责 |
|---------|-----------------|---------|
| TurnTransactionController | 心脏 / 单次神经放电 | 单次事务化 turn 执行 |
| RoleSessionOrchestrator | 主控意识 / 前额叶皮层 | 编排 turn 级执行流 |
| DevelopmentWorkflowRuntime | 肌肉记忆 / 小脑 | read→write→test 自动闭环 |
| StreamShadowEngine | 直觉预感 / 神经预激 | 跨 turn 推测执行 |
| ContinuationPolicy + KernelGuard | 免疫系统 / 痛觉 | 防止死循环和资源泄漏 |
| TurnEvent 流 | 脑电图 | 实时向 UI 暴露活动 |
| SessionArtifactStore | 海马体 | 持久记忆固化 |
| OrchestratorSessionState | 躯体 / 自我意识 | 持久身份和会话状态 |

## 表 B：工程实体 → 官职隐喻映射

| 工程角色 | 官职隐喻 | 职责 |
|---------|---------|------|
| PM | 尚书令 | 项目规划、任务拆分 |
| Architect | 中书令 | 架构设计、技术选型 |
| Chief Engineer | 工部尚书 | 技术分析、代码审查 |
| Director | 工部侍郎 | 代码执行、文件操作 |
| QA | 门下侍中 | 质量审查、测试验证 |
| Scout | 探子 | 只读代码探索 |

## 表 C：核心技术术语

| 术语 | 定义 | 代码位置 |
|------|------|----------|
| KernelOne | AI 运行时 OS 基座 | polaris/kernelone/ |
| Cell | 最小自治边界单元 | polaris/cells/ |
| ACGA | Agent Cognitive Governance Architecture | docs/ |
| ContextOS | 上下文操作系统 | polaris/kernelone/context/context_os/ |
| TruthLog | 追溯日志（append-only） | ContextOS 子组件 |
| Akashic Memory | 三层融合记忆系统 | polaris/kernelone/akashic/ |
| StreamShadowEngine | 跨 turn 推测执行引擎 | polaris/cells/roles/kernel/internal/ |
| Fitness Rules | 架构约束规则集 | docs/governance/ci/fitness-rules.yaml |

## 表 D：架构组件与效果

| 术语 | 定义 | 代码位置 |
|------|------|----------|
| TransactionKernel | 事务化 Turn 执行内核 | polaris/cells/roles/kernel/internal/turn_transaction_controller.py |
| KernelGuard | 内核守卫，强制执行物理法则 | polaris/cells/roles/kernel/internal/ |
| HandoffPack | Cell 间移交契约 | polaris/domain/cognitive_runtime/models.py |
| Descriptor Pack | Cell 语义检索资产 | polaris/cells/*/generated/descriptor.pack.json |
| Context Pack | Cell 工作上下文资产 | polaris/cells/*/generated/context.pack.json |
| Verify Pack | Cell 验证资产 | polaris/cells/*/generated/verify.pack.json |
| NATS JetStream | 消息总线 | polaris/infrastructure/messaging/nats/ |
| LanceDB | 向量搜索数据库 | polaris/infrastructure/db/repositories/lancedb_code_search.py |
| EDA Task Market | 事件驱动任务市场 | polaris/cells/runtime/task_market/ |

## 表 E：治理术语

| 术语 | 定义 | 位置 |
|------|------|------|
| Fitness Rules | 架构约束规则集 | docs/governance/ci/fitness-rules.yaml |
| Catalog Governance Gate | Cell 目录治理门禁 | docs/governance/ci/scripts/run_catalog_governance_gate.py |
| Verification Card | 验证卡片，记录修复证据 | docs/governance/templates/verification-cards/ |
| ADR | 架构决策记录 | docs/governance/decisions/ |
| Pipeline Template | CI 流水线模板 | docs/governance/ci/pipeline.template.yaml |

---
> **使用约定**：
> - 代码实现、变量名、类名：只使用**工程实体名称**
> - 架构文档、设计讨论：可引用隐喻作为记忆辅助，但必须同时给出工程实体名称
> - 对外沟通：避免使用隐喻，使用标准技术术语