# Software Engineering AGI RFC

## 1. 定位

Polaris 的目标叙事现在明确为：

- `Software Engineering AGI`
- 自动化专业开发软件领域的 AGI

当前实现方式不是把整个系统重写成一个单体 `agi_orchestrator`，而是在现有 `PM -> Director -> QA` 主链路上增加一个长期驻留、可治理、可恢复的 AGI 内核。

实现层仍保留 `src/backend/app/resident/` 这个包名，原因是：

- 代码里已经有存储、API、测试、runtime 投影依赖它
- 立即全量 rename 只会制造高 churn，不能增加能力闭环
- 对外命名与对内实现可以分层演进

## 2. 设计目标

AGI 内核必须满足六件事：

1. 持续身份
2. 持续议程
3. 决策可审计
4. 目标可提议但受治理
5. 经验可积累并迁移
6. 自我改进只能在安全边界内推进

这不是“多加几个模块”，而是把 Polaris 从一次性执行器推进为可长期驻留的领域 AGI 内核。

## 3. 边界

### 3.1 AGI 可以做什么

- 记录 PM / Director 等角色的结构化 `DecisionRecord`
- 运行 `tick()` 刷新 insight、skill、capability、experiment、improvement、goal
- 生成 `GoalProposal`
- 将已批准目标桥接为 PM 可消费合同，并可选择直接写入 PM 运行态
- 将 AGI 状态投影到 `/state/snapshot` 与 runtime websocket
- 提供前端 AGI 工作台，展示身份、议程、目标、决策与学习结果

### 3.2 AGI 不能做什么

- 不能绕过 `GoalGovernor` 直接执行自主目标
- 不能直接修改 policy core、权限系统、批准链
- 不能保存原始 chain-of-thought
- 不能把没有证据的推测提升为能力声明
- 不能直接把实验结论静默推广到主干

## 4. 模块结构

### 4.1 后端内核

- `src/backend/app/resident/models.py`
  - AGI 内核统一数据模型
- `src/backend/app/resident/storage.py`
  - AGI 状态持久化与 UTF-8 文件读写
- `src/backend/app/resident/decision_trace.py`
  - append-only 决策轨迹
- `src/backend/app/resident/meta_cognition.py`
  - 策略评分卡与 insight 生成
- `src/backend/app/resident/capability_graph.py`
  - 基于决策和技能的能力图推导
- `src/backend/app/resident/goal_governor.py`
  - 目标提议、批准、拒绝、物化
- `src/backend/app/resident/pm_bridge.py`
  - 已批准目标到 PM 合同/运行态的桥接器
- `src/backend/app/resident/counterfactual_lab.py`
  - 失败轨迹的反事实实验生成
- `src/backend/app/resident/skill_foundry.py`
  - 重复成功决策的技能抽取
- `src/backend/app/resident/self_improvement_lab.py`
  - 受控自改提案
- `src/backend/app/resident/service.py`
  - AGI 内核服务门面、生命周期与恢复

### 4.2 API 与投影

- `src/backend/api/v2/resident.py`
  - `/v2/resident/*` 控制面
- `src/backend/app/services/runtime_projection.py`
  - AGI 状态进入 `/state/snapshot`
- `src/backend/app/services/runtime_ws_status.py`
  - AGI 状态进入 websocket `status` payload
- `src/backend/app/orchestration/workflows/pm_workflow.py`
  - PM 决策自动写入 AGI trace
- `src/backend/app/orchestration/workflows/director_workflow.py`
  - Director 决策自动写入 AGI trace

### 4.3 前端工作台

- `src/frontend/src/app/components/resident/ResidentWorkspace.tsx`
  - AGI 工作台
- `src/frontend/src/hooks/useResident.ts`
  - AGI 状态与动作 hook
- `src/frontend/src/app/App.tsx`
  - `activeRoleView='agi'` 入口
- `src/frontend/src/app/components/ControlPanel.tsx`
  - AGI 工作区入口
- `src/frontend/src/app/components/ContextSidebar.tsx`
  - AGI 摘要侧栏

## 5. 一等公民对象

- `ResidentIdentity`
  - 对外即 AGI identity：使命、模式、所有者、能力画像
- `ResidentAgenda`
  - 当前焦点、目标审批队列、实验、自改提案
- `DecisionRecord`
  - actor、stage、options、selected、expected、actual、verdict
- `GoalProposal`
  - AGI 自主提议目标
