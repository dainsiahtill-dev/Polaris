# 任务集市架构重构：10 人团队落地分工方案

- 版本：`v1.1 sync`
- 日期：`2026-04-14`
- 上下文：
  - `AGENT_COLLABORATION_EDA_TASK_MARKET_BLUEPRINT_2026-04-14.md`
  - `AGENT_COLLABORATION_EDA_IMPLEMENTATION_STATUS_2026-04-14.md`
  - `AGENT_COLLABORATION_EDA_REVISION_FIRST_BLUEPRINT_2026-04-14.md`
  - `AGENT_COLLABORATION_EDA_REVISION_FIRST_EXECUTION_PLAN_2026-04-14.md`
- 目标：把 Polaris 从同步角色链迁移到 `Task Bazaar v1.1`，并以 revision-first 语义治理需求漂移

> 本文档是当前阶段的执行分工，不是“最初 PR-01/PR-02 尚未完成”的历史快照。
> 当前仓已经具备 `runtime.task_market`、SQLite WAL、FSM、lease、human review、change order、projection API 等基础点位。
> 团队当前的优先级已经转向：`Outbox -> Durable Pull Consumer -> Revision Enforcement -> Observability`。

---

## 1. 当前基线

### 1.1 已落地事实

当前仓已经具备：

1. `runtime.task_market` public contracts 与 service
2. SQLite WAL + JSON fallback store
3. `fsm` / `lease_manager` / `dlq` / `human_review` / `saga` / `reconciler`
4. `RegisterPlanRevisionCommandV1` / `SubmitChangeOrderCommandV1`
5. `CEConsumer` / `DirectorExecutionConsumer` / `QAConsumer`
6. `dlq_api` / `hitl_api` / `projection_api`
7. `pm_dispatch` rollout mode：
   - `off`
   - `shadow`
   - `mainline`
   - `mainline-design`
   - `mainline-full`

### 1.2 当前最大缺口

1. `outbox_atomic` 未完成
2. `mainline-full` 仍是 inline loop，不是 durable mainline
3. `OpenTelemetry / Prometheus / Grafana` 未接
4. schema pack 未落地
5. revision drift / impact analyzer / DAG validator 未形成正式控制循环

### 1.3 依赖顺序

```
PR-00 Outbox / relay  ─┐
PR-01 Revision harden ─┼─ PR-03 PM authority ─┬─ PR-04 CE durable consumer
                       │                       ├─ PR-05 Director durable consumer
                       │                       └─ PR-06 QA durable consumer
                       │
                       ├─ PR-07 HITL / DLQ / compensation authority
                       ├─ PR-08 Projection / Observability
                       └─ PR-09 Governance / Schemas / Gates
```

裁决：

1. `PR-00` 和 `PR-01` 是所有后续工作的前置。
2. 只有在 `outbox + revision enforcement` 具备后，consumer 常驻化和 Safe Parallel 才值得继续推进。

---

## 2. 团队角色与职责

| 角色 | 编号 | 当前职责 |
| --- | --- | --- |
| Principal Architect | `P0` | 架构裁决、边界审查、主线优先级和 gate 最终验收 |
| Infrastructure Engineer | `E1` | `Outbox + store hardening + config hardening` |
| PM Orchestration Engineer | `E2` | `pm_dispatch` 主链、revision authority、change order 上游接入 |
| ChiefEngineer Integration | `E3` | CE durable consumer、blueprint strict gate、design-stage revision validation |
| Director Integration | `E4` | Director heartbeat、safe parallel、blueprint/revision enforcement |
| QA Integration | `E5` | QA verdict contract、evidence refs、requeue policy |
| Governance & Schema Engineer | `E6` | schema pack、fitness rules、pipeline template、graph freshness |
| DLQ / HITL Engineer | `E7` | human review authority、Tri-Council、DLQ replay / timeout policy |
| Observability Engineer | `E8` | `projection + OTel + metrics + dashboard` |
| Test Automation Engineer | `E9` | 集成 / 并发 / gate / 回归矩阵 |

