# Resident Autonomy 第二轮蓝图 (Round 2: G2/G4/G5)

- **日期**: 2026-06-09
- **前置**: `RESIDENT_AUTONOMY_IGNITION_BLUEPRINT_20260609.md`（第一轮：点火 + G3，已合入 `main` 提交 `fc528d3d`）
- **范围**: 审计遗留的 G2（UI 面）/ G4（inert 契约）/ G5（workflow_activity 死副本）

## G2 — 暴露进化产出 + 补全目标治理（前端，full）

**问题**：UI 仅接 ~7/30 端点。`tick()` 现已能产出 skills/experiments/improvements，但前端
`useResident` 把它们拉取后**从不渲染**；`rejectGoal` 有方法无按钮。点火后这些产出仍不可见。

**方案**：
1. `ResidentWorkspace.tsx` 新增 `进化` tab（`TAB_OPTIONS` 增加 `evolution`）：
   - 技能 (skills)：name / trigger / confidence / version，run 按钮 `extractSkills()`。
   - 反事实实验 (experiments)：baseline→counterfactual / recommendation / status，run 按钮 `runExperiments()`。
   - 自改提案 (improvements)：title / category / target_surface / status，run 按钮 `runImprovements()`。
   - 每区空态有占位文案（点火前为空属正常）。
2. 目标 tab 的展开操作区，在「批准」旁补「拒绝」按钮 → 已存在的 `rejectGoal(goalId)`。

**数据来源**：`useResident` 已暴露 `residentSkills/residentExperiments/residentImprovements`
与 `extractSkills/runExperiments/runImprovements/rejectGoal`（无需改 hook）。类型见
`appContracts.ts` `ResidentSkill/Experiment/ImprovementPayload`。

## G4 — 让 inert 的公开契约真正活起来（后端，full）

**问题**：`public/contracts.py` 的 `RunResidentCycleCommandV1 / RecordResidentEvidenceCommandV1 /
QueryResidentStatusV1 / ResidentCycleCompletedEventV1 / ResidentAutonomyResultV1 /
ResidentAutonomyError` 是纯数据类，**无 handler、无 dispatcher、无 caller**（仅被自身
`__init__`/契约测试引用）。全部真实流量走 `record_resident_decision()` 函数 + HTTP router，
与声明的 CQRS 面平行无交集。

**方案**：在 `public/service.py` 增加 handler，把契约映射到 `ResidentService`，并由**真实调用方**消费：

| 契约 | Handler | 真实调用方 |
|---|---|---|
| `QueryResidentStatusV1` | `query_resident_status(q, *, include_details)` → `get_status` | `/v2/resident/status` 端点（返回形状不变） |
| `RunResidentCycleCommandV1` | `run_resident_cycle(cmd) -> ResidentAutonomyResultV1` → `tick(force=cmd.context['force'])` | `resident_autotick.run_autotick_once`（每轮构造命令） |
| `ResidentAutonomyResultV1` | `run_resident_cycle` 的返回（status/actions/metrics 由 tick 摘要派生） | 同上 |
| `ResidentCycleCompletedEventV1` | `_emit_cycle_completed_event(cmd, result)` 构造+日志 | `run_resident_cycle` 内 |
| `ResidentAutonomyError` | cycle 失败时抛出（code=`cycle_execution_failed`） | `run_autotick_once` 吞咽 |
| `RecordResidentEvidenceCommandV1` | `record_resident_evidence(cmd)` → 以证据形状 `record_resident_decision`（stage=`evidence:{kind}`） | 单测构造；契约可由 PM/Director 复用 |

**关键约束**：
- `run_resident_cycle` 的 force 来自 `command.context.get("force", False)`，**保留 autotick 的
  active 二次门控**（autotick 传空 context → force=False → 未启动则无害早退，result.status=`skipped_inactive`）。
- `/status` 改为经 `query_resident_status`，输出**逐字不变**（handler 内部仍是 `get_status`）。
- handler 失败统一抛 `ResidentAutonomyError`；autotick 已有 try/except 吞咽。
- 不引入命令总线/事件总线（避免过度设计）；event 当前构造+日志，pub/sub 留待后续。

## G5 — workflow_activity 死副本（治理记录，非改码）

**核实事实**：
- `workflow_activity`（4279 行）**零生产引用**：此前疑似的两个 importer 实为
  `domain/entities/workflow.py` 的 **docstring 提及** 与 `workflow_runtime/.../director_activities.py:384`
  的**字符串字面量** `"workflow_activity": "workflow_runtime.director_execution"`，均非 import。
- 仅被 2 个测试引用（位于 `workflow_runtime/tests/`：`test_pm_workflow_timeouts.py`、`test_director_activities.py`）。
- 活引擎是 `workflow_runtime`（15663 行）；两者已**分叉**（行数差 ~3.6x），非逐字副本。
- 架构意图**不明**：`workflow_activity` 自述「Owns Activity/Workflow definitions」，可能本应是
  「定义 cell」被 `workflow_runtime` 复用，但现实是 runtime 自带了一份 director/pm workflow。

**决策**：本轮**不**做结构性合并。理由：
1. 风险/价值极不对称——4k 行工作流引擎合并需逐函数等价性验证，触碰 live 引擎的 blast radius 大；
   收益仅为内部去重，无用户面价值。
2. 架构意图未定，贸然加 deprecation 标记可能误判（也许 `workflow_activity` 才应是 canonical 定义源）。
3. 契合「债务靠修复非删除」——但「修复」需先定意图，属独立专项。

**本轮动作**：在 `docs/governance/` 留一份事实记录 + 推荐一个等价性验证驱动的专项 wave；
不改任何 `workflow_activity` 代码（纯加法文档，零行为风险）。

## 验证

- 后端：新增 `test_resident_contract_handlers.py`；更新 `test_resident_autotick.py` 返回类型；
  回归 `test_resident_api.py`（/status 不变）。ruff/mypy/pytest fail-closed。
- 前端：`ResidentWorkspace.test.tsx` 增「进化 tab 渲染 + run 按钮 + reject」断言。tsc/eslint/vitest。
