# Polaris Agent 文档中心

此目录面向 **AI Agent / 自动化执行器**，强调可执行约束、证据链与可回放性。

---

## 📚 文档索引

### ACGA 3 补充方向

为支撑 Polaris 向“无人值守软件工厂控制面”演进，仓库新增以下补充文档：

| 文档 | 说明 | 读者 |
| --- | --- | --- |
| [ACGA 3 Factory Positioning](../../ACGA3_FACTORY_POSITIONING.md) | ACGA 3 的产品定位、差异化亮点与诚实边界 | 产品 / 工程师 |
| [ACGA 3 Autonomous Factory Spec](../architecture/ACGA_3_AUTONOMOUS_FACTORY_SPEC.md) | 治理内核、双运行模式、Proposal/Truth 分层与演化闭环 | 工程师 / 平台开发 |
| [Backend ACGA 3 RFC](../../src/backend/docs/ACGA_3.0_RFC.md) | 后端 ACGA 3 草案与 KernelOne / Cells 分层约束 | 工程师 |

说明：

- 这些文档是仓库级补充，用于同步下一阶段目标架构与产品方向。
- 当前后端正式真相仍以 `src/backend/docs/graph/**`、`src/backend/docs/FINAL_SPEC.md` 与相关 `cell.yaml` 为准。

### 🎯 产品与需求

| 文档 | 说明 | 读者 |
| --- | --- | --- |
| [产品说明书](../product/product_spec.md) | 产品定位、优势、适用场景 | Agent / PM |
| [需求文档](../product/requirements.md) | 需求定义与验收基线 | Agent / 开发者 |

### 🏗️ 系统与工程

| 文档 | 说明 | 读者 |
| --- | --- | --- |
| [架构文档](architecture.md) | 状态机、事件模型、数据流、Context Engine | 工程师 |
| [不变量宪法](invariants.md) | 核心 8 条约束（不可破坏） | 工程师 |
| [参考手册](reference.md) | CLI、工具、事件类型、产物索引 | 开发者 |
| [Runtime 命名规范](runtime_artifact_naming.md) | `.polaris/runtime` 分层与文件命名标准（canonical） | 工程师 |
| [拟人化设计](anthropomorphic_design.md) | Memory/Reflection/Persona/Glass Mind | 工程师 |
| [Software Engineering AGI RFC](../resident/resident-engineering-rfc.md) | 软件工程领域 AGI 内核、治理边界、存储模型与运行时挂载点 | 工程师 |
| [AGI Control API](../resident/resident-api.md) | `/v2/resident/*` 控制面接口说明 | 工程师 / 平台开发 |
| [AGI Rollout](../resident/resident-rollout.md) | AGI 启用顺序、运行模式、门禁与巡检建议 | 工程师 / 运维 |
| [AGI Workspace](../resident/agi-workspace.md) | AGI 工作台 UI、入口、动作与前端联动 | 工程师 / 平台开发 |
| [AGI 价值说明](../resident/agi-value-proposition.md) | AGI 当前的实际作用、能力边界，以及对未来项目与平台的长期价值 | 产品 / 工程师 / 平台开发 |
| [Workspace 持久化机制](workspace_persistence.md) | workspace 的真相源、持久化路径、环境变量同步与前端防回退策略 | 工程师 / 平台开发 |
| [Context Engine v2 计划](context_engine_v2_plan.md) | 升级路线与落地阶段 | 工程师 |
| [Sniper Mode v2.0 计划](sniper_mode_v2_plan.md) | 上下文工程优化与成本感知路线图 | 工程师 |
| [3-Hops 失败定位实现](failure_3hops_implementation.md) | Phase→Evidence→Tool Output 工程落地与测试 | 工程师 |
| [门下省 QA 验收规范 v1](qa_chancellery_v1.md) | QA 裁决规则、LLM 验收链路选择、`qa_contract`、插件接口与后置流程 | 工程师 / PM |
| [Chief Engineer Blueprint Spec](chief_engineer_blueprint.md) | Chief Engineer how to maintain project blueprint, generate method-level construction plans, and collaborate with Director | Engineer / PM / Director |

