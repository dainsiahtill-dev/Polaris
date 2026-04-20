# Polaris Runtime Artifact Naming Convention

本文定义 `.polaris/runtime/` 的统一命名规则，作为后续实现、测试、前端展示与运维排障的共同基线。

---

## 1. 目标

1. 统一命名风格，消除大小写混用与同义文件并存。
2. 解耦角色语义与模型供应商命名（避免 `OLLAMA_*` 这类供应商耦合命名）。
3. 明确目录分层，区分合同、状态、结果、事件、日志、控制与运行归档。
4. 支持迁移期兼容：`新路径优先读取 + 旧路径兜底读取 + 仅写新路径`。

---

## 2. 适用范围

1. PM / Director / QA / Engine 在 `.polaris/runtime/` 下的所有工件。
2. Backend 配置默认路径、CLI 默认参数、WebSocket 状态投影路径。
3. 测试夹具、自动化脚本、run 级临时文件与测试文件。

---

## 3. 命名总则

1. 全部小写。
2. 语义优先，不使用供应商名作为文件主语义。
3. 后缀表达数据类型和用途：
`*.state.json`、`*.status.json`、`*.result.json`、`*.events.jsonl`、`*.log`、`*.md`
4. 稳定全局指针放 `runtime` 根分层目录；单次运行细节放 `runs/<run_id>/...`。
5. 临时测试工件必须进入 `runs/<run_id>/test/`，不得平铺在 `runtime/` 根目录。

---

## 4. 目录分层（Canonical）

```text
.polaris/runtime/
  contracts/
  state/
  status/
  results/
  events/
  logs/
  control/
  runs/<run_id>/
  evidence/
  snapshots/
  policy/jobs/
  policy/runs/
```

---

## 5. Canonical 文件清单

### 5.1 Contracts

1. `contracts/plan.md`
2. `contracts/pm_tasks.contract.json`
3. `contracts/gap_report.md`
4. `contracts/agents.generated.md`
5. `contracts/agents.feedback.md`

### 5.2 State / Status / Results

1. `state/pm.state.json`
2. `state/task_history.state.json`
3. `state/assignee_execution.state.json`
4. `state/assignee_routing.state.json`
5. `status/director.status.json`
6. `status/engine.status.json`
7. `results/director.result.json`
8. `results/planner.output.md`
9. `results/director_llm.output.md`
10. `results/qa.review.md`
11. `results/auditor.review.md`
12. `results/pm.report.md`
13. `results/pm_last.output.md`

### 5.3 Events / Logs / Control

1. `events/runtime.events.jsonl`
2. `events/pm.events.jsonl`
3. `events/pm.llm.events.jsonl`
4. `events/director.llm.events.jsonl`
5. `events/dialogue.transcript.jsonl`
6. `events/pm.task_history.events.jsonl`
7. `events/hp.phases.events.jsonl`
8. `logs/pm.process.log`
9. `logs/director.process.log`
10. `logs/director.runlog.md`
11. `control/pm.stop.flag`
12. `control/director.stop.flag`

### 5.4 Run-scoped

1. `runs/<run_id>/contracts/pm_tasks.contract.json`
2. `runs/<run_id>/results/director.result.json`
3. `runs/<run_id>/events/runtime.events.jsonl`
4. `runs/<run_id>/state/assignee_execution.state.json`
5. `runs/<run_id>/state/assignee_routing.state.json`
6. `runs/<run_id>/test/plan.auto.md`
7. `runs/<run_id>/test/pm_tasks.auto.json`

---

## 6. 迁移兼容策略

1. 读取策略：先读 canonical 路径，缺失时回退 legacy 路径。
2. 写入策略：只写 canonical 路径，不再写 legacy 同义文件。
3. 对外说明：旧路径仅用于过渡读取，不保证长期保留。

---

## 7. Legacy 到 Canonical 对照（核心）

1. `PLAN.md` -> `contracts/plan.md`
2. `pm_tasks.json` / `PM_TASKS.json` -> `contracts/pm_tasks.contract.json`
3. `PM_STATE.json` -> `state/pm.state.json`
4. `DIRECTOR_STATUS.json` -> `status/director.status.json`
5. `ENGINE_STATUS.json` -> `status/engine.status.json`
6. `DIRECTOR_RESULT.json` -> `results/director.result.json`
7. `PLANNER_RESPONSE.md` -> `results/planner.output.md`
8. `OLLAMA_RESPONSE.md` -> `results/director_llm.output.md`
9. `QA_RESPONSE.md` -> `results/qa.review.md`
10. `REVIEW_RESPONSE.md` -> `results/auditor.review.md`
11. `events.jsonl` -> `events/runtime.events.jsonl`
12. `PM_LOG.jsonl` -> `events/pm.events.jsonl`
13. `PM_LLM_EVENTS.jsonl` -> `events/pm.llm.events.jsonl`
14. `DIRECTOR_LLM_EVENTS.jsonl` -> `events/director.llm.events.jsonl`
15. `DIALOGUE.jsonl` -> `events/dialogue.transcript.jsonl`
16. `RUNLOG.md` -> `logs/director.runlog.md`
17. `PM_SUBPROCESS.log` -> `logs/pm.process.log`
18. `DIRECTOR_SUBPROCESS.log` -> `logs/director.process.log`
19. `PM_STOP.flag` -> `control/pm.stop.flag`
20. `DIRECTOR_STOP.flag` -> `control/director.stop.flag`

---

## 8. Engine 协同要求

1. PM 仅负责规划输出（`contracts/plan.md`、`contracts/pm_tasks.contract.json`）。
2. Polaris Engine 负责角色调度、状态聚合与统一事件出口。
3. 角色运行状态统一由 Engine 维护并对外投影（如 WebSocket），角色本身不直接耦合前端状态协议。

---

## 9. 变更管理

1. 新增 runtime 工件时，必须遵循本规范命名与分层。
2. 若需引入新后缀类型，先更新本文档再实施代码变更。
3. 任何对默认路径的变更必须同步更新：
   1. backend config 默认值
   2. CLI 默认参数
   3. 相关测试夹具和文档引用

