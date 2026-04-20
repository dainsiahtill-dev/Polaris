# ACGA 3 Factory Positioning

- 状态: Draft / Supplement
- 适用范围: Polaris 仓库级产品定位补充
- 角色: 面向无人值守自动化软件开发工厂的产品与差异化说明

> 本文不是当前实现完成证明，而是对 Polaris 下一阶段产品目标的定位补充。当前后端正式架构真相仍以 `src/backend/docs/graph/**`、`src/backend/docs/FINAL_SPEC.md` 与相关 `cell.yaml` 为准。

---

## 1. 一句话定位

Polaris 的 ACGA 3 方向，不是再做一个“更聪明的 AI 编码助手”，而是把 Polaris 进化为：

**面向无人值守软件开发工厂的 Autonomous Coding Control Plane**

也就是：

- 让 AI/Agent/LLM 持续写代码、改代码、重构代码
- 允许系统在受控边界内持续演化更好的算法、实现和局部架构
- 同时保持可验证、可审计、可回滚、可比较

### 1.1 核心目标之一：帮 AI/Agent 分担上下文负担

Polaris 的 ACGA 3 方向，核心目标之一不是“让模型记住更多”，而是：

**让系统替模型记住正确的东西。**

这意味着 Polaris 要主动替 AI/Agent 背负项目级上下文负担，而不是把连续多轮成功寄托在单次会话的残余记忆上。

在产品层面，这个目标收敛为 4 个核心能力：

1. `Context Capture`
   - 把 graph、cell、projection、receipt、verify 等上下文沉淀成运行时资产
2. `Context Slicing`
   - 每次只给 AI 当前任务最小必要且正确的上下文
3. `Context Handoff`
   - 支撑跨 session、跨 agent、跨 context window 的稳定续跑
4. `Context Guarding`
   - 防止 AI 因上下文漂移而越界、误改、失焦

因此，Polaris 的价值不只是“给模型更多上下文”，而是：

- 替模型管理上下文世界本身
- 把长期上下文从模型脑内迁移到运行时资产
- 让 LLM 更专注于理解、提案、执行局部任务，而不是维持全局连续性

---

## 2. 我们真正要解决的问题

Polaris 未来的目标，不是优化单次聊天写代码体验，而是解决以下更难的问题：

1. 如何让 AI 在长周期、多轮次、无人值守的工程交付中持续产出代码
2. 如何让 AI 的改动命中正确边界，而不是误伤无关模块
3. 如何支撑跨文件修改、符号级重构与局部架构演化
4. 如何让系统在失败后自动恢复、定位、重试与回放
5. 如何把每次运行结果沉淀为下一轮 AI 可复用的真实资产
6. 如何持续替 AI/Agent 卸载上下文负担，避免长任务中的记忆漂移与上下文失焦

---

## 3. 与传统 AI 编码产品的差异

### 3.1 Polaris 不直接与模型竞争

Polaris 不把自身定义为：

- 新的基础模型
- 新的聊天机器人
- 新的 IDE 插件

Polaris 的角色是：

- 编排多模型 / 多 Agent / 多角色协作
- 提供运行时治理、边界裁决、验证闭环与演化机制
- 把 `Codex / Claude / Gemini / Cursor` 一类能力视为可接入的推理与执行引擎

### 3.2 与传统产品相比的核心差异

| 维度 | 传统 AI 编码产品 | Polaris ACGA 3 方向 |
|------|------------------|-------------------------|
| 核心定位 | 人在回路里的编码助手 | 无人值守软件工厂控制面 |
| 认知单位 | 文件、diff、会话 | Cell、Projection、Back-Mapping、Receipt |
| 默认交互 | 对话式单任务 | 持续运行的工厂闭环 |
| 主要目标 | 帮人写一次代码 | 让系统长期稳定地产生和演化代码 |
| 结果约束 | 依赖人工 review | 依赖 runtime gate / verification / promotion |
| 失败恢复 | 人工接管为主 | 设计为自动恢复与可回放 |
| 经验沉淀 | 以会话和提示词为主 | 以结构化资产和演化证据为主 |

### 3.3 Polaris 的真正优势不在“模型更强”

Polaris 的护城河不应建立在：

- prompt 更长
- 角色更多
- UI 更炫

真正的优势应建立在：

- `Cell IR`
- `Projection Map`
- `Back-Mapping`
- `Runtime Receipts`
- `Verification / Comparison / Promotion / Rollback`