---

## 3. 分工包

### P0：Principal Architect

当前 ownership：

1. 审批 `runtime.task_market` 与 `events.fact_stream` 的边界
2. 审批 `outbox_atomic` 的实现路径
3. 审批 `revision-first` 与 `task bazaar` 的统一口径
4. 决定 `mainline-full` 何时退役，durable consumer 何时切主链

当前必须盯住的 blocker：

1. `runtime.execution_broker` 不能回流为业务 broker
2. `events.fact_stream` 必须继续保持单写者
3. `Director` 执行不能绕开 blueprint
4. `revision drift` 不能被当作普通 warning

### E1：Infrastructure Engineer

ownership：

1. `polaris/cells/runtime/task_market/internal/store_sqlite.py`
2. `polaris/cells/runtime/task_market/internal/store.py`
3. `polaris/cells/runtime/task_market/internal/service.py`
4. 新增 `outbox` / relay 模块

当前任务：

1. 落 `outbox_messages` 持久化
2. 让 state transition 与 fact publication 绑定
3. 补配置：
   - `KERNELONE_TASK_MARKET_DESIGN_VISIBILITY_TIMEOUT_SECONDS`
   - `KERNELONE_TASK_MARKET_EXEC_VISIBILITY_TIMEOUT_SECONDS`
   - `KERNELONE_TASK_MARKET_MAX_ATTEMPTS`
4. 保持 `KERNELONE_TASK_MARKET_STORE` 的 `sqlite/json` 行为稳定

完成定义：

1. state 写成功但 fact emit 失败时，不丢事实
2. relay 可恢复、可重放、可审计

### E2：PM Orchestration Engineer

ownership：

1. `polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`
2. `polaris/cells/orchestration/pm_planning/**`
3. 相关 tests 与 graph/subgraph 同步

当前任务：

1. 把 PM 发布变成 revision authority 上游入口
2. 接 `RegisterPlanRevisionCommandV1`
3. 接 `SubmitChangeOrderCommandV1`
4. 落 DAG validator / impact analyzer 的 PM 入口
5. 让 `mainline` 与 `mainline-design` 默认携带 revision-aware payload

完成定义：

1. PM 发布的每个设计任务都能追溯到唯一 revision
2. PM 不再静默重写旧任务语义

### E3：ChiefEngineer Integration

ownership：

1. `polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py`
2. `polaris/cells/chief_engineer/blueprint/internal/chief_engineer_preflight.py`
3. `blueprint` 相关测试

当前任务：

1. 把 CE 从 inline helper 提升为 durable pull consumer
2. 强制 blueprint 输出携带：
   - `blueprint_id`
   - `context_snapshot_ref/hash`
   - `guardrails`
   - `no_touch_zones`
3. ack 前校验 revision drift
4. 去掉无 blueprint 的兜底路径

完成定义：

1. `pending_exec` 只能从合法 blueprint 产出
2. revision 漂移时 CE 不会继续产出旧蓝图

### E4：Director Integration

ownership：

1. `polaris/cells/director/task_consumer/internal/director_consumer.py`
2. `polaris/cells/director/execution/**`
3. `director` 相关 tests

当前任务：

1. 补 heartbeat / renew
2. 强制 `director_requires_blueprint`
3. 落 `scope_paths` / `target_files` 冲突检测
4. 引入 Safe Parallel 第一阶段
5. ack 前校验 revision / blueprint 一致性

完成定义：

1. stale lease ack 被拒绝
2. 缺 blueprint 的任务进入 reject / dead letter
3. 并发 Director 不会盲写同一 scope

### E5：QA Integration

ownership：

1. `polaris/cells/qa/audit_verdict/internal/qa_consumer.py`
2. QA contract / verdict mapping

当前任务：

1. 补 `evidence_refs`
2. 统一 `resolved / rejected / requeue_exec / requeue_design / waiting_human` 路由
3. 让 QA verdict 与 revision-aware revalidation 对齐

