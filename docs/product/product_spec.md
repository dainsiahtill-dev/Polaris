# Polaris 产品说明书

> **面向单人开发者的云端主模型 + 本地 SLM 协同 AI 自动化软件开发指挥台**
>
> 用"PM 规划 → Director 执行 → QA 校验 → Dashboard 可视化"的闭环，把"写代码"从聊天式辅助升级为**可回放、可追溯、可治理**的工程流水线。

---

## 📋 目录

- [产品定位](#-产品定位)
- [核心优势](#-核心优势护城河)
- [功能一览](#-已有功能一览)
- [行业对比](#-行业对比)
- [适合谁](#-适合谁)
- [设计原则](#-设计原则)
- [系统不变量](#️-系统不变量)

---

## 🎯 产品定位

Polaris 不是为了做成"万能通用 Agent 工具"，而是坚持**面向现实的取舍**：

### 固定成本优先（Reality-first Cost Model）

默认走 **Cloud/FIXED 主模型** + **本地 SLM 前置分流（`director_runtime`）**，在保证质量的前提下把边际成本压到可控范围。

### 可控优先（Control > Cleverness）

用合同与不变量约束系统行为，避免靠"模型自以为是"决定一切。

### 可复盘优先（Replayable by Design）

所有关键动作都有事实记录，失败能在少数跳数内定位到原因与证据。

### 一句话总结

> 🎯 一个"像工程系统一样运转"的个人软件工厂：**低边际成本、强约束可控、可回放可定位、能长期无人值守跑任务**。

### ACGA 3 补充定位

在现有产品定位之上，Polaris 的下一阶段目标不是继续堆叠“更聪明的聊天式编码助手”，而是朝以下方向增强：

- 让 AI/Agent/LLM 在无人值守工厂中持续写代码、改代码、重构代码
- 允许系统在受控门禁下持续演化更优的算法、实现与局部架构
- 把 Polaris 从“自动化开发指挥台”推进为“Autonomous Coding Control Plane”

补充文档：

- [ACGA 3 Factory Positioning](../../ACGA3_FACTORY_POSITIONING.md)
- [ACGA 3 Autonomous Factory Spec](../architecture/ACGA_3_AUTONOMOUS_FACTORY_SPEC.md)
- [Backend ACGA 3 RFC](../../src/backend/docs/ACGA_3.0_RFC.md)

诚实边界：

- 以上内容描述的是下一阶段产品与架构目标补充
- 不代表当前仓库已经完成 ACGA 3 全量落地
- 当前后端正式真相仍以 `src/backend/docs/graph/**`、`FINAL_SPEC.md` 与相关 `cell.yaml` 为准

---

## ✨ 核心优势（护城河）

### 1️⃣ 多 Agent 协作治理（唐朝官员制度）：把 Agent 变成"可管理的工程协作"

Polaris adopts a multi-Agent governance architecture with clearly defined role boundaries:

| Engineering Role | Role | Responsibility | CLI Entry |
|------|------|------|----------|
| PM | **PM** | Select direction, decompose tasks, write acceptance criteria | `scripts/pm/cli.py` |
| Architect | **Architect** | Architecture design, technology selection | `role_agent/architect_cli.py` |
| Chief Engineer | **Chief Engineer** | Technical analysis, code review | `role_agent/chief_engineer_cli.py` |
| Director | **Director** | Evidence gathering, changes, verification | `scripts/director/cli_thin.py` |
| QA | **QA** | Quality review, test verification | Factory/Pipeline integration |
| Scout | **Scout** | Concurrent read-only access layer (explore/search/summarize) | Coming soon (sub-agent) |

双方通过结构化 `PM_TASKS.json` 通信，并遵守"**合同不可变**"等约束，避免目标漂移。

> 💡 **关键区别**：Polaris 的多 Agent 是**治理协作**，不是并行协作——通过合同机制约束各角色边界。

### 2️⃣ 事实流 Append-Only + 可回放：长期无人值守的底座

- 关键动作写入 `events.jsonl`（追加写、不可覆盖）
- 用 `run_id` 串联产物与证据，支持回放、对比与归因
- 失败可在少数跳数内定位到：**Phase → Evidence → Tool Output**

### 3️⃣ 单人现实主义成本策略：固定成本驱动，而不是 token 崇拜

Polaris 的成本策略是"现实主义"的**三分法**：

| 通道           | 说明                                                     |
| -------------- | -------------------------------------------------------- |
| 🖥️ **LOCAL**   | 本地 SLM 前置分流（电费）——可选加速层（非 Director 主模型） |
| 📦 **FIXED**   | 包月/订阅 CLI —— Director 主模型优先通道                   |
| 💳 **METERED** | 按量计费 HTTPS API ——高质量主模型/紧急通道（默认强门禁）   |

> 💡 这让系统适合**长跑、迭代、无人值守**，而不会被 token 成本绑架。

### 4️⃣ 拟人化核心：Memory / Reflection / Persona / Inner Voice

Polaris 把"经验复用"工程化为四块能力：

| 模块                        | 能力                                                |
| --------------------------- | --------------------------------------------------- |
| **Memory（记忆）**          | 把关键事件、结论沉淀到长期记忆（向量检索）          |
| **Reflection（反思）**      | 从历史 run 归纳启发式规则，减少反复踩坑             |
| **Persona（人设）**         | 用角色风格与禁忌约束行为（PM/Director/QA 边界明确） |
| **Inner Voice（内心独白）** | 从模型输出中抽取思考摘要，展示"自言自语"            |

并通过 **Glass Mind（透明思维）** 在 UI 中展示：检索了哪些记忆、触发了哪些反思、上下文如何构建——让系统更可控、更可解释。

### 5️⃣ Mission Control Dashboard：强调观测与追踪，而不是"UI 直接决策"

- Dashboard **只读**，不直接改状态/改代码
- 侧边 **Process Monitor**、多维日志、结构化 **Smart 视图**解析
- 高性能虚拟滚动，适配超长日志与密集事件流

---

## ✅ 已有功能一览（按能力域）

### 工作流闭环

| 功能              | 说明                             |
| ----------------- | -------------------------------- |
| PM 生成任务合约   | 目标/AC/证据需求/策略覆盖        |
| Director 执行流程 | 取证 → 计划 → 改动 → 测试 → 汇总 |
| QA 报告与失败码   | 可扩展的验收机制                 |

### 产物与可观测性

| 文件                   | 用途                               |
| ---------------------- | ---------------------------------- |
| `PM_TASKS.json`        | 任务合约                           |
| `PLAN.md`              | 规划文档                           |
| `DIRECTOR_RESULT.json` | 执行结果                           |
| `QA_RESPONSE.md`       | QA 报告                            |
| `RUNLOG.md`            | 运行日志                           |
| `DIALOGUE.jsonl`       | 叙事流（含内心独白），适合 UI 展示 |
| `events.jsonl`         | 事实源，支持回放/分析              |
| `trajectory.json`      | 轨迹索引，产物与证据串联           |

### 工具链与工程化

| 能力                | 说明                     |
| ------------------- | ------------------------ |
| Repo 检索与切片读取 | ripgrep 封装、行范围读取 |
| 结构化编辑          | Tree-sitter AST 操作     |
| QA 工具链           | lint/type/test/validate  |
| 端口策略            | 风险门禁、回滚/修复策略  |

### 拟人化与透明思维

| 能力        | 说明                       |
| ----------- | -------------------------- |
| Memory      | 向量检索关键事件           |
| Reflection  | 归纳启发式规则             |
| Persona     | 角色风格与禁忌             |
| Inner Voice | 思考摘要抽取与展示         |
| Glass Mind  | 记忆检索与上下文构建可视化 |

### LLM 面试模式

- LLM 设置以“面试大厅 → 面试进行中”完成模型接入与胜任性测试。
- PM/Director 为核心岗位，必须通过 thinking/reasoning 检测才可上岗。
- QA/Docs 为辅助岗位，thinking 可选但会提示建议。
- 面试通过后，可将任意已配置模型绑定到岗位，不再强制固定到单一后端。

### 通知与外部通道（展望）

- 计划引入 WhatsApp / Telegram 等通知通道，用于实时推送任务阶段完成报告与关键事件摘要。

### 单人可控运行体验

| 特性             | 说明                                                                 |
| ---------------- | -------------------------------------------------------------------- |
| 运行产物隔离     | `.polaris/runtime/`（可指向 RAMDISK）                            |
| 主模型与分流分层 | Director 主模型优先 Cloud/FIXED；本地模型仅作可选 SLM，经 `director_runtime` 前置分流 |
| 订阅 CLI 增强    | Codex 作为固定成本的 PM                                              |

---

## 🆚 行业对比

Polaris 的差异化不是"更炫更全"，而是更像 **"可运营的工程系统"**。

### 与 IDE 一体化助手对比

**代表产品**：Codex (IDE/生态)、Cline、Trae、Windsurf、Claude Code、Continue 等

| 维度     | 他们通常强在哪里   | Polaris 的核心区别      |
| -------- | ------------------ | --------------------------- |
| 定位     | 编辑器内的智能助手 | **指挥台/流水线**，不是插件 |
| 交互模式 | 对话式辅助         | **合同闭环 + 事实流可回放** |
| 成本模型 | 通常按 token 计费  | **固定成本优先**            |
| 可追溯性 | 有限               | **失败 3 hops 可定位**      |

### 与云端交付型代理对比

**代表产品**：Devin 类、各类云沙箱 coding agent

| 维度     | 他们通常强在哪里    | Polaris 的核心区别       |
| -------- | ------------------- | ---------------------------- |
| 执行方式 | 并行/云执行/PR 交付 | **本地流程执行 + 可选云模型/单人长跑** |
| 成本结构 | 按使用量计费        | **可控 + 可复盘 + 成本边界** |
| 控制力   | 云端黑箱            | Dashboard 只读但**透明观测** |

### 与开源框架/平台对比

**代表产品**：OpenHands 等

| 维度     | 他们通常强在哪里         | Polaris 的核心区别               |
| -------- | ------------------------ | ------------------------------------ |
| 设计目标 | 生态与可扩展性、通用框架 | **更"现实主义"**                     |
| 功能范围 | 追求通用能力面           | 围绕软件开发闭环做**强约束与可观测** |

### 与通用型自动化 Agent 对比

**代表产品**：Manus 等

| 维度     | 他们通常强在哪里     | Polaris 的核心区别           |
| -------- | -------------------- | -------------------------------- |
| 覆盖范围 | 覆盖面广、任务类型多 | **更克制**                       |
| 设计原则 | 尽可能通用           | 只把"开发流水线"做到**稳定可控** |

### 一句话总结

> 💡 很多工具是"更聪明的助手/插件"，Polaris 是 **"更工程化的个人软件工厂"**。

---

## 👤 适合谁？

| 场景              | 说明                                                   |
| ----------------- | ------------------------------------------------------ |
| 💰 **成本敏感**   | 想把成本压到"电费/包月"级别，长期跑任务的人            |
| 🔄 **长周期项目** | 长周期个人项目，需要持续迭代、持续复盘、持续稳定的人   |
| 🔍 **工程化追求** | 追求"失败可定位、过程可回放、行为可约束"的工程化开发者 |
| 🤖 **无人值守**   | 想要无人值守，但不想把系统变成不可控黑箱的人           |

### 不适合谁

| 场景                 | 原因                                     |
| -------------------- | ---------------------------------------- |
| 追求"一句话搞定一切" | Polaris 强调**合同与约束**，不是魔法 |
| 需要多人协作         | 当前设计是**单人优先**                   |
| 追求云端弹性扩展     | 当前设计聚焦**单人流程治理与成本边界**（非云原生并行平台） |

---

## 🏗️ 设计原则

Polaris 的所有设计决策都遵循以下原则：

### Reality-Driven（面向现实）

基于真实成本和资源设计，而非理想模型。

### Tool-Augmented（工具增强）

用工具链（Lint/Test/RAG）补足中小模型（SLM）短板，并把高风险任务回退到主模型。

### Memory-over-IO（内存优先）

优先使用内存缓存（Repo Index），减少磁盘 IO。

### Control/Execute Separation（控制/执行分离）

PM 定义合约，Director 执行合约，Dashboard 旁路观测。

### Fact-First Narrative（事实优先叙事）

拟人化展示必须绑定事实（`run_id` / `events.jsonl`），禁止"编造成功"。

---

## ⚖️ 系统不变量

为了防止系统在长期迭代中失控，Polaris 遵循以下 **9 条不可打破的约束**：

### 1️⃣ 合同不可变 (Immutable Contract)

`PM_TASKS.json` 中的 `task goal` 和 `acceptance_criteria` 不允许 Director 或记忆模块改写。执行过程中只能追加 `evidence`，不能篡改原始目标。

### 2️⃣ 事实流 Append-Only

`events.jsonl` 只能追加，**严禁覆盖**。任何"修正"或"回滚"操作都必须产生新的 Event。

### 3️⃣ Run ID 全局唯一

所有产物、引用 (refs)、备忘录 (memo)、轨迹 (trajectory) 都必须以 `run_id` 为主键串联。

### 4️⃣ 观测优先 (UI Read-Only)

Dashboard UI **只读**。UI 不产生决策、不修改状态、不直接操作代码。

### 5️⃣ 可回放 (Replayable)

仅依靠 `events.jsonl` + `trajectory.json` + `artifacts paths` 必须能完全重建关键过程。

### 6️⃣ 失败可定位 (Traceable Failure)

任何失败都必须能在 **3 跳 (Hops)** 内定位到：Phase → Evidence → Tool Output。

### 7️⃣ 原子写入与一致性读取 (Atomic Writes)

关键状态文件必须使用原子写入策略，避免中断导致半截文件或状态破坏。

### 8️⃣ 记忆必须可溯源 (Memory with Refs)

Memory/Reflection 只能作为建议，必须带可回放的 refs 才能参与决策。

### 9️⃣ 编码统一性 (Encoding Uniformity)

所有文本读写必须显式使用 UTF-8，避免乱码破坏证据与回放。

---

## 📚 相关文档

| 文档                                    | 说明                                  |
| --------------------------------------- | ------------------------------------- |
| [架构文档](architecture.md)             | 状态机、事件模型、Policy 合并规则     |
| [拟人化设计](anthropomorphic_design.md) | Memory/Reflection/Persona/Inner Voice |
| [参考手册](reference.md)                | CLI 参数、工具清单、产物说明          |
| [根目录 README](../README.md)           | 快速开始指南                          |

---

_最后更新：2026-02-04_