---

## 4. ACGA 3 的产品核心

### 4.1 从“治理系统”升级为“受控自演化系统”

ACGA 2 更偏向：

- Graph 边界治理
- Context / Descriptor / Effect / State Owner 管理

ACGA 3 应升级为：

- **Governed Autonomous Evolution**

即：

- AI 不是只按文档做事
- AI 可以提出更优实现、更优重构、更优局部架构
- 但所有演化必须经过受控的 `proposal -> verify -> compare -> promote / reject`

### 4.2 Polaris 的最终产品形态

Polaris 应逐步形成两个层次：

1. **Interactive Entry**
   - Chat / Dashboard / CLI / API
   - 供人类发出目标、查看状态、接管异常

2. **Autonomous Factory Core**
   - 常驻后台运行
   - 统一调度 AI/Agent/LLM 的编码、重构、验证、恢复与演化

### 4.3 同一个治理内核，两种运行模式

最推荐的形态不是“纯独立服务”或“纯内嵌模块”二选一，而是：

**同一个 Cell Governance Engine，支持两种运行模式**

1. **开发 / 单机场景**
   - 内嵌在 Polaris 进程内
   - 降低复杂度与延迟

2. **生产 / 多 Agent 场景**
   - 起本地守护进程
   - Polaris 与各 Agent 通过本地 API 或 MCP 访问同一治理内核

这个设计的目的，是避免：

- 过早分布式化
- 权威状态绑定在某个 agent session 内
- LLM 长会话背着整套元数据工作

---

## 5. 我们希望 AI 在工厂里增强什么能力

ACGA 3 不是为了“多做元数据”，而是为了增强 AI 在以下工程能力上的稳定性：

1. **写代码**
   - 更准确命中真正需要变更的范围

2. **改代码**
   - 在已有仓库中做局部修复、功能扩展和问题收敛

3. **重构代码**
   - 跨文件 rename、extract、move、split、merge

4. **优化算法与实现**
   - 自动提出多个候选版本，做 benchmark 与正确性比较

5. **演化局部架构**
   - 在受控门禁下推进 Cell 拆分、收敛、接口收口与投影调整

---

## 6. 产品边界

### 6.1 我们不做什么

Polaris 当前不应把自己定义为：

- 通用桌面自动化平台
- 通用知识工作 Agent
- 面向任意任务的开放式自治系统

Polaris 的主战场仍然是：

- Repo
- 文档
- 代码
- 测试
- 构建
- 审计

### 6.2 我们强调什么

Polaris 强调的是：

- 软件交付闭环
- 代码与架构边界
- 无人值守长跑
- 运行时可靠性
- 演化受控

---

## 7. 关键产品亮点

如果 ACGA 3 方向落地，Polaris 与传统编码产品相比的关键亮点将是：

1. **长期无人值守**
   - 不是一次 patch，而是持续运行的交付与演化流水线

2. **能力级上下文**
   - 不只是文件和片段，而是 Cell / Projection / Receipt 级别的上下文

3. **受控自演化**
   - 允许 AI 自动提出更优候选，但必须走比较和晋升闭环

4. **强审计与强回滚**
   - 任何一次 promotion 都有证据链、比较结果和回滚依据

5. **模型可替换**
   - 可接入不同 LLM / Agent 作为推理与执行引擎，系统价值不绑定单一模型

---

## 8. 当前诚实边界

截至当前，Polaris 已具备部分工厂化与治理化基础：

- PM / Director / QA / Dashboard 闭环
- Workflow Runtime / Runtime Projection / Audit / Context Catalog
- Factory Pipeline 的部分 projection / back-mapping 能力

但以下内容仍处于目标态或局部原型阶段：

- ACGA 3 统一的 Autonomous Factory Core
- 统一的 Promotion / Rollback Ledger
- 稳定的全链路 Runtime Cell Refs
- 以 ACGA 3 为中心的无人值守自演化工厂

因此当前更准确的表述是：

> Polaris 正在从“可治理的自动化开发指挥台”向“受控自演化的软件工厂控制面”演进。

---

## 9. 配套补充文档

- [ACGA 3 Autonomous Factory Spec](docs/architecture/ACGA_3_AUTONOMOUS_FACTORY_SPEC.md)
- [Backend ACGA 3 RFC](src/backend/docs/ACGA_3.0_RFC.md)