完成定义：

1. QA verdict 有结构化证据
2. 需要回到设计阶段时能明确回流

### E6：Governance & Schema Engineer

ownership：

1. `docs/governance/ci/fitness-rules.yaml`
2. `docs/governance/schemas/*.yaml`
3. `docs/graph/catalog/cells.yaml`
4. `docs/graph/subgraphs/*.yaml`

当前任务：

1. 固化 `v1.1` 红线：
   - `no_direct_role_call`
   - `task_market_is_single_business_broker`
   - `outbox_atomic`
   - `director_requires_blueprint`
2. 新增 schema pack
3. 同步 pipeline template
4. 审查 graph 与 code truth 一致性

完成定义：

1. 文档与 graph 不再互相打架
2. schema pack 可供 CI / replay / audit 使用

### E7：DLQ / HITL Engineer

ownership：

1. `polaris/cells/runtime/task_market/internal/human_review.py`
2. `polaris/cells/runtime/task_market/public/hitl_api.py`
3. `polaris/cells/runtime/task_market/internal/dlq.py`

当前任务：

1. 将 human review 从 helper API 提升为 authority path
2. 完整打通 Tri-Council：
   - `director -> chief_engineer -> pm -> architect -> human`
3. 加入 timeout / SLA / escalation metadata
4. 规范 DLQ replay policy

完成定义：

1. `waiting_human` 具备明确升级路径
2. DLQ replay 不会绕开治理审计

### E8：Observability Engineer

ownership：

1. `polaris/cells/runtime/projection/task_market_projection.py`
2. `polaris/cells/runtime/task_market/public/projection_api.py`
3. observability 接线

当前任务：

1. 补 dashboard summary 的 revision 视图
2. 接 `OpenTelemetry`
3. 暴露 metrics：
   - `claim_rate`
   - `ack_latency_seconds`
   - `lease_renew_failures_total`
   - `dlq_rate`
   - `consumer_backlog`
4. 为后续 Grafana 看板准备 query model

完成定义：

1. 运维不再依赖零散日志判断系统状态
2. backlog / worker load / DLQ / human review 可观测

### E9：Test Automation Engineer

ownership：

1. `task_market` 单元 / 集成 / 并发测试矩阵
2. catalog governance gate
3. migration regression

当前任务：

1. 扩全链路测试：
   - PM -> CE
   - CE -> Director
   - Director -> QA
   - revision drift
   - DLQ replay
   - stale lease
   - competing claims
2. 在 CI 中加入 schema / governance / replay gate
3. 为 `outbox` 增加故障注入场景

完成定义：

1. 新主链不靠人工 smoke 测试维持
2. rollback 与 replay 都有自动化证据

---

## 4. 里程碑

### M0：本周

1. `E1` 落 `outbox` 基础设施
2. `E6` 补 schema pack 草案
3. `E9` 建 `outbox` 与 revision drift 测试骨架

### M1：下周

1. `E2` 完成 PM revision authority 接入
2. `E3` / `E4` / `E5` 对齐 consumer 的 revision validation
3. `E7` 打通 authority 级 HITL 路径

### M2：第三周

1. `E8` 接 observability
2. `E4` 落 Safe Parallel 第一阶段
3. `E9` 扩并发回归与 failure injection

### M3：第四周

1. 评估 `mainline-full` 退役
2. 切 durable pull-consumer 主链
3. 跑治理 gate、回滚演练和压测

---

## 5. 当前回合执行口径

1. 不再把 `PR-01/PR-02` 当作主要工作包，它们大体已在仓。
2. 当前最重要的三件事是：
   - `Outbox`
   - `Revision Enforcement`
   - `Durable Mainline`
3. 任何继续扩大 inline orchestration 的修改，都应视为短期 stopgap，而不是长期架构。
4. 任何继续让 `events.fact_stream` 之外的 cell 声称写 `runtime/events/*` 的改动，都应直接拦下。
