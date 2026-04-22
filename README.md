# 🏯 Polaris：天工开物 · 认知自动化开发工厂

**「垂拱而治」（Wu-wei Governance）·「事务内核」（Transaction Kernel）·「认知连续」（Cognitive Lifeform）**

*Polaris is not another chat-style coding agent. It is a transaction-governed software factory kernel for unattended, auditable, and recoverable AI software delivery.*

---

## 一句话看懂 Polaris 的野心

传统 Agent 把"继续还是停止"的控制权交给 LLM，本质上是模型主导的隐式递归，容易陷入死循环或幻觉；
**Polaris 则把 LLM 降级为受限的决策组件。** 由系统内核接管执行、审计、预算、提交与停止，将 AI 从"聪明的对话助手"升级为**可无人值守、可追责、可回滚的工业级软件生产流水线**。

---

## 📜 目录

- [✨ 核心系统特色](#-核心系统特色)
- [🎯 为什么不只用 Codex / Claude Code？](#-为什么不只用-codex--claude-code)
- [🧠 哲学顶层：认知生命体架构](#-哲学顶层认知生命体架构)
- [👥 角色体系：三省六部制](#-角色体系三省六部制)
- [⚖️ 核心不变量（Kernel Invariants）](#-核心不变量kernel-invariants)
- [🧩 深度全景：核心架构与功能矩阵](#-深度全景核心架构与功能矩阵)
- [🛠️ 底层技术栈与工程化能力](#-底层技术栈与工程化能力)
- [🧪 测试与质量保障体系](#-测试与质量保障体系)
- [🏛️ Cell 架构生态（51+ Cells）](#-cell-架构生态51-cells)
- [🚀 快速开始](#-快速开始)

---

## ✨ 核心系统特色

Polaris 并不是在堆砌 prompt 技巧，而是从操作系统和认知科学的维度，重构了 AI 编程的底层逻辑。我们拥有以下独家硬核特色：

### 🧱 1. 事务级内核 (Transaction Kernel)
告别传统 Agent 危险的 `while True` 无限循环。Polaris 将 AI 的每一次行动封装为**显式的、带边界的单回合事务（Turn Transaction）**。单次决策、单次工具批次、强制收口封印。不仅避免了 Token 爆仓，更让系统的每一步都精确可控。

### 🧠 2. 认知生命体架构 (Cognitive Lifeform)
我们不是在做一个"工具"，而是在培育一个"认知主体"。系统内置了**主控意识（Orchestrator）**决定流程走向，**工作记忆（StructuredFindings）**防止多轮任务中的"集体失忆"，以及**肌肉记忆（WorkflowRuntime）**自动处理"读-写-测"这种熟练闭环。

### ⚖️ 3. 极端的权责分离 (Checks & Balances)
引入中国古代"三省六部制"的分权制衡思想。**规划权、执行权、验收权彻底分离**。
PM 负责写合同，Chief Engineer 画架构蓝图，Director 严格按图施工，最后由 QA 进行无情的独立证据验收。彻底杜绝 AI "既当裁判又当运动员"的虚假成功。

### 💾 4. 四层真相记忆系统 (ContextOS)
摒弃脆弱的"聊天记录拼接"，采用企业级数据库级别的状态管理：
- **TruthLog**：Append-only 的最终事实流水，不可篡改的审计源。
- **WorkingState**：运行时上下文投影，保证 Prompt 永远是最精炼的精华。
- **ReceiptStore**：大文件输出引用而非重复存储。
- **ProjectionEngine**：只读投影生成，与数据平面严格隔离。

### 🛡️ 5. 痛觉感知与自我保护 (Failure Taxonomy)
系统不仅知道"出错了"，更精确知道"哪里错、为什么错"。内置强大的异常分类学（心律失常/肌肉拉伤/海马体受损），结合 Continuation Policy 形成"痛觉保护"。超预算？遇到物理法则违规？系统会优雅地触发 Fail-closed，并保留现场等待人类介入或交接。

### 🔄 6. 既是工厂，也是工作台 (Dual Execution Modes)
虽然我们志在建立无人值守的自动化工厂，但我们绝不绑架用户。系统支持**双轨运行模式**：
- **流水线模式**：丢入需求，从 PM 规划到 QA 验收全链路自动走完。
- **单角色交互模式 (CLI)**：如果你只想找个强大的助手，你完全可以单独唤起 `Architect` 帮你设计架构，或者单独唤起 `Director` 帮你改 Bug，**此时的体验与传统的 Codex 或 Claude Code 完全一致**。

### 🧬 7. Cell 波粒二象性架构
Polaris 内部不把代码当成单纯的"文件夹"，而是作为高维的内部架构 IR（中间表示）：
- **Wave for Discovery（波态发现）**：负责语义检索、聚类、演化候选和上下文压缩，让 AI 能够"联想"和"探索"。
- **Particle for Truth（粒态契约）**：负责严格的契约、边界、状态拥有权（Single State Owner）和副作用裁决，让 AI "守规矩"。
- **Projection for Delivery（物理投影）**：用户最终看到的传统工程文件，只是高维 Cell 在文件系统上的物理投影。

### 💻 8. KernelOne：Agent 的 Linux 运行时底座
在 `src/backend/polaris/kernelone/` 目录下打造了纯粹面向 AI 的操作系统底座（OS Substrate）：
- **文件系统抽象**：`KernelFileSystemAdapter` 协议，支持原子写入、UTF-8 强制、路径边界隔离
- **数据库抽象**：`KernelSQLiteAdapterPort` / `KernelSQLAlchemyAdapterPort` / `KernelLanceDbAdapterPort` 多适配器
- **分布式锁**：`LockPort` 协议，支持 TTL、分布式抢占、自旋重试
- **WebSocket 会话管理**：`WsSessionPort` 协议，支持内存/Redis多模式
- **副作用追踪**：`Effect` + `EffectReceipt` 完整链路审计
- **车道式并发**：`ExecutionRuntime` 按 ASYNC_IO / BLOCKING_IO / SUBPROCESS 三车道隔离执行

### 🕸️ 9. Graph-First 图谱治理
拒绝让 AI 靠向量检索（Embedding）瞎猜架构边界。
- **唯一架构真相**：系统的真实边界由 `cells.yaml` 和 `subgraphs` 严格声明
- **先契约后检索**：向量检索只能在 Graph 约束出的合法候选集合内排序
- **Fitness Rules**：50+ 架构约束规则，CI 自动化执行

### 📦 10. 结构化降噪上下文 (Descriptor over Raw Source)
传统 Agent 喜欢把几万行源码直接扔给 LLM，导致严重幻觉。
- **Descriptor Pack**：结构化的代码描述符，替代原始源码喂给 LLM
- **Context Pack**：按需加载的最小上下文切片（Context Slicing）
- **Budget Guard**：读文件前检查行数预算，超限拒绝

### 🏭 11. 异步任务集市 (EDA Task Market)
彻底打破了多 Agent 协作间的同步 RPC 阻塞灾难。
- **Task Market**：PM 将任务合同投递至全局任务集市
- **Lease 抢占执行**：Claim + Lease -> Compute -> Ack + Fact 异步模型
- **死信队列 DLQ**：Visibility Timeout 自动回收 + 人工介入链路

### 🧠 12. 认知演化与偏误防御
内置了极其丰富的**反思层（Reflection Layer）**与**元认知监控（Meta-Cognition）**：
- 强制"六问"批判性思维
- 确认偏误、可得性启发防御策略
- AI 主动表达"我不知道"

---

## 🎯 为什么不只用 Codex / Claude Code？

| 维度 | Codex / Claude Code (通用智能助手) | Polaris (自动化开发工厂) |
|:---|:---|:---|
| **核心抽象** | 高生产力的交互式 Coding Agent | 事务驱动、流水线治理的软件工厂内核 |
| **执行流控制** | 强大的工具编排与模型自我闭环反馈 | 由内核接管状态机与物理法则，AI 仅受限决策 |
| **长期无人值守** | 依赖计划模式与 hooks，仍由模型主导驱动 | 默认物理停止，所有执行都在极度严苛的事务约束内 |
| **失败与恢复** | 回滚聊天历史或基于 Git 的 checkpoint | 事务级精确重放、幂等重试（Idempotency）、回执接管 |
| **审计与追责** | 记录松散的执行日志与命令输出 | 每 turn 提供医疗级诊断报告（`TurnOutcomeEnvelope`） |
| **组织与治理** | 灵活的 Subagents / 技能池扩展 | PM / Director / QA 权力硬隔离，彻底杜绝自说自话 |

**简而言之：Codex/Claude Code 强在"快速把活干出来"；而 Polaris 强在"把活可控、可追责、可持续、无人值守地干出来"。**

---

## 🧠 哲学顶层：认知生命体架构

在 Polaris 的工程实现里，每一个核心模块都能在生物认知科学中找到对应的器官映射：

| 抽象概念 | 工程实体 | 代码路径 |
|---------|---------|---------|
| **主控意识 (前额叶皮层)** | `RoleSessionOrchestrator` | `polaris/cells/roles/runtime/internal/session_orchestrator.py` |
| **工作记忆 (小纸条)** | `StructuredFindings` | `polaris/domain/cognitive_runtime/models.py` |
| **海马体 (记忆固化)** | `ContextOS` + `Commit Protocol` | `polaris/kernelone/context/context_os/runtime.py` |
| **肌肉记忆 (小脑)** | `DevelopmentWorkflowRuntime` | `polaris/cells/roles/kernel/internal/development_workflow_runtime.py` |
| **心脏 (神经放电)** | `TurnTransactionController` | `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` |
| **直觉预感 (神经预激)** | `StreamShadowEngine` | `polaris/cells/roles/kernel/internal/stream_shadow_engine.py` |
| **生理防线 (痛觉本能)** | `ContinuationPolicy` + `KernelGuard` | `polaris/cells/roles/runtime/internal/continuation_policy.py` |

---

## 👥 角色体系：三省六部制

| 角色（今名） | 角色（古名） | 代码落地 | 职掌与权力边界 |
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

系统能长久稳定运行，靠的是铁的纪律：

1. **单 Turn 单决策**：`len(TurnDecisions) == 1`，绝不允许 AI 既想写代码又想重启服务
2. **单批次工具执行**：`len(ToolBatches) <= 1`，严禁隐藏的连续循环调度
3. **无隐藏连续**：`hidden_continuation == 0`，状态轨迹禁止非法循环
4. **收口期绝对封印**：Finalization 阶段从底层彻底屏蔽工具调用
5. **三段式提交纪律**：Pre-commit 验证 → Atomic 写入 → Post-commit 封印
6. **Fail-Closed 熔断**：宁可报错停机，绝不带病狂奔
7. **最大自动回合**：`turn_count <= max_auto_turns`，超限必须停止
8. **停滞检测**：artifact hash 未变化且无 speculative hints 时强制终止
9. **重复失败熔断**：连续 3 个 Turn 发生相同错误时强制终止

---

## 🧩 深度全景：核心架构与功能矩阵

### 💻 KernelOne：AI 的 OS Substrate

| 抽象层 | 核心组件 | 文件路径 |
|--------|---------|---------|
| **文件系统** | `KernelFileSystem` + `KernelFileSystemAdapter` | `polaris/kernelone/fs/` |
| **数据库** | `KernelSQLiteAdapter` / `KernelSQLAlchemyAdapter` / `KernelLanceDbAdapter` | `polaris/kernelone/db/` |
| **分布式锁** | `FileLockAdapter` / `SQLiteLockAdapter` / `RedisLockAdapter` | `polaris/kernelone/locks/` |
| **WebSocket** | `WsSessionPort` + `InMemorySessionManager` / `RedisSessionManager` | `polaris/kernelone/ws/` |
| **副作用追踪** | `EffectTrackerImpl` + `EffectReceipt` 审计链 | `polaris/kernelone/effect/` |
| **执行运行时** | `ExecutionRuntime` (ASYNC/BLOCKING/SUBPROCESS 三车道) | `polaris/kernelone/runtime/` |
| **存储布局** | 3层 PathResolver (RAMDISK/WORKSPACE/GLOBAL) | `polaris/kernelone/storage/` |
| **上下文压缩** | `ContextCompaction` primitives | `polaris/kernelone/context/compaction.py` |

### 💾 ContextOS 四层架构

| 层级 | 组件 | 职责 |
|------|------|------|
| **TruthLog** | `TruthLogService` | Append-only 最终事实流水 |
| **WorkingState** | `WorkingStateManager` | 运行时可变状态投影 |
| **ReceiptStore** | `ReceiptStore` | 大文件引用存储 (>500字符) |
| **Projection** | `ProjectionEngine` | 只读 Prompt 生成 |

### 🧠 Akashic Memory Engine 四层记忆

| 层级 | 组件 | 职责 |
|------|------|------|
| **Working** | `WorkingMemoryWindow` | 短时记忆，Token 预算控制 |
| **Episodic** | `EpisodicMemoryStore` | Session 级记忆固化 |
| **Semantic** | `SemanticMemoryStore` | 长时向量检索记忆 |
| **Cache** | `SemanticCacheInterceptor` | 语义缓存加速 |

**Tier 协调**：支持跨层提升/降级，带完整事务回滚语义

### 🧬 Cell 生态（59 Cells）

详见 [Cell 架构生态章节](#cell-架构生态51-cells)

### 🔄 StreamShadowEngine 推测引擎

跨 Turn 推测执行，实现"思考"与"行动"时间重叠：
- ADOPT / JOIN / CANCEL / REPLAY 语义
- ShadowTaskRegistry 推测任务注册
- SpeculationResolver 推测结果决议

---

## 🛠️ 底层技术栈与工程化能力

### 协议与接口

| 能力 | 描述 | 证据路径 |
|------|------|---------|
| **MCP 协议** | Model Context Protocol stdio/HTTP 双模式 | `polaris/infrastructure/llm/providers/codex_output_parser.py` |
| **Tree-sitter AST** | 符号级精确定位、安全重命名 | `polaris/infrastructure/code_intelligence/` |
| **Protocol 协议** | 197+ `@runtime_checkable` Protocol 定义 | 全局搜索 |

### 运行时能力

| 能力 | 描述 | 证据路径 |
|------|------|---------|
| **WebSocket 实时推送** | 毫秒级状态推送、心跳保活 | `polaris/delivery/ws/` |
| **错误分类器** | 可重试 vs 致命错误 + 熔断器 | `polaris/infrastructure/llm/providers/provider_helpers.py` |
| **多级编辑策略** | tool_first / precision_edit / repo_apply_diff | `prompts/generic.json` |
| **代码依赖分析** | 动态生成依赖拓扑图 | `polaris/infrastructure/code_intelligence/` |
| **Director Pool** | 多实例并发 + ScopeConflictDetector 防冲突 | `polaris/cells/runtime/task_market/` |
| **Playwright E2E** | 浏览器自动化 UI 测试 | `tests/electron/` |

### 存储与消息

| 能力 | 描述 | 证据路径 |
|------|------|---------|
| **NATS/JetStream** | 自愈流 + 自动重建 | `polaris/infrastructure/messaging/nats/client.py` |
| **SQLite + JSONL** | 原子写入 + 日志追加 | `polaris/infrastructure/storage/adapter.py` |
| **LanceDB 向量搜索** | 语义代码搜索 (80行/块) | `polaris/infrastructure/db/repositories/lancedb_code_search.py` |
| **Workspace 隔离** | State 存储在 workspace 外部 | `polaris/infrastructure/persistence/state_store.py` |

### DI 与服务组装

| 能力 | 描述 | 证据路径 |
|------|------|---------|
| **DI Container** | 异步单例/瞬态工厂 + 双检锁 | `polaris/infrastructure/di/container.py` |
| **DIContainerScope** | 测试隔离 + 全局状态重置 | `polaris/infrastructure/di/scope.py` |
| **MessageBus** | 核内事件总线 | `polaris/kernelone/events/message_bus.py` |
| **Service Assembly** | 9阶段引导链 | `polaris/bootstrap/assembly.py` |

---

## 🧪 测试与质量保障体系

### 测试框架

| 框架 | 用途 | 配置 |
|------|------|------|
| **pytest** | 主测试框架 | `pyproject.toml` asyncio_mode=auto |
| **Playwright** | E2E 浏览器自动化 | `playwright.config.ts` |
| **Vitest** | 前端 JS/TS 测试 | `vite.config.ts` |

### E2E 测试

```bash
npm run test:e2e                 # 全链路 Electron E2E
npm run test:e2e:panel           # Panel 回归测试
npm run test:e2e:task           # 自然语言 Panel 任务
npm run test:e2e:hybrid          # Playwright + Computer Use 混合
```

**测试文件**：`tests/electron/` (app.spec.ts, full-chain-audit.spec.ts, pm-director-real-flow.spec.ts 等)

### 压力测试

```bash
python scripts/run_agent_headless_stress.py --workspace .  # 无头压测
```

**测试维度**：提示词穿透、输出格式合规、内容质量评分、边界条件处理、角色协作一致性

### 基准测试

| 类型 | 文件路径 |
|------|---------|
| **延迟基准** | `polaris/kernelone/benchmark/latency.py` (p50/p90/p95/p99) |
| **吞吐基准** | `polaris/kernelone/benchmark/throughput.py` |
| **内存基准** | `polaris/kernelone/benchmark/memory.py` |
| **混沌工程** | `polaris/kernelone/benchmark/chaos/` (deadlock, rate limiting) |
| **VCR 录制回放** | `polaris/kernelone/benchmark/reproducibility/` |

### 质量门禁

| 门禁 | 描述 |
|------|------|
| **Ruff** | 代码规范检查 + 自动修复 |
| **Mypy** | 严格静态类型检查 |
| **Coverage** | 覆盖率门禁 |
| **Fitness Rules** | 50+ 架构约束规则 |
| **Catalog Governance** | 9阶段 CI 流水线 |

---

## 🏛️ Cell 架构生态（51+ Cells）

### Cell 类型

| 类型 | 说明 |
|------|------|
| **capability** | 单一职责能力单元 |
| **workflow** | 多步骤工作流 |
| **policy** | 策略执行与门禁 |
| **projection** | 只读状态投影 |

### 核心 Cell 一览

#### 上下文与知识
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `context.catalog` | 构建 Descriptor Card | `polaris/cells/context/catalog/` |
| `context.engine` | 角色执行上下文组装 | `polaris/cells/context/engine/` |
| `cognitive.knowledge_distiller` | 模式提炼与检索 | `polaris/cells/cognitive/knowledge_distiller/` |

#### 运行时核心
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `runtime.state_owner` | 单写源真管理 | `polaris/cells/runtime/state_owner/` |
| `runtime.task_runtime` | 任务生命周期 | `polaris/cells/runtime/task_runtime/` |
| `runtime.task_market` | 异步任务集市 | `polaris/cells/runtime/task_market/` |
| `runtime.execution_broker` | 统一执行提交 | `polaris/cells/runtime/execution_broker/` |
| `runtime.projection` | 只读状态投影 | `polaris/cells/runtime/projection/` |

#### 编排层
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `orchestration.pm_planning` | PM 任务合同生成 | `polaris/cells/orchestration/pm_planning/` |
| `orchestration.pm_dispatch` | PM 合同分发 | `polaris/cells/orchestration/pm_dispatch/` |
| `orchestration.workflow_runtime` | 工作流引擎 | `polaris/cells/orchestration/workflow_runtime/` |

#### LLM 层
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `llm.control_plane` | LLM 边界组合 | `polaris/cells/llm/control_plane/` |
| `llm.dialogue` | 对话编排 | `polaris/cells/llm/dialogue/` |
| `llm.evaluation` | LLM 评测 | `polaris/cells/llm/evaluation/` |
| `llm.provider_runtime` | Provider 运行时 | `polaris/cells/llm/provider_runtime/` |
| `llm.tool_runtime` | 工具调用编排 | `polaris/cells/llm/tool_runtime/` |

#### 角色系统
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `roles.runtime` | 角色运行时组合 | `polaris/cells/roles/runtime/` |
| `roles.kernel` | 角色执行内核 | `polaris/cells/roles/kernel/` |
| `roles.session` | 角色会话生命周期 | `polaris/cells/roles/session/` |
| `roles.engine` | 角色引擎策略 | `polaris/cells/roles/engine/` |
| `roles.profile` | 角色配置注册 | `polaris/cells/roles/profile/` |

#### Director 管道
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `director.execution` | Director 执行工作流 | `polaris/cells/director/execution/` |
| `director.planning` | Director 任务规划 | `polaris/cells/director/planning/` |
| `director.tasking` | Director 任务生命周期 | `polaris/cells/director/tasking/` |
| `director.runtime` | 代码/补丁应用 | `polaris/cells/director/runtime/` |
| `director.delivery` | Director CLI 传输 | `polaris/cells/director/delivery/` |

#### 审计与归档
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `audit.evidence` | 运行时证据事件 | `polaris/cells/audit/evidence/` |
| `audit.diagnosis` | 审计失败诊断 | `polaris/cells/audit/diagnosis/` |
| `audit.verdict` | 验收裁决 | `polaris/cells/audit/verdict/` |
| `archive.run_archive` | 运行归档 | `polaris/cells/archive/run_archive/` |
| `archive.task_snapshot_archive` | 任务快照归档 | `polaris/cells/archive/task_snapshot_archive/` |

#### 策略层
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `policy.workspace_guard` | 工作区路径合法性 | `polaris/cells/policy/workspace_guard/` |
| `policy.permission` | 角色能力矩阵 | `polaris/cells/policy/permission/` |
| `policy.protocol` | 协议状态机 | `polaris/cells/policy/protocol/` |

#### Factory 系统
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `factory.pipeline` | 工厂执行工作流 | `polaris/cells/factory/pipeline/` |
| `factory.cognitive_runtime` | 跨角色运行时权威 | `polaris/cells/factory/cognitive_runtime/` |
| `factory.verification_guard` | 工厂输出验收门禁 | `polaris/cells/factory/verification_guard/` |

#### 其他关键 Cell
| Cell ID | 职责 | 路径 |
|---------|------|------|
| `resident.autonomy` | 长期自主运行 | `polaris/cells/resident/autonomy/` |
| `finops.budget_guard` | Token 预算控制 | `polaris/kernelone/runtime/usage_metrics.py` |
| `events.fact_stream` | 运行时事实流 | `polaris/kernelone/events/` |
| `storage.layout` | 存储布局解析 | `polaris/cells/storage/layout/` |
| `kernelone.core` | ContextOS + WorkflowEngine | `polaris/kernelone/context/context_os/` |
| `kernelone.traceability` | 执行节点溯源 | `polaris/kernelone/traceability/` |

### Cell 架构模式

1. **标准结构**：`public/contracts.py` + `internal/*.py` + `tests/`
2. **公共契约**：`@dataclass(frozen=True)` 的 Command/Query/Event/Result/Error
3. **状态所有权**：`state_owners` 声明单写源
4. **副作用白名单**：`effects_allowed` 显式声明

### 子图 Pipelines

| 子图 | 入口 Cell | 出口 Cell |
|------|----------|----------|
| `execution_governance_pipeline` | delivery.api_gateway | qa.audit_verdict, runtime.projection |
| `pm_pipeline` | delivery.api_gateway | orchestration.pm_dispatch |
| `director_pipeline` | director.execution | qa.audit_verdict |
| `storage_archive_pipeline` | delivery.api_gateway | archive.* |

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

```bash
npm run dev
```

### 3. 命令行触发流水线与独立工具

**模式一：端到端全自动流水线**
```bash
.venv\Scripts\python src\backend\scripts\pm\cli.py --workspace /path/to/repo --run-director
```

**模式二：交互式单角色工作台**
```bash
# Architect - 架构设计
python -m polaris.delivery.cli.architect.cli --mode interactive --workspace .

# Chief Engineer - 技术分析
python -m polaris.delivery.cli.chief_engineer.cli --mode interactive --workspace .

# Director - 代码执行
python -m polaris.delivery.cli.director.cli --workspace . --iterations 1
```

### 4. 架构进阶阅读

- 核心白皮书：[`src/backend/docs/polaris-system-whitepaper.md`](src/backend/docs/polaris-system-whitepaper.md)
- 认知生命体设计稿：[`src/backend/docs/核心重塑：认知生命体.md`](src/backend/docs/%E6%A0%B8%E5%BF%83%E9%87%8D%E5%A1%91%EF%BC%9A%E8%AE%A4%E7%9F%A5%E7%94%9F%E5%91%BD%E4%BD%93.md)

---

## ☕ 请作者喝杯咖啡

如果 Polaris 对你有所帮助，欢迎请作者喝杯咖啡，持续支持项目迭代！

<table>
  <tr>
    <td align="center">
      <img src="docs/assets/images/coffee/alipay.jpg" width="200" alt="Alipay 支付宝" /><br/>
      <strong>支付宝 Alipay</strong>
    </td>
    <td width="60"></td>
    <td align="center">
      <img src="docs/assets/images/coffee/wechat.jpg" width="200" alt="WeChat Pay 微信支付" /><br/>
      <strong>微信支付 WeChat Pay</strong>
    </td>
  </tr>
</table>

---

## 许可协议

MIT License