---

## AI Agent Quick Context

- `.ai-agent/context.json` (machine-readable core context)
- `.ai-agent/project_context.md` (human-friendly overview)
- `.ai-agent/project_map.xml` (compressed navigation map)
- `.ai-agent/best_practices.md` (AI do/don't)
- `.ai-agent/templates/` (refactor / new_feature / bug_fix templates)

---

## 🔍 快速入口

- 项目总览：[`../../README.md`](../../README.md)
- 文档入口：[`../README.md`](../README.md)
- Electron Playwright 自动化手册：[`../testing/PLAYWRIGHT_ELECTRON_AUTOMATION.md`](../testing/PLAYWRIGHT_ELECTRON_AUTOMATION.md)
- AI agent context：`.ai-agent/context.json`
- Context Engine v2：[`architecture.md#3-上下文引擎-context-engine-v2`](architecture.md#3-上下文引擎-context-engine-v2)

---

## 🔄 AI/Agent 双流程（必须明确）

针对 AI/Agent 的端到端执行，必须明确以下两条流程，按场景选其一：

### Flow A: Architect First (Recommended)

适用：新项目、复杂需求、需求边界未收敛、需要先形成文档契约的任务。

1. Architect first produces project documents（`workspace/docs/product/requirements.md`、`workspace/docs/product/plan.md` 等）。
2. 同步 `plan.md` 到运行态 `runtime/contracts/plan.md`（`applyDocs` 会自动执行该同步）。
3. PM 基于文档生成任务合同。
4. (Optional) Chief Engineer generates code construction plans for complex tasks（模块/文件/方法级），再交付 Director。
5. Director 按合同与施工图落地实现。
6. Auditor(QA) 基于证据链与规则引擎给出 PASS/FAIL/INCONCLUSIVE。

### Flow B: No Architect (PM Direct Start)

适用：已有稳定需求文档、增量修复、小范围改动、快速迭代。

1. 直接从 PM 开始（可由现有 requirements/plan 或最小输入启动）。
2. PM generates task contract, and triggers Chief Engineer to generate construction plans as needed.
3. Director 执行实现。
4. Auditor(QA) 进行独立验收并给出裁决。
5. If requirements ambiguity, unstable acceptance chain, or consecutive failures occur, must switch back to Flow A, with Architect completing document contracts first before continuing.

约束说明：
- 两条流程最终都必须落到同一闭环：`PM -> (ChiefEngineer 可选) -> Director -> Auditor(QA)`。
- 仅流程 A 要求“先Architect后 PM”；流程 B 允许 PM 直接启动，但不豁免 QA 与证据要求。
- 运行态产物写入 Polaris 运行目录，不得在目标 workspace 下新建 `runtime/`。

### 命令行一次性指令（内存输入）

AI/Agent 场景建议通过单次 CLI 指令启动，而不是先手工写 docs/code：

- `--start-from architect|pm`：指定入口流程
- `--directive` / `--directive-file` / `--directive-stdin`：一次性输入任务说明
- 输入文本在进程内存中读取与传递；大文本可用 stdin 管道输入

示例：

```bash
python src/backend/scripts/loop-pm.py \
  --workspace /path/to/repo \
  --start-from architect \
  --directive-file /path/to/requirement.md \
  --run-director --director-iterations 1
```

---

## 🧭 Agent 核心约束速查

- **合同不可变**：`PM_TASKS.json` 的 goal/AC 只能追加证据
- **事实流 Append-Only**：`events.jsonl` 只追加，不回写
- **数据即真相，视图即表现**：事实源/规范化配置是唯一真相，UI/报表/看板仅做投影
- **Run ID 全局唯一**：所有产物/引用必须携带 run_id
- **UI Read-Only**：运行态 UI 不写入任务/代码
- **可回放**：仅依赖 events + trajectory + artifacts paths
- **失败可定位（3 Hops）**：Phase → Evidence → Tool Output
- **原子写入**：关键状态文件 write tmp → fsync → rename
- **记忆必须可溯源**：memory/reflection 必须带 refs

### 拟人化核心的工程目的

- 拟人化不是装饰，而是无人值守的**自我修复控制面**。
- 当执行遇阻时，角色间可基于证据链协商（PM/Chief Engineer/Director/QA），把“失败”转成“下一步可执行动作”。
- 相比单线路定死的自动化流程，该机制更能适配弱模型与复杂场景，核心收益是**减少人工介入频次**并提升长跑稳定性。

## 🛡️ 角色权限矩阵（强约束）

| 角色 | 可读范围 | 可写范围 | 禁止事项 |
| --- | --- | --- | --- |
| User（用户） | Dashboard/设置面板等运行态投影 | 仅 UI 入口指令（如“Discussion”“快驿”） | 直接改代码/运行态契约文件 |
| Architect | `docs/` | `docs/` | Write to any path outside `docs/` |
| PM（PM） | `docs/` + 运行态事实/状态 | 运行态任务与协调产物（如 `pm_tasks.json`、状态/报告） | 直接实现业务代码 |
| Chief Engineer（ChiefEngineer） | 需求/计划/任务合同 + 代码现状 | 蓝图产物与施工图产物（`runtime/contracts/chief_engineer.blueprint.json`、`runtime/state/chief_engineer.state.json`） | 直接提交业务代码 |
| Engineering（Director） | PM 下发任务契约 + 被授权代码范围 | PM 授权的代码目录/模块（默认 `scope_mode=module`） | 越权写入未授权路径、自动回滚 |
| 门下（QA/Auditor） | 全量只读（代码、文档、运行态证据） | 审计产物（QA 结论、缺陷票据、门禁判定） | 代替 Director 实现需求功能 |

补充规则：
- `hp_run_verify` 属于 Director 自检（语法/类型/测试等），不是 QA 验收。
- QA 必须按 PM 的任务目标与 `acceptance` 做验收，失败时先要求 Director 修复。
- 达到修复上限后标记 FAIL/BLOCKED 并等待人工处理，但不阻塞 PM 主线继续推进其他任务。
- Snapshot 仅作参考备份，不用于自动回滚；回滚仅允许人工确认触发。

## 🤖 模型约束速查

| 约束 | 要求 | 优先级 |
|------|------|--------|
| **Thinking 必须** | PM/Director 必须输出 `<thinking>` 标签 | P0 |
| **Streaming 必须** | 所有角色必须支持流式输出（延迟 < 100ms） | P0 |
| **兼容性检测** | 接入前必须验证 Thinking + Streaming 支持 | P0 |
| **胜任性测试** | PM/Director 未通过thinking检测则 BLOCKED | P0 |
| **推荐模型** | GPT-4、Claude-3、Codex CLI、Kimi-K2、MiniMax | P1 |
| **部分兼容** | Local SLM（Qwen/Llama，经 Ollama/LM Studio，需 prompt 工程，仅建议分流）、Gemini CLI（需格式转换） | P2 |
| **不兼容** | 基础模型（无 thinking/streaming） | - |

### 性能指标

| 指标 | 要求 |
|------|------|
| 流式延迟 | < 100ms |
| Thinking 解析 | < 1s |
| Provider 接口 | 兼容 OpenAI SDK |

---

## 📝 文档更新日志

| 日期 | 更新内容 |
|------|----------|
| 2026-02-24 | Added Chief Engineer Blueprint Spec: supports `off/auto/on` optional intervention, produces module/file/method-level construction plans and injects into Director task contracts |
| 2026-02-23 | Tri-Council（三省协调回合）落地：复杂任务/QA非PASS 时触发 PM-Director-Auditor 协调，并写入协调决策产物与对话流 |
| 2026-02-23 | QA 规范补充：LLM 仅用于验收链路选择，PASS/FAIL/INCONCLUSIVE 仍由规则引擎 + 证据裁决 |
| 2026-02-23 | 新增门下省 QA 验收规范 v1（契约化裁决与插件接口） |
| 2026-02-09 | 新增 Electron Playwright 自动化手册入口 |
| 2026-02-08 | 添加模型约束速查章节，明确 Thinking/Streaming 要求 |
| 2026-02-02 | 重构为人类/Agent 双入口文档结构 |
