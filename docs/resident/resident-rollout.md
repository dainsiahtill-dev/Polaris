# AGI Rollout Guide

## 1. 目标

AGI 上线的真实目标不是“宣布 Polaris 已经是 AGI”，而是把 Polaris 推进到一个可长期驻留、可提议目标、可学习、可治理的领域 AGI 内核。

上线顺序必须保守：

1. 先证明 AGI 能稳定记录事实
2. 再证明 AGI 的 insight 对真实工程问题有效
3. 再开放 goal proposal 与 PM bridge
4. 最后才推进 shadow runtime 与自改 promotion

## 2. 推荐模式

### Phase A: `observe`

适用：

- 首次启用
- 只验证决策轨迹、技能、实验质量

要求：

- 不依赖 AGI 自动提议目标
- 每次只检查投影、轨迹、insight 是否可信

### Phase B: `propose`

适用：

- 决策轨迹稳定
- evidence refs 完整
- 目标治理链已确认

要求：

- AGI 可以提议目标
- 目标必须人工批准后才能进入桥接与执行

### Phase C: `assist`

适用：

- goal proposal 质量稳定
- PM / Director 已经开始消费 AGI 产物

要求：

- AGI 提供辅助建议与桥接能力
- 不绕过主链路

### 暂不默认启用

- `bounded_auto`
- `lab_only`

`bounded_auto` 需要更强的 shadow runtime、A/B promotion、自动回滚，现在还不应默认启用。

## 3. 实际落地流程

### 3.1 启动 AGI

```bash
curl -X POST http://127.0.0.1:49977/v2/resident/start ^
  -H "Content-Type: application/json" ^
  -d "{\"workspace\":\"X:/Git/Harborpilot\",\"mode\":\"observe\"}"
```

### 3.2 运行一次 PM / Director

AGI 已接入：

- `src/backend/app/orchestration/workflows/pm_workflow.py`
- `src/backend/app/orchestration/workflows/director_workflow.py`

因此正常跑主链路就会自动写入结构化决策事件。

### 3.3 手动刷新一次 AGI

```bash
curl -X POST "http://127.0.0.1:49977/v2/resident/tick?force=true" ^
  -H "Content-Type: application/json" ^
  -d "{\"workspace\":\"X:/Git/Harborpilot\"}"
```

### 3.4 审查 AGI 状态

```bash
curl "http://127.0.0.1:49977/v2/resident/status?workspace=X:/Git/Harborpilot&details=true"
```

重点检查：

- `counts.decisions`
- `counts.skills`
- `counts.experiments`
- `counts.improvements`
- `counts.goals`
- `agenda.risk_register`

### 3.5 从 AGI 提案到 PM 执行

1. `approve`
2. `stage`
3. `promote_to_pm_runtime=true` 时写入 PM 运行态
4. `run`

桥接产物：

- `runtime/contracts/resident.goal.contract.json`
- `runtime/contracts/resident.goal.plan.md`
- `runtime/contracts/pm_tasks.contract.json`
- `runtime/contracts/plan.md`
- `runtime/state/pm.state.json`

桥接备份：

- `workspace/meta/resident/staging_backups/<goal_id>/`

## 4. AGI 工作台

前端已提供 AGI 工作台：

- 主入口：`ControlPanel -> AGI 工作区`
- 主视图：身份、agenda、capability graph、goal governance、decision trace、learning artifacts
- 侧栏：`ContextSidebar -> AGI`

适合的操作顺序：

1. 检查 identity / mode
2. 查看 agenda 与风险
3. 审查新 goals
4. approve / reject
5. stage / 写入 PM / 交给 PM

## 5. 门禁

### 5.1 决策轨迹门禁

- 必须覆盖 PM 与 Director 关键阶段
- `DecisionRecord` 必须包含 `actor`、`stage`、`expected_outcome`、`actual_outcome`
- 高价值决策必须带 `evidence_refs`

### 5.2 目标治理门禁

- `pending` 目标不能直接执行
- 所有目标必须可追溯到 insight / experiment / evidence
- bridge 只写合同和运行态，不跳过 PM 治理

### 5.3 自改门禁

- 只能输出 `ImprovementProposal`
- 不得直接修改 policy core
- 不得静默写主干
- 必须带 rollback plan

## 6. 巡检项

### 每日

- 查看 `ResidentAgenda.current_focus`
- 查看 `risk_register`
- 查看新增 `GoalProposal`

### 每周

- 审查 `SkillArtifact` 是否重复或过度泛化
- 审查 `ExperimentRecord` 的建议是否可信
- 审查 `ImprovementProposal` 是否集中在同一薄弱面

### 每个版本

- 回归 AGI API
- 回归 runtime snapshot / websocket resident 投影
- 抽查一条 PM 决策和一条 Director 决策是否完整写入 `decision_trace.jsonl`
- 从 AGI 工作台走一遍 approve -> stage -> run

## 7. 故障排查

### AGI 状态为空

先检查：

- `runtime/state/resident.state.json`
- `workspace/meta/resident/identity.json`

再检查：

- `/v2/resident/start` 是否已调用
- backend 重启后服务是否成功恢复

### 有决策但没有技能 / 实验 / 目标

通常是输入样本不足：

- 技能需要重复成功决策
- 反事实实验需要失败或阻塞决策
- 目标提议依赖 insight / capability gap / improvement

### 目标无法桥接或执行

先检查：

- 是否已 `approve`
- `goal_id` 是否存在
- `materialize` 是否返回治理冲突
- bridge 备份目录是否已生成

## 8. 推荐验证命令

```bash
python -m pytest -q \
  src/backend/tests/test_resident_service.py \
  src/backend/tests/test_resident_api.py \
  src/backend/tests/test_runtime_projection_resident.py \
  src/backend/tests/test_resident_pm_bridge.py

npm run typecheck
npm run test -- src/frontend/src/app/components/resident/ResidentWorkspace.test.tsx
```

## 9. 下一步

1. 增加 shadow runtime，让 `ImprovementProposal` 有真实 promotion 路径
2. 把 `SkillArtifact` 接入 PM / Director 上下文选择器
3. 补 AGI 工作台到 PM 执行的端到端 E2E
