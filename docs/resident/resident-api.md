# AGI Control API

对外叙事统一称为 `AGI Control API`，当前实现仍挂载在 `/v2/resident/*`。

## 1. 生命周期

### `POST /v2/resident/start`

启动 AGI 内核。

请求体：

```json
{
  "workspace": "X:/Git/polaris",
  "mode": "propose"
}
```

返回关键字段：

- `identity`
- `runtime`
- `agenda`
- `counts`

### `POST /v2/resident/stop`

停止 AGI 循环，但不会清空持久化状态。

### `POST /v2/resident/tick?force=true`

手动触发一次 AGI 刷新循环。

适用：

- 新增决策后立即刷新
- 立刻生成 insight / skill / experiment / goal

## 2. 身份与状态

### `GET /v2/resident/status`

查询 AGI 总状态。

Query：

- `workspace`
- `details`

当 `details=true` 时，返回：

- `decisions`
- `goals`
- `insights`
- `skills`
- `experiments`
- `improvements`
- `capability_graph`

### `GET /v2/resident/identity`

读取 AGI 身份。

### `PATCH /v2/resident/identity`

更新 AGI 身份。

请求体支持：

- `name`
- `mission`
- `owner`
- `operating_mode`
- `values`
- `memory_lineage`
- `capability_profile`

### `GET /v2/resident/agenda`

读取 AGI 议程。

## 3. 决策轨迹

### `GET /v2/resident/decisions`

查询结构化决策轨迹。

Query：

- `workspace`
- `limit`
- `actor`
- `verdict`

### `POST /v2/resident/decisions`

写入结构化决策记录。

最小请求体：

```json
{
  "workspace": "X:/Git/polaris",
  "run_id": "run-001",
  "actor": "pm",
  "stage": "contract_validation",
  "summary": "Validated PM contract",
  "strategy_tags": ["contract_validation"],
  "expected_outcome": { "status": "validated", "success": true },
  "actual_outcome": { "status": "validated", "success": true },
  "verdict": "success",
  "evidence_refs": ["runtime/contracts/plan.md"],
  "confidence": 0.85
}
```

约束：

- `actor` 必填
- `stage` 必填
- 只保存结构化摘要，不保存原始 chain-of-thought

## 4. 目标治理

### `GET /v2/resident/goals`

查询 AGI 目标提议。

Query：

- `workspace`
- `status_filter`

### `POST /v2/resident/goals`

手动创建目标提议。

请求体：

```json
{
  "workspace": "X:/Git/polaris",
  "goal_type": "reliability",
  "title": "Stabilize PM contract quality",
  "motivation": "Reduce drift in PM output",
  "source": "manual",
  "scope": ["src/backend/app/orchestration"],
  "evidence_refs": ["docs/resident/resident-engineering-rfc.md"]
}
```

### `POST /v2/resident/goals/{goal_id}/approve`

批准目标。

请求体：

```json
{
  "workspace": "X:/Git/polaris",
  "note": "approved in AGI workspace"
}
```

### `POST /v2/resident/goals/{goal_id}/reject`

拒绝目标。

### `POST /v2/resident/goals/{goal_id}/materialize`

将目标物化为 PM 合同骨架。

约束：

- 仅 `approved` 或已物化目标可调用
- 未批准目标返回 `409`

返回核心字段：

- `focus`
- `overall_goal`
- `metadata`
- `tasks`

### `POST /v2/resident/goals/{goal_id}/stage`

将目标暂存到 AGI/PM 桥接层。

请求体：

```json
{
  "workspace": "X:/Git/polaris",
  "promote_to_pm_runtime": true
}
```

返回字段：

- `goal`
- `goal_status`
- `staged_at`
- `promoted_to_pm_runtime`
- `contract`
- `artifacts`
- `promotion`

### `POST /v2/resident/goals/{goal_id}/run`

将已治理目标送交 PM 执行。

请求体：

```json
{
  "workspace": "X:/Git/polaris",
  "run_type": "pm",
  "run_director": false,
  "director_iterations": 1
}
```

返回字段：

- `goal`
- `staging`
- `pm_run`

## 5. 技能、实验、自改

### `GET /v2/resident/skills`

读取技能工坊产物。

### `POST /v2/resident/skills/extract`

基于决策轨迹重新提炼技能。

### `GET /v2/resident/experiments`

读取反事实实验列表。

### `POST /v2/resident/experiments/run`

基于当前轨迹重新生成反事实实验。

### `GET /v2/resident/improvements`

读取自改提案列表。

### `POST /v2/resident/improvements/run`

基于实验结果重新生成自改提案。

## 6. 状态字段

### `runtime`

- `active`
- `mode`
- `last_tick_at`
- `tick_count`
- `last_error`
- `last_summary`

### `agenda`

- `current_focus`
- `pending_goal_ids`
- `approved_goal_ids`
- `materialized_goal_ids`
- `risk_register`
- `next_actions`
- `active_experiment_ids`
- `active_improvement_ids`

### `counts`

- `decisions`
- `goals`
- `skills`
- `experiments`
- `improvements`

## 7. 错误语义

- 目标不存在：`404`
- 身份/工作区无效：`400`
- 未授权：`401`
- 未批准目标调用物化：`409`
- 决策写入缺少 `actor` 或 `stage`：服务层校验失败

## 8. 推荐顺序

```text
start -> run PM/Director -> tick -> review -> approve -> stage -> run
```

推荐操作流：

1. `POST /v2/resident/start`
2. 运行一次 PM / Director，自动生成决策轨迹
3. `POST /v2/resident/tick?force=true`
4. `GET /v2/resident/status?details=true`
5. 审查 `goals / experiments / improvements`
6. `POST /v2/resident/goals/{goal_id}/approve`
7. `POST /v2/resident/goals/{goal_id}/stage`
8. `POST /v2/resident/goals/{goal_id}/run`
