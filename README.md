<div align="center">

![Polaris Cyberpunk Banner](docs/assets/banner.png)

# 🏯 Polaris：天工开物 · 认知自动化开发工厂

**「垂拱而治」（Wu-wei Governance）·「事务内核」（Transaction Kernel）·「认知连续」（Cognitive Lifeform）**

*Polaris is not another chat-style coding agent. It is a transaction-governed software factory kernel for unattended, auditable, and recoverable AI software delivery.*

</div>

> **一句话看懂 Polaris 的野心：**
> 传统 Agent 把“继续还是停止”的控制权交给 LLM，本质上是模型主导的隐式递归，容易陷入死循环或幻觉；
> **Polaris 则把 LLM 降级为受限的决策组件。** 由系统内核接管执行、审计、预算、提交与停止，将 AI 从“聪明的对话助手”升级为**可无人值守、可追责、可回滚的工业级软件生产流水线**。

---

## 📜 目录

- [✨ 核心系统特色](#-核心系统特色)
- [🎯 为什么不只用 Codex / Claude Code？](#-为什么不只用-codex--claude-code)
- [🧠 哲学顶层：认知生命体架构](#-哲学顶层认知生命体架构)
- [👥 角色体系：三省六部制](#-角色体系三省六部制)
- [⚖️ 核心不变量（Kernel Invariants）](#-核心不变量kernel-invariants)
- [🚀 快速开始](#-快速开始)

---

## ✨ 核心系统特色

Polaris 并不是在堆砌 prompt 技巧，而是从操作系统和认知科学的维度，重构了 AI 编程的底层逻辑。我们拥有以下独家硬核特色：

### 🧱 1. 事务级内核 (Transaction Kernel)
告别传统 Agent 危险的 `while True` 无限循环。Polaris 将 AI 的每一次行动封装为**显式的、带边界的单回合事务（Turn Transaction）**。单次决策、单次工具批次、强制收口封印。不仅避免了 Token 爆仓，更让系统的每一步都精确可控。

### 🧠 2. 认知生命体架构 (Cognitive Lifeform)
我们不是在做一个“工具”，而是在培育一个“认知主体”。系统内置了**主控意识（Orchestrator）**决定流程走向，**工作记忆（StructuredFindings）**防止多轮任务中的“集体失忆”，以及**肌肉记忆（WorkflowRuntime）**自动处理“读-写-测”这种熟练闭环。

### ⚖️ 3. 极端的权责分离 (Checks & Balances)
引入中国古代“三省六部制”的分权制衡思想。**规划权、执行权、验收权彻底分离**。
PM 负责写合同，Chief Engineer 画架构蓝图，Director 严格按图施工，最后由 QA 进行无情的独立证据验收。彻底杜绝 AI “既当裁判又当运动员”的虚假成功。

### 💾 4. 三层真相记忆系统 (ContextOS)
摒弃脆弱的“聊天记录拼接”，采用企业级数据库级别的状态管理：
- **TruthLog**：Append-only 的最终事实流水，不可篡改的审计源。
- **Snapshot**：Turn 结束后的高保真稳定存档，随时支持 Time-travel 恢复重放。
- **WorkingState**：运行时上下文投影，保证 Prompt 永远是最精炼的精华。

### 🛡️ 5. 痛觉感知与自我保护 (Failure Taxonomy)
系统不仅知道“出错了”，更精确知道“哪里错、为什么错”。内置强大的异常分类学（心律失常/肌肉拉伤/海马体受损），结合 Continuation Policy 形成“痛觉保护”。超预算？遇到物理法则违规？系统会优雅地触发 Fail-closed，并保留现场等待人类介入或交接。

### 🔄 6. 既是工厂，也是工作台 (Dual Execution Modes)
虽然我们志在建立无人值守的自动化工厂，但我们绝不绑架用户。系统支持**双轨运行模式**：
- **流水线模式**：丢入需求，从 PM 规划到 QA 验收全链路自动走完。
- **单角色交互模式 (CLI)**：如果你只想找个强大的助手，你完全可以单独唤起 `Architect` 帮你设计架构，或者单独唤起 `Director` 帮你改 Bug，**此时的体验与传统的 Codex 或 Claude Code 完全一致**。

---

## 🎯 为什么不只用 Codex / Claude Code？

Codex 和 Claude Code 是目前最顶级的通用编码工作台，它们是**“超级工程师助手”**，强在极高的交互式生产力。

但 Polaris 的主战场不同，我们不仅能向下兼容作为**“交互式代码终端”**来使用，向上更能作为**“工程执行操作系统”**支撑复杂的任务生态。

| 维度 | Codex / Claude Code (通用智能助手) | Polaris (自动化开发工厂) |
|:---|:---|:---|
| **核心抽象** | 高生产力的交互式 Coding Agent | 事务驱动、流水线治理的软件工厂内核 |
| **执行流控制** | 强大的工具编排与模型自我闭环反馈 | 由内核接管状态机与物理法则，AI 仅受限决策 |
| **长期无人值守** | 依赖计划模式与 hooks，仍由模型主导驱动 | 默认物理停止，所有执行都在极度严苛的事务约束内 |
| **失败与恢复** | 回滚聊天历史或基于 Git 的 checkpoint | 事务级精确重放、幂等重试（Idempotency）、回执接管 |
| **审计与追责** | 记录松散的执行日志与命令输出 | 每 turn 提供医疗级诊断报告（`TurnOutcomeEnvelope`） |
| **组织与治理** | 灵活的 Subagents / 技能池扩展 | PM / Director / QA 权力硬隔离，彻底杜绝自说自话 |

**简而言之：Codex/Claude Code 强在“快速把活干出来”；而 Polaris 强在“把活可控、可追责、可持续、无人值守地干出来”。**

---

## 🧠 哲学顶层：认知生命体架构

在 Polaris 的工程实现里，每一个核心模块都能在生物认知科学中找到对应的器官映射。这不是华而不实的比喻，而是我们保证系统稳定演进的底层哲学：

* **主控意识 (前额叶皮层)** ➡️ `RoleSessionOrchestrator`：裁决“此刻该做什么”。
* **工作记忆 (小纸条)** ➡️ `StructuredFindings`：跨 Turn 认知传递，防止任务切换时失忆。
* **海马体 (记忆固化)** ➡️ `ContextOS` + `Commit Protocol`：确保每次提交都是真实发生的 Durable Truth。
* **肌肉记忆 (小脑)** ➡️ `DevelopmentWorkflowRuntime`：封装套路化的开发反射动作。
* **心脏 (神经放电)** ➡️ `TurnTransactionController`：不可逆的单次心跳控制。
* **生理防线 (痛觉本能)** ➡️ `FailureTaxonomy` + `ContinuationPolicy`：提供物理级熔断与自我保护。

---

## 👥 角色体系：三省六部制

我们以中国古代国家机器的高效治理结构，重塑了 AI 开发流程，让混乱的 Agent 协作变得井然有序：

| 角色（今名） | 角色（古名） | 代码落地 (src/backend/polaris/cells/) | 职掌与权力边界 |
| :----------- | :----------- | :---- | :---- |
| **Human Admin** | 天子 | `delivery/` (API/CLI 入口) | 设定项目愿景与全局预算，不干预具体代码执行。 |
| **Architect** | 中书令 | `cells/architect/` | 顶层架构师，起草全局设计规格（`spec.md`）。 |
| **PM** | 尚书令 | `delivery/cli/pm/` | 项目经理，负责理解需求，下发明确的任务合同（`PM_TASKS.json`）。 |
| **Chief Engineer** | 工部尚书 | `cells/chief_engineer/` | 技术大拿，根据任务设计具体施工蓝图（Blueprint），**但不写实现代码**。 |
| **Director** | 工部侍郎/工匠| `cells/director/` | 实际干活的执行者，严格按蓝图写代码、调工具。 |
| **QA / Auditor**| 门下侍中 | `cells/qa/` | 独立裁判，基于客观证据执行验收，拥有**绝对的封驳权**（一票否决）。 |
| **FinOps** | 户部尚书 | `cells/finops/` | 统管全场 Token 预算与工具副作用消耗，防止资产流失。 |

---

## ⚖️ 核心不变量（Kernel Invariants）

系统能长久稳定运行，靠的是铁的纪律。以下是 Polaris 绝对不可侵犯的物理法则：

1. **单 Turn 单决策**：一个 Turn 只能产生唯一的 Canonical Decision，绝不允许 AI 既想写代码又想重启服务。
2. **单批次工具执行**：一个 Turn 最多执行一个 Tool Batch，严禁隐藏的连续循环调度。
3. **收口期绝对封印**：进入总结收口（Finalization）阶段后，系统将从底层彻底屏蔽工具调用能力，防止“假总结、真循环”。
4. **三段式提交纪律（Commit Protocol）**：必须通过 Pre-commit 验证 → Atomic 写入 → Post-commit 封印。未 Commit 的内容一律视为未发生的临时幻觉。
5. **Fail-Closed 熔断**：宁可报错停机，绝不带病狂奔。物理法则被打破、记忆写入失败时，立刻中止执行并上报。

---

## 🧩 深度全景：核心架构与功能矩阵 (Features Matrix)

基于对 Polaris 底层规范（ACGA 2.0、KernelOne、Cell Evolution 等）的深度审计，系统已实装或正全面迁移至以下企业级核心能力：

### 🧬 1. Cell 波粒二象性架构 (Cell Wave-Particle Duality)
Polaris 内部不把代码当成单纯的“文件夹”，而是作为高维的内部架构 IR（中间表示）：
* **Wave for Discovery（波态发现）**：负责语义检索、聚类、演化候选和上下文压缩，让 AI 能够“联想”和“探索”。
* **Particle for Truth（粒态契约）**：负责严格的契约、边界、状态拥有权（Single State Owner）和副作用裁决，让 AI “守规矩”。
* **Projection for Delivery（物理投影）**：用户最终看到的传统工程文件，只是高维 Cell 在文件系统上的物理投影。

### 💻 2. KernelOne：Agent 的 Linux 运行时底座
我们剥离了业务逻辑，在 `src/backend/polaris/kernelone/` 目录下打造了纯粹面向 AI 的操作系统底座（OS Substrate）：
* 提供统一的底层技术抽象：原生文件系统 (`fs`)、数据库 (`db`)、流式通信 (`ws`/`stream`)、跨进程锁 (`locks`)、甚至专用于 AI 的记忆压缩（`context_compaction`）。
* 让所有 AI 生成的代码和动作，都必须通过底座的标准系统调用（Syscalls）执行，从而实现**所有的副作用全部显式化、可审计（Explicit Effects）**。

### 🕸️ 3. Graph-First 的图谱治理
拒绝让 AI 靠向量检索（Embedding）瞎猜架构边界。
* **唯一架构真相**：系统的真实边界由 `cells.yaml` 和 `subgraphs` 严格声明。
* **先契约后检索**：向量检索只能在 Graph 约束出的合法候选集合内排序，彻底杜绝越权访问和架构腐化。

### 📦 4. 结构化降噪上下文 (Descriptor over Raw Source)
传统 Agent 喜欢把几万行源码直接扔给 LLM，导致严重幻觉。
* Polaris 强制要求先生成**结构化的 Descriptor** 和 **Context Pack**。
* LLM 检索时只吃小巧、精准的描述符，工作时才按需加载最小必需的上下文（Context Slicing），最大化利用 Token 预算（Budget Control）。

### 🏭 5. 异步任务集市 (EDA Task Market)
彻底打破了多 Agent 协作间的同步 RPC 阻塞灾难。
* **不再“手拉手”干活**：PM 不再同步等待 Director 完成，而是将 `PM_TASKS.json` 投递至全局的 `Task Market`（任务集市）。
* **基于 Lease 的抢占执行**：Chief Engineer 和 Director 通过 `Pull (Claim + Lease) -> Compute -> Push (Ack + Fact)` 模型异步抢占任务。
* **高容错与死信队列**：内置 `Visibility Timeout` 自动回收、`DLQ`（死信队列）以及 `HITL`（人类介入）链路。即使某个 Agent 完全崩溃，任务也会在租期失效后被其他 Agent 自动接管，保障流水线绝对的可用性。

### 🧠 6. 认知演化与偏误防御
内置了极其丰富的**反思层（Reflection Layer）**与**元认知监控（Meta-Cognition）**：
* 强制要求 AI 在得出结论前执行“六问”批判性思维（假设检验、反事实推演）。
* 内置对确认偏误（Confirmation Bias）、可得性启发（Availability Heuristic）的主动防御策略，让 AI 优雅地表达“我不知道”，并主动索取信息，而不是不懂装懂。

---

## 🛠️ 底层技术栈与工程化能力 (Engineering Capabilities)

除了顶层架构哲学，Polaris 已经在代码层面落地了大量企业级的底层工程能力，完全避免了“重复造轮子”：

* **🔌 全面兼容 MCP 协议 (Model Context Protocol)**：内置完善的 MCP Client，支持 stdio 与 HTTP 模式，可无缝接入海量外部工具集与上下文资源。
* **🌳 语法级精确代码解析 (Tree-sitter)**：不依赖脆弱的正则替换，底层使用 Tree-sitter 构建 AST（抽象语法树），实现符号级精确定位、安全重命名和节点级代码替换。
* **⚡ 实时事件流与 WebSocket (Runtime WS)**：内核运行时与可视化 Dashboard 之间通过原生 WebSocket 通信，提供毫秒级的状态推送、心跳保活与全息执行监控。
* **🛠️ 智能错误恢复与熔断 (Error Classifier)**：内置先进的运行时错误分类器，精确区分“可重试错误”与“致命错误”，配合指数退避（Exponential Backoff）和熔断机制，保障系统能在无人值守下安全自愈。
* **📝 多级代码编辑策略**：支持 `tool_first`、`precision_edit`、`repo_apply_diff` 等多种修改模式，兼顾大范围重构与细粒度语法树修补。
* **📊 深度代码依赖分析**：能够基于工作区动态生成代码依赖拓扑图，进行复杂的代码度量与复杂度分析。
* **🏊 多实例防冲突并发池 (Director Pool)**：Chief Engineer 能够通过内置的 `DirectorPool` 并发调度多个 Director 实例干活。底层具备**全局文件冲突检测（ScopeConflictDetector）**，防止多个 Agent 同时修改同一个文件，并支持实时的状态监控面板与崩溃后的自动重新指派（Reassign/Split/Abort）。
* **🤖 Playwright 浏览器自动化**：原生支持通过 Playwright 驱动浏览器进行端到端（E2E）UI 测试与复杂交互，验证最终代码成果。

---

## 🚀 快速开始

### 1. 环境配置

```bash
# 一键安装 Node + Python 依赖
npm run setup:dev

# 或手动初始化 Python 虚拟环境
# Windows: infrastructure\setup\setup_venv.bat
# Linux/macOS: bash infrastructure/setup/setup_venv.sh
```

### 2. 启动可视化观察台 (Dashboard)

我们提供了一个强大的 React + Electron 桌面应用，用于全息监控这个认知生命体的每一次心跳和思维轨迹：
```bash
npm run dev
```

### 3. 命令行触发流水线与独立工具

**🤖 模式一：端到端全自动流水线（任务规划 → 蓝图 → 施工 → 验收）**
```bash
.venv\Scripts\python src\backend\scripts\pm\cli.py --workspace /path/to/repo --run-director
```

**👨‍💻 模式二：交互式单角色工作台 (类似 Claude Code)**
如果你只想把 Polaris 当作某个特定领域的助手，可以直接运行对应的角色终端：

```bash
# 唤起顶层架构师为你单独设计技术规格
python -m polaris.delivery.cli.architect.cli --mode interactive --workspace .

# 唤起工程大拿为你单独做代码审查和方案推演
python -m polaris.delivery.cli.chief_engineer.cli --mode interactive --workspace .

# 仅限 Director 施工队按照你给定的任务快速开干
python -m polaris.delivery.cli.director.cli --workspace . --iterations 1
```

### 4. 架构进阶阅读

如果您希望深入了解 Polaris 从“执行工具”向“认知生命体”进化的硬核细节，请阅读：
- 核心白皮书：[`src/backend/docs/polaris-system-whitepaper.md`](src/backend/docs/polaris-system-whitepaper.md)
- 认知生命体设计稿：[`src/backend/docs/核心重塑：认知生命体.md`](src/backend/docs/%E6%A0%B8%E5%BF%83%E9%87%8D%E5%A1%91%EF%BC%9A%E8%AE%A4%E7%9F%A5%E7%94%9F%E5%91%BD%E4%BD%93.md)

---

<div align="center">

## 许可协议

MIT License

</div>