- `SkillArtifact`
  - 可复用技能
- `ExperimentRecord`
  - 反事实实验
- `ImprovementProposal`
  - 受控自改提案

## 6. 存储布局

持久化位置：

- `workspace/meta/resident/identity.json`
- `workspace/meta/resident/agenda.json`
- `workspace/meta/resident/goals.json`
- `workspace/meta/resident/insights.json`
- `workspace/meta/resident/capability_graph.json`
- `workspace/meta/resident/skills.json`
- `workspace/meta/resident/experiments.json`
- `workspace/meta/resident/improvements.json`
- `workspace/meta/resident/decision_trace.jsonl`
- `workspace/meta/resident/tick_history.jsonl`
- `runtime/state/resident.state.json`

所有文本和 JSON 读写均显式使用 UTF-8。

## 7. 执行循环

`ResidentService.tick()` 当前执行：

1. 读取最近决策轨迹
2. 刷新元认知 insight
3. 抽取技能
4. 重建能力图
5. 回放反事实实验
6. 生成自改提案
7. 生成目标提议
8. 更新 identity capability profile
9. 更新 agenda
10. 写入 `resident.state.json` 和 `tick_history.jsonl`

## 8. 目标治理与 PM 桥接

### 8.1 Governance

- 新目标默认 `pending`
- 只有 `approve_goal()` 才能进入 `approved`
- 未批准目标调用 `materialize_goal()` 会返回治理冲突
- AGI 只有提议权，没有默认执行权

### 8.2 PM Bridge

`src/backend/app/resident/pm_bridge.py` 已实现两级桥接：

1. `stage`
   - 写入 AGI 专用暂存合同
   - 产物位于 `runtime/contracts/resident.goal.*`
2. `promote`
   - 在保留备份的前提下写入 PM 运行态
   - 更新：
     - `runtime/contracts/pm_tasks.contract.json`
     - `runtime/contracts/plan.md`
     - `runtime/state/pm.state.json`

备份目录：

- `workspace/meta/resident/staging_backups/<goal_id>/`

## 9. AGI 工作台

当前前端已落地：

- AGI 身份编辑
- agenda / risk / next actions 展示
- capability graph 展示
- goal proposal 创建
- approve / reject / stage / promote / run
- decision trace 浏览
- skill / experiment / improvement 刷新

主界面入口：

- `ControlPanel -> AGI 工作区`

运行时联动：

- `App.tsx` 中以 `activeRoleView='agi'` 挂载
- `ContextSidebar` 提供 AGI 摘要
- `LlmRuntimeOverlay` 支持 `AGI` 视图标识

## 10. 当前已落地范围

### 10.1 已实现

- AGI 内核持久化与恢复
- 决策轨迹记录
- 元认知 insight 与策略评分卡
- 能力图推导
- 目标提议 / 批准 / 物化
- PM bridge 暂存与写入运行态
- 反事实实验生成
- 技能工坊
- 自改提案实验室
- `/v2/resident/*` API
- runtime snapshot / websocket resident 投影
- PM / Director workflow 决策钩子
- 前端 AGI 工作台

### 10.2 尚未实现

- Shadow runtime 自动 A/B 执行器
- 自改提案到真实 promotion 的自动化审批流
- skill-aware context 检索接入 Context Engine 主路径
- capability graph 的跨项目对齐与冲突解决

## 11. 验证

后端：

- `src/backend/tests/test_resident_service.py`
- `src/backend/tests/test_resident_api.py`
- `src/backend/tests/test_runtime_projection_resident.py`
- `src/backend/tests/test_resident_pm_bridge.py`

前端：

- `src/frontend/src/app/components/resident/ResidentWorkspace.test.tsx`

推荐命令：

```bash
python -m pytest -q \
  src/backend/tests/test_resident_service.py \
  src/backend/tests/test_resident_api.py \
  src/backend/tests/test_runtime_projection_resident.py \
  src/backend/tests/test_resident_pm_bridge.py

npm run typecheck
npm run test -- src/frontend/src/app/components/resident/ResidentWorkspace.test.tsx
```

## 12. 后续顺序

1. 增加 shadow runtime 与 A/B promotion
2. 将 `SkillArtifact` 接入 PM / Director context 选择器
3. 为 `ImprovementProposal` 增加审批工单与回滚演练
4. 补 E2E：从 AGI 工作台提案到 PM 执行再到 snapshot 投影闭环
