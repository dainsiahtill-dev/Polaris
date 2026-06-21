# 三层任务分解架构蓝图（PM→CE→Director）

日期: 2026-06-12 ｜ 状态: 历史设计输入；当前强约束为 `PM → Chief Engineer → Director` 唯一链路 ｜ 来源: 用户架构指令 + factory-bench L1/L2 全量取证

> 2026-06-21 强制修订：本文早期“兼容期回退 PM→Director 直连”的设想已废弃。当前运行态缺少 CE 蓝图/交接证据时必须 fail-closed，并在前端/事件/日志中显示等待或阻塞 CE；禁止恢复 PM→Director 直连、feature flag 回退或快照兜底。

## 1. 问题与动机（实证）

现状链路 PM→Director 直连，CE 仅 preflight 结构索引（`chief_engineer.blueprint.json`
只有 files/modules/api_contracts，零施工步骤），Director 收不到 CE 任何智能
（blueprint 信号 role-bound CE + 默认关）。后果（L2 梯队 14 跑实证）：

- PM（云端强模型）任务粒度随机（同 brief 四跑四种拆法），常产出弱执行者不可收敛的形状（单文件大产物、vendored 库目标）。
- Director（本地 27B，16k 窗口）独自承担 HOW：>120 行单写必被输出顶截断；多文件任务靠重试梯硬扛。
- CE 绑定的云端强模型在执行链路上空转。

## 2. 目标架构（文本图）

```
项目技术书 requirements.md
   │
   ▼
PM（云）—— WHAT 层：分批产生 合同+计划文档+粗粒度任务（assigned_to: chief_engineer）
   │            pm_tasks.contract.json（可多批次/多迭代追加）
   ▼
CE（云）—— HOW 层：领取每个 PM 任务 → 拆解为蓝图任务（可一拆多）
   │            ce_blueprint_tasks.contract.json：construction_steps[]
   │            每步 = {step_id, parent_pm_task, target_file(单文件), est_lines≤120,
   │                    signatures[](函数/类骨架), interface_names[](统一定名),
   │                    verify(机器可执行判据), depends_on[]}
   ▼
Director（本地）—— DO 层：领取 CE 蓝图任务（不再直领 PM 任务）
   │            每步单文件≤120行 + 写后自查(verify) + 截断即 append 续写
   ▼
QA —— 终态 workspace_check + 验收
```

## 3. 模块职责与改动面

| 模块 | 改动 |
|---|---|
| `pm_planning` 质量门 | PM 任务 assigned_to 允许/引导 `chief_engineer`（执行类任务不再直派 director）|
| `chief_engineer` cell | 新增蓝图任务拆解能力：消费 PM 任务 → 产出 `construction_steps[]` 合同（提示词纪律已落地 2026-06-12）|
| `pm_dispatch` | 派发源切换：director 队列从 CE 蓝图任务合同读取；保留 `task["blueprint_id"]` 锚点（dispatch_pipeline.py:1472）做溯源 |
| `roles.kernel` RoleSignalPlane | 新 `BlueprintStepsSignal`（director-bound、default-on、must-have）：注入当前步骤的 signatures/interface_names/verify |
| `director` adapter | 验收豁免/质量扫描对 step.verify 优先（已有 verify-exists 机制可复用）|
| factory-bench | chain_results 增 ce_task 维度统计 |

## 4. 核心数据流与契约

- 新契约 `ce_blueprint_tasks.contract.json`（schema: ce-blueprint-tasks/1），owner=ChiefEngineer，
  落 `runtime/contracts/`；每条蓝图任务携带 parent_pm_task 溯源。
- Director 任务上下文 = 单个 construction_step（不是整个 PM 任务）——上下文体积天然受控，
  16k 窗口下提示+骨架+verify ≈ 2-3k tokens，输出预算充裕。
- 失败语义：step 失败只阻塞 depends_on 后继，同 parent 的并行步不受累（缓解串行全停）。

## 5. 技术理由

1. **输出预算物理学**（L2-11 r6/r7 实锤）：~7KB 单文件整写不可收敛；步长 ≤120 行是经验证的可收敛粒度。
2. **智能分布匹配模型能力**：WHAT/HOW 在云端强模型（1M/256k 上下文），DO 在本地（隐私/成本/被测对象）。
3. **接口先行定名**防多步漂移（弱模型跨步遗忘函数名/DOM id 的实证对策）。
4. **每步机器 verify** 把 QA 左移到写后即查（A5/质量扫描/workspace_check 全套既有设施直接复用）。

## 6. 实施顺序（建议 3 个增量）

1. **I1 契约+CE 拆解**：ce_blueprint_tasks 契约 schema + CE 拆解调用（复用 role runtime；提示词已备）+ 单测。
2. **I2 派发切换**：pm_dispatch 读 CE 合同派 Director + BlueprintStepsSignal 注入 + 回归（dispatch/kernel 测试面）。
3. **I3 工厂验证**：L2-12/L2-11 复跑对照（步级成功率/截断率/墙钟），出量化报告。

