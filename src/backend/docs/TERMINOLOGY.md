# Polaris 工程术语规范

> 适用范围：`src/backend/polaris/cells/roles/kernel/internal/`
> 目的：消除代码中的隐喻别名，统一使用工程术语。

---

## 1. 古代官职隐喻（已清理）

| 旧隐喻 | 工程术语 | 说明 |
|--------|---------|------|
| 尚书令 | Project Manager (PM) | 任务编排、需求管理、路由调度 |
| 中书令 | — | 未在 kernel/internal 中使用 |
| 工部尚书 | Chief Engineer | 设计图纸、生成蓝图、技术决策 |
| 工部侍郎 | Director | 按图施工、代码实现 |
| 门下侍中 | QA (Quality Assurance) | 独立审计、否决权、最终验收 |
| 大理寺 | Policy | 策略闸门、合规检查 |
| 户部 | FinOps | 预算控制、成本管理 |
| 锦衣卫 | Security | 安全审查 |
| 总建筑师 | Architect | 系统架构 |
| 封驳 | 否决 (veto) | QA 对不合格产出的拒绝权 |
| 章奏 | 需求/任务 | PM 接收的用户输入 |

---

## 2. 生物/神经科学隐喻（已清理）

| 旧隐喻 | 工程术语 | 说明 |
|--------|---------|------|
| 认知生命体 | Cognitive Agent / Role Session | 角色会话实体 |
| 主控意识 | RoleSessionOrchestrator | 会话编排器 |
| 心脏 / 单次神经放电 | TurnTransactionController | 单次事务执行内核 |
| 肌肉记忆 / 潜意识 | DevelopmentWorkflowRuntime | 开发工作流运行时 |
| 潜意识加速器 / 直觉预感 | StreamShadowEngine | 流式推测执行引擎 |
| 物理法则 / 生存约束 | ContinuationPolicy + KernelGuard | 运行时约束与断言 |
| 脑电图 / 对外表达 | TurnEvent 流 | 事件流输出 |
| 海马体 | SessionArtifactStore | 会话产物存储 |
| 前额叶皮层 | — | 未在 kernel/internal 中使用 |
| 小脑 | — | 未在 kernel/internal 中使用 |
| 免疫系统 | — | 未在 kernel/internal 中使用 |
| 痛觉 | — | 未在 kernel/internal 中使用 |
| 神经预激 | Speculative Execution | 推测执行 |
| 躯体 | OrchestratorSessionState | 会话状态 |
| 自我意识 | — | 未在 kernel/internal 中使用 |

---

## 3. 英文新造词/品牌词（保留，但限制使用场景）

| 术语 | 使用场景 | 说明 |
|------|---------|------|
| KernelOne | 基础设施层引用 | 底座框架名称，保留在 import 路径和架构文档中 |
| ACGA | 架构规范文档 | Agent-Centric Graph Architecture，保留在规范文档中 |
| AgentAccel | 工具执行层 | 工具加速执行器，保留在 `llm.toolkit` 引用中 |
| Cognitive Runtime | 哲学层概念 | 保留在 `CLAUDE.md` 哲学映射章节，禁止进入代码注释 |
| Cognitive Lifeform | 哲学层概念 | 保留在 `CLAUDE.md` 哲学映射章节，禁止进入代码注释 |

---

## 4. 清理规则

1. **代码注释**：禁止使用任何隐喻，必须使用上表中的工程术语。
2. **变量/类名**：保持现有命名不变（如 `TurnTransactionController` 已是工程术语）。
3. **文档文件**：`CLAUDE.md` 和 Blueprint 文档中的隐喻映射表保留（作为哲学层参考），但需明确标注"哲学层概念，非代码术语"。
4. **Persona 配置**：`prompt_templates.py` 中的默认人设已改为现代工程风格词汇（如"已核实"替代"臣已核实"）。

---

## 5. 验证状态

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
  - `pytest` — 54 tests passed (test_truthlog_recorder: 3, test_transaction_controller: 29, test_mutation_guard_soft_mode: 22)