## 7. 风险

- CE 拆解本身的质量需要门（步长超限/缺 verify 的蓝图任务要被质量门拦截——复用 PM 门模式）。
- 双层任务簿状态机（PM 任务聚合状态 = 其 CE 子任务状态归并）需明确归并规则。
- 兼容期策略已废弃：无 CE 拆解产物时阻塞 Director 派发并记录缺失的 CE 蓝图/交接证据，禁止通过 feature flag 回退到 PM→Director 直连。

## 8. 修订（2026-06-12 深夜）：与 TaskMarket 原始设计合流（最优方案）

**考古结论**：`PM_CHIEF_DIRECTOR_TASK_MARKET_ROUTING_BLUEPRINT_20260603.md` 早已设计本拓扑——
市场阶段 `pending_design`(CE 领取)→蓝图落证→`pending_exec`(Director worker 领取)→QA；
PM 按任务复杂度路由 `direct_to_director` / `chief_blueprint_required`。
且**部分已实现**：dispatch_pipeline.py:545/1017 路由常量、:1472 blueprint_id、
三方 consumer 在 cells（chief_engineer/blueprint/internal/ce_consumer.py、
director/task_consumer/internal/director_consumer.py、qa/audit_verdict/internal/qa_consumer.py）。
今日工厂链走的是 workflow 串行派发旁路，市场路线休眠——early design 目的：
①市场化解耦（claim 语义+worker 池+scope 冲突检查管并行）②按复杂度双路由
③blueprint_id 证据链。

**最优方案 = 完成原设计 + 三个增量扩展**（防重复造轮子，复用既有市场/consumer）：

- **E1 步级一拆多（对原设计的关键 delta）**：原设计 CE "advance 同一任务"；改为 CE 领取
  pending_design 后可**拆分为 N 个 pending_exec 步任务**（每步=单文件 construction_step，
  parent_pm_task 溯源，依赖经 depends_on 编排）——这正是用户指令「CE 可产生多个蓝图/任务」。
- **E2 弱执行者步契约**：construction_step schema（§4）+ CE 步质量门（步长>120行/缺 verify/
  缺签名即拦截，复用 PM 门模式）。
- **E3 Director 步上下文注入**：director_consumer 领取的步任务携带 signatures/interface_names/
  verify 进任务 context（BlueprintStepsSignal，director-bound、default-on）。
- **E4 路由默认翻转（flag 门控）**：弱 Director 配置下，代码物化类任务默认
  `chief_blueprint_required`；`direct_to_director` 保留给 docs-only/平凡任务与降级回退
  （与原设计"PM may send simple work directly"完全一致）。

**实施顺序修订**：I1=E2 契约+CE 拆解（在 ce_consumer 的 claim→advance 处插拆分）；
I2=E3 注入+E4 路由默认；I3=工厂以**市场模式**跑 L2-11/12 对照 workflow 旁路出量化报告。

## 9. 外部评审采纳（Gemini 复盘）+ 边界卡点（2026-06-12）

采纳三项精细化：
- **E1 三元组强绑定**：步任务 payload 必含 `parent_task_id` + `blueprint_id/blueprint_path` + `depends_on[]`（归并/审计链/拓扑序三用途）。
- **E2 熔断语义**：步质量门拦截（步长>120 估行 / 缺 verify / 缺签名）即在 CE 阶段熔断，垃圾任务不入市场。
- **E1 并发红利**：N 步任务挂回 pending_exec 后由 worker 池在 depends_on 就绪前提下并发——这是市场路线对串行旁路的决定性优势。

边界卡点（本仓实证补充，I1/I2 开工前必须确认）：
1. **E3 有界注入**：「上游已完成步骤产出物」严禁全文注入——16k 窗口实证（W1.5c 后 director 可用提示 ~5-6k）。
   注入 = 全局签名骨架 + 接口定名表 + 上游产出**文件清单**（路径+单行摘要）；正文由 Director 按需 read_file。
2. **depends_on 就绪门**：市场 claim 必须校验依赖步全部 success 才可领（落点=director_consumer claim 检查，
   与既有 scope-conflict 检查同层）；防无序争抢导致构建报错。
3. **状态归并规则（I2）**：父任务 = 子任务集合聚合：任一 failed→父 failed（触发回退/报警）；
   全 success→父 success；存在 pending/running→父 running。归并入口=任务簿订阅 parent_task_id。
4. **I3 量化口径**：步级成功率 / 截断率（A5 syntax_check=failed 含截断签名比率）/ 墙钟，
   市场模式 vs 串行旁路同项目同 seed 对照。
