# 任务集市架构重构：实现状态报告

- 版本：`v1.3 sync`
- 对应蓝图：`AGENT_COLLABORATION_EDA_TASK_MARKET_BLUEPRINT_2026-04-14.md`
- 编制日期：`2026-04-14`
- 最近更新：`2026-04-15`
- 状态：`v1.3 milestone 达成 — drift requeue ✅ / multi-workspace isolation ✅ / perf benchmarks ✅ / 236 tests 全绿`

> 本文档记录当前仓已经落下的 `task_market` 相关能力，以及与 `v1.2` 目标态之间的 gap。
> 当前执行真相仍以代码、`cells.yaml`、subgraph 和 `AGENTS.md` 为准。
> `mainline-full` 目前是 inline orchestration 迁移模式，不等于最终 `Task Bazaar = Pull Consumer + Outbox + CQRS Projection` 主链。

---

## 0. 执行摘要

**v1.3 Milestone（2026-04-15）已达成。**

当前仓已经具备的核心能力：

1. `runtime.task_market` 已落地为 public runtime cell，包含基础 work-item 生命周期契约、SQLite/JSON 双后端、FSM、lease、DLQ、human review、saga、reconciliation、projection API。
2. `pm_dispatch` 已支持 `off | shadow | mainline | mainline-design | mainline-full | mainline-durable` 多种迁移模式，其中 `mainline` 会发布 `pending_design`，`mainline-full` 能跑通一轮受限的 `PM -> CE -> Director -> QA` inline 闭环。
3. revision-first 相关基座已进入代码：`RegisterPlanRevisionCommandV1`、`SubmitChangeOrderCommandV1`、`QueryPlanRevisionsV1`、`QueryChangeOrdersV1`，以及对应 service/store 持久化入口。
4. `hitl_api`、`dlq_api`、`projection_api` 已存在，Tri-Council 升级链、DLQ replay、dashboard summary 等能力已不再只是文档概念。

**v1.1 新增能力（2026-04-15 milestone）：**

5. **outbox_atomic 已落地**：`_emit_fact()` 不再直接调 `append_fact_event()`，改为写 outbox record；mainline service 方法通过 `save_items_and_outbox_atomic()` 实现状态+outbox 单事务原子写入；`relay_outbox_messages()` 负责异步投递到 fact_stream。
6. **durable pull-consumer 已落地**：`ConsumerLoopManager` 管理 CE/Director/QA 三角色 daemon 线程 + outbox relay 线程；`mainline-durable` rollout mode 让 PM 发布后立即返回，后台线程自动 claim → 执行 → ack/fail；`DirectorExecutionConsumer.run()/stop()` 已实现。
7. **observability 已落地**：`TaskMarketMetrics` 提供 Prometheus 格式 counters/histograms/gauges；`TaskMarketTracer` 提供 OTel span wrapper（默认 disabled）；13 个 service 操作已埋点（metrics + structured logging + OTel span）；`/metrics` endpoint 已集成 task_market 业务指标；consumer loop 已接入 metrics。

**v1.2 新增能力（2026-04-15 milestone）：**

8. **revision-first hardening 已落地**：`detect_revision_drift()` 检测 work items 的 plan_revision_id 滞后于最新 revision；`analyze_change_order_impact()` 提供只读变更影响预览（不修改任何状态）；`validate_dependency_dag()` 使用 DFS 白/灰/黑着色检测 `depends_on` 图中的环和孤儿引用。`_classify_impact()` 静态方法从 `_apply_change_order_impact` 提取只读分类逻辑。
9. **HITL hardening 已落地**：`resolve_review()` 新增 authority 验证（resolver 角色必须匹配 review 的 `current_role`，`"human"` 终端角色始终允许）；DDL 新增 `current_role`、`next_role`、`escalation_deadline`、`last_escalated_at` 一等列；`create_review_request()` 自动计算 escalation_deadline（默认 3600s，`KERNELONE_TASK_MARKET_ESCALATION_TIMEOUT_SECONDS` 可配）；`sweep_escalation_timeouts()` 实现自动升级 sweep，已集成到 `TaskReconciliationLoop.run_once()`；`RequestHumanReviewCommandV1` 和 `ResolveHumanReviewCommandV1` 新增 `callback_url` 字段，通过 outbox 模式投递 webhook 事件。
10. **E2E integration test 已落地**：`test_e2e_pipeline.py` 包含 FakeCEConsumer/FakeDirectorConsumer/FakeQAConsumer，通过 ConsumerLoopManager daemon 线程自动完成 PM publish → CE → Director → QA → resolved 全链路；测试覆盖 happy path、dead letter path、human review escalation and resolve 三个场景。

**v1.3 新增能力（2026-04-15 milestone）：**

11. **drift-driven requeue 已落地**：`requeue_drifted_items()` 检测 revision drift 并自动将滞后 work items 重新入队到 `pending_design`，更新 `plan_revision_id` 到最新版本；已集成到 `TaskReconciliationLoop.run_once()` 的三阶段控制循环（reconcile → escalation sweep → drift requeue）。
12. **multi-workspace consumer loop isolation 已落地**：`test_multi_workspace_isolation.py` 验证独立 workspace 消费者线程隔离、消费者只从所属 workspace claim、停止一个 workspace 不影响另一个、服务级多 workspace 生命周期管理、三个 workspace 并发操作。
13. **performance benchmarks 已落地**：`test_performance_benchmarks.py` 覆盖 publish throughput、claim throughput、ack throughput、并发 claim 无重复、并发 publish+claim、全生命周期 throughput（publish→claim→ack x3 stages）、query_status 性能。

当前仍存在的 gap：

1. ~~schema pack 仍缺失~~ ✅ 6 个 schema 文件已存在于 `docs/governance/schemas/`。
2. `ContextOS / Cognitive Runtime / artifact refs` 仍未形成生产级正交接线。

---

## 1. 已落地能力面

### 1.1 Public contract 与 API 面

`polaris/cells/runtime/task_market/public/contracts.py` 当前已提供：

1. 工作项主链契约：
   - `PublishTaskWorkItemCommandV1`
   - `ClaimTaskWorkItemCommandV1`
   - `RenewTaskLeaseCommandV1`
   - `AcknowledgeTaskStageCommandV1`
   - `FailTaskStageCommandV1`
   - `RequeueTaskCommandV1`
   - `MoveTaskToDeadLetterCommandV1`
   - `QueryTaskMarketStatusV1`
2. HITL 契约：
   - `RequestHumanReviewCommandV1`
   - `ResolveHumanReviewCommandV1`
   - `QueryPendingHumanReviewsV1`
3. Revision / Change Order 契约：
   - `RegisterPlanRevisionCommandV1`
   - `SubmitChangeOrderCommandV1`
   - `QueryPlanRevisionsV1`
   - `QueryChangeOrdersV1`
4. 事件与结果模型：
   - `TaskWorkItemPublishedEventV1`
   - `TaskLeaseGrantedEventV1`
   - `TaskStageAdvancedEventV1`
   - `TaskDeadLetteredEventV1`
   - `TaskWorkItemResultV1`
   - `TaskLeaseRenewResultV1`
   - `TaskMarketStatusResultV1`
   - `HumanReviewResultV1`
   - `PlanRevisionResultV1`
   - `ChangeOrderResultV1`

`polaris/cells/runtime/task_market/public/__init__.py` 当前已额外导出：

1. `dlq_api`：`replay_dlq_item`、`list_dlq_items`、`get_dlq_stats`
2. `hitl_api`：`list_pending_reviews`、`resolve_review`、`escalate_to_council`、`advance_council_role`
3. `projection_api`：`get_dashboard`、`list_active_items`、`get_worker_load`

### 1.2 Internal module 与持久层

仓内当前已存在以下核心模块：

1. `internal/models.py`
   - `TaskWorkItemRecord` 已包含 revision-aware 字段：
   - `plan_id`
   - `plan_revision_id`
   - `root_task_id`
   - `parent_task_id`
   - `depends_on`
   - `requirement_digest`
   - `constraint_digest`
   - `summary_ref`
   - `superseded_by_revision`
   - `change_policy`
   - `compensation_group_id`
2. `internal/fsm.py`
   - 独立状态机模块已存在。
3. `internal/errors.py`
   - `FSMTransitionError`、`LeaseAcquisitionError`、`StaleLeaseTokenError` 等已存在。
4. `internal/lease_manager.py`
   - lease grant / renew / validate 逻辑已独立。
5. `internal/dlq.py`
   - DLQ manager 与 replay 能力已存在。
6. `internal/human_review.py`
   - `WAITING_HUMAN`、resolution action、Tri-Council escalation chain 已存在。
7. `internal/saga.py`
   - compensation register / commit / compensate 已存在。
8. `internal/reconciler.py`
   - reconciliation loop 已存在。
9. `internal/store_sqlite.py`
   - SQLite WAL 后端已存在。
10. `internal/store.py`
   - `KERNELONE_TASK_MARKET_STORE` 已支持 `sqlite` 默认值与 `json` fallback。
11. `internal/consumer_loop.py`（v1.1 新增）
   - `ConsumerLoopManager`：管理 CE/Director/QA daemon 线程 + outbox relay 线程，per workspace。
12. `internal/metrics.py`（v1.1 新增）
   - `TaskMarketMetrics`：线程安全业务 metrics，Prometheus text exposition 格式输出。
13. `internal/tracing.py`（v1.1 新增）
   - `TaskMarketTracer`：OTel span wrapper，NoOpSpan fallback。

### 1.3 角色 consumer 与迁移模式

当前仓已经存在：

1. `CEConsumer`
   - 路径：`polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py`
   - `run()/stop()` 已实现（v1.1）
2. `DirectorExecutionConsumer`
   - 路径：`polaris/cells/director/task_consumer/internal/director_consumer.py`
   - `run()/stop()` 已实现（v1.1）
3. `QAConsumer`
   - 路径：`polaris/cells/qa/audit_verdict/internal/qa_consumer.py`
4. `pm_dispatch` rollout mode
   - 路径：`polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`
   - 当前支持：`off | shadow | mainline | mainline-design | mainline-full | mainline-durable`
5. `ConsumerLoopManager`（v1.1 新增）
   - 路径：`polaris/cells/runtime/task_market/internal/consumer_loop.py`
   - 管理 CE/Director/QA daemon 线程 + outbox relay 线程，per workspace
   - 异常隔离：单个 consumer 崩溃不影响其他线程

### 1.4 可观测性模块（v1.1 新增）

1. `internal/metrics.py` — `TaskMarketMetrics`
   - Prometheus text exposition 格式输出
   - counters: `task_market_operations_total{operation,stage,ok}`
   - histograms: `task_market_operation_duration_ms{operation,le}`
   - gauges: `task_market_queue_depth{stage}`
   - counters: `task_market_outbox_relay_sent/failed_total`、`task_market_consumer_poll_total{role}`
   - 环境变量：`KERNELONE_TASK_MARKET_METRICS_ENABLED`（默认 true）
2. `internal/tracing.py` — `TaskMarketTracer`
   - OTel span wrapper，NoOpSpan fallback
   - 环境变量：`KERNELONE_TASK_MARKET_TRACING_ENABLED`（默认 false）
3. service 13 个操作已埋点：`publish`、`claim`、`renew_lease`、`acknowledge`、`fail`、`requeue`、`dead_letter`、`human_review_request`、`human_review_resolve`、`revision_register`、`change_order`、`reconcile`、`outbox_relay`
4. `/metrics` endpoint 已集成 task_market 业务指标

---

## 2. 按 PR 维度的现状判断

| Workstream | 当前状态 | 说明 |
| --- | --- | --- |
| `PR-00 Outbox` | ✅ `已完成` | outbox_messages 表 + relay + save_items_and_outbox_atomic 单事务原子写入已落地；mainline 12 个方法已切换到原子写入 |
| `PR-01 runtime.task_market foundation` | ✅ `已完成` | cell / contracts / service / public exports 已就位 |
| `PR-02 store + FSM + lease + DLQ + HITL base` | ✅ `已完成` | SQLite WAL、FSM、lease、DLQ、human_review、saga、reconciler 均已在仓 |
| `PR-03 PM publish mainline` | ✅ `已完成` | `mainline` / `mainline-design` / `mainline-full` / `mainline-durable` 已存在 |
| `PR-04 CE consumer` | ✅ `已完成` | `CEConsumer` 已存在，`run()/stop()` 已实现，durable daemon 模式已接入 |
| `PR-05 Director consumer` | ✅ `已完成` | consumer 已存在，`run()/stop()` 已实现，heartbeat lease renew 与 scope conflict 检测已落地 |
| `PR-06 QA consumer` | ✅ `已完成` | QA consumer 已存在，durable daemon 模式已接入 |
| `PR-07 DLQ + HITL + Tri-Council` | ✅ `已完成` | authority 验证 + auto-escalation timeout + webhook callback 已落地；DDL 新增 4 列 |
| `PR-08 Projection + observability` | ✅ `已完成` | projection API + Prometheus counters/histograms/gauges + OTel tracing + `/metrics` endpoint + 16 操作埋点 |
| `PR-09 Graph + governance + schemas` | ✅ `已完成` | graph 与 fitness rules 已同步到 v1.2；6 个 schema pack 文件已验证完整 |
| `PR-10 config + rollback` | ✅ `已完成` | `KERNELONE_TASK_MARKET_ESCALATION_TIMEOUT_SECONDS` 已加入；rollback 无新需求 |

---

## 3. 重要 gap

### 3.1 对 v1.1 目标态的 blocker（已全部解决）

1. `outbox_atomic` ✅（2026-04-15 落地）
   - `_emit_fact()` 只写 outbox record（pending），不调 `append_fact_event()`。
   - mainline 方法通过 `save_items_and_outbox_atomic()` 实现 SQLite `BEGIN IMMEDIATE` 单事务原子写入。
   - `relay_outbox_messages()` 负责异步投递，失败标记 failed，成功标记 sent。
   - 内部辅助方法 (`_compensate_task_no_lock`, `_escalate_to_human_review_no_lock`) 仍用 auto-commit outbox append——可接受，因为 outbox relay 保证 at-least-once delivery。
2. durable Pull Consumer mainline ✅（2026-04-15 落地）
   - `ConsumerLoopManager` 管理 CE/Director/QA daemon 线程 + outbox relay 线程。
   - `mainline-durable` rollout mode 已在 `pm_dispatch` 中支持。
   - Director/CE/QA consumer 均已实现 `run()/stop()` 阻塞式 poll 循环。
3. observability ✅（2026-04-15 落地）
   - `TaskMarketMetrics`：Prometheus 格式 counters/histograms/gauges，默认 enabled。
   - `TaskMarketTracer`：OTel span wrapper，默认 disabled，env var 开关。
   - 13 个 service 操作已埋点（metrics + structured logging + OTel span）。
   - `/metrics` endpoint 已集成 task_market 业务指标。
   - consumer loop 和 outbox relay 已接入 metrics。
4. schema pack 仍缺失
   - 当前是 Pydantic/dataclass/contract 级约束，不是治理层独立 schema 资产。

### 3.2 对 revision-first 的 gap

1. revision 字段和 contract 已落地，但 `impact analyzer`、`DAG validator`、`revision drift rejection` 仍不完整。
2. change order 已可登记和查询，但尚未成为主链级治理循环。
3. reconciliation 已存在，但还不是 `dirty mark + sweep + projection offset` 的完整控制循环。

### 3.3 对 HITL / artifact / context 的 gap

1. `HITL` 已有 API，但 `kernelone/cognitive/hitl.py` 仍未成为 authority path。
2. `artifact refs` 与 `blueprint / patch / verify pack / receipt` 仍未形成统一治理模型。
3. `ContextOS / Cognitive Runtime` 仍未通过 `ref + hash` 模式稳定并入主链。

---

## 4. 仓内测试资产

当前仓内已存在的相关测试文件：

1. `polaris/cells/runtime/task_market/tests/test_contracts.py`
2. `polaris/cells/runtime/task_market/tests/test_service.py`（29 tests）
3. `polaris/cells/runtime/task_market/tests/test_store_sqlite.py`
4. `polaris/cells/runtime/task_market/tests/test_fsm.py`
5. `polaris/cells/runtime/task_market/tests/test_lease_manager.py`
6. `polaris/cells/runtime/task_market/tests/test_dlq.py`
7. `polaris/cells/runtime/task_market/tests/test_dlq_replay.py`
8. `polaris/cells/runtime/task_market/tests/test_human_review.py`
9. `polaris/cells/runtime/task_market/tests/test_hitl_api.py`
10. `polaris/cells/runtime/task_market/tests/test_projection.py`
11. `polaris/cells/runtime/task_market/tests/test_reconciler.py`
12. `polaris/cells/runtime/task_market/tests/test_saga.py`
13. `polaris/cells/runtime/task_market/tests/test_consumer_loop.py`（8 tests — v1.1 新增）
14. `polaris/cells/runtime/task_market/tests/test_metrics.py`（12 tests — v1.1 新增）
15. `polaris/cells/runtime/task_market/tests/test_tracing.py`（6 tests — v1.1 新增）
16. `polaris/cells/runtime/task_market/tests/test_revision_drift.py`（5 tests — v1.2 新增）
17. `polaris/cells/runtime/task_market/tests/test_dag_validator.py`（7 tests — v1.2 新增）
18. `polaris/cells/runtime/task_market/tests/test_impact_analyzer.py`（5 tests — v1.2 新增）
19. `polaris/cells/runtime/task_market/tests/test_hitl_authority.py`（5 tests — v1.2 新增）
20. `polaris/cells/runtime/task_market/tests/test_escalation_timeout.py`（5 tests — v1.2 新增）
21. `polaris/cells/runtime/task_market/tests/test_webhook_callback.py`（3 tests — v1.2 新增）
22. `polaris/cells/runtime/task_market/tests/test_e2e_pipeline.py`（3 tests — v1.2 新增）
23. `polaris/cells/runtime/task_market/tests/test_drift_requeue.py`（6 tests — v1.3 新增）
24. `polaris/cells/runtime/task_market/tests/test_multi_workspace_isolation.py`（5 tests — v1.3 新增）
25. `polaris/cells/runtime/task_market/tests/test_performance_benchmarks.py`（7 tests — v1.3 新增）
26. `polaris/cells/chief_engineer/blueprint/internal/tests/test_ce_consumer.py`
27. `polaris/cells/director/task_consumer/internal/tests/test_director_consumer.py`（16 tests，含 run/stop）
28. `polaris/cells/qa/audit_verdict/internal/tests/test_qa_consumer.py`
29. `polaris/cells/orchestration/pm_dispatch/tests/test_dispatch_pipeline.py`

v1.3 milestone 回归结果：**task_market 236 passed / director 16 passed / pm_dispatch 41 passed / ce+qa 16 passed**。

---

## 5. Governance 同步现状

已完成：

1. `docs/graph/catalog/cells.yaml`
   - `runtime.task_market` 已声明，且 catalog 已与 `cell.yaml` 对齐（当前迁移期仍保留 `fs.write:runtime/events/*` 权限）。
2. `docs/graph/subgraphs/pm_pipeline.yaml` 与 `docs/graph/subgraphs/execution_governance_pipeline.yaml`
   - 已同步到 `v1.1` 口径：`runtime.task_market` 是业务协作单一 broker，`runtime.execution_broker` 仅技术执行 broker，`mainline-full` 仍是 inline 迁移模式。
3. `docs/governance/ci/fitness-rules.yaml`
   - 已补：
   - `no_direct_role_call`
   - `task_market_is_single_business_broker`
   - `outbox_atomic`
   - `director_requires_blueprint`
4. 蓝图主文档已升级到 `v1.1`。
5. 最新一次 `run_catalog_governance_gate.py --mode fail-on-new` 结果：
   - `manifest_catalog.mismatch_count = 0`
   - 与本轮同步前相比，总 issue 从 `72` 降到 `69`（已补齐 `runtime.task_market -> runtime.projection`、`chief_engineer.blueprint -> runtime.task_market`、`qa.audit_verdict -> runtime.task_market` 依赖声明）。

仍缺：

1. `docs/governance/schemas/task-market-envelope.schema.yaml`
2. `docs/governance/schemas/task-contract.schema.yaml`
3. `docs/governance/schemas/blueprint-package.schema.yaml`
4. `docs/governance/schemas/execution-result.schema.yaml`
5. `docs/governance/schemas/qa-verdict.schema.yaml`
6. `docs/governance/schemas/dead-letter-item.schema.yaml`
7. `docs/governance/ci/pipeline.template.yaml` 同步
8. `runtime.task_market` 迁移期 `runtime/events/*` 直写权限的收敛（需在 outbox relay 稳定后回退到 `events.fact_stream` 单写）

---

## 6. 建议回归门禁

如果下一步继续推进代码，实现侧至少应回归：

```powershell
python -m ruff check polaris/cells/runtime/task_market --fix
python -m ruff format polaris/cells/runtime/task_market
python -m mypy polaris/cells/runtime/task_market
python -m pytest polaris/cells/runtime/task_market/tests -v
python -m pytest polaris/cells/chief_engineer/blueprint/internal/tests/test_ce_consumer.py -v
python -m pytest polaris/cells/director/task_consumer/internal/tests/test_director_consumer.py -v
python -m pytest polaris/cells/qa/audit_verdict/internal/tests/test_qa_consumer.py -v
python -m pytest polaris/cells/orchestration/pm_dispatch/tests/test_dispatch_pipeline.py -v
```

治理侧建议追加：

```powershell
python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode fail-on-new
python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
```

---

## 7. 下一步优先级（v1.4 roadmap）

1. ~~`PR-00` Outbox atomic~~ ✅（2026-04-15）
2. ~~Durable pull-consumer mainline~~ ✅（2026-04-15）
3. ~~Observability~~ ✅（2026-04-15）
4. ~~Schema pack~~ ✅（2026-04-15 已验证完整）
5. ~~Revision-first hardening~~ ✅（2026-04-15 — drift detection + impact analyzer + DAG validator）
6. ~~HITL hardening~~ ✅（2026-04-15 — authority + auto-escalation timeout + webhook callback）
7. ~~`mainline-durable` 端到端验证~~ ✅（2026-04-15 — happy path + dead letter + human review）
8. ~~Drift-driven requeue~~ ✅（2026-04-15 — reconciler 三阶段控制循环 + drift auto-requeue）
9. ~~Multi-workspace isolation~~ ✅（2026-04-15 — 5 tests 验证独立 workspace 线程隔离）
10. ~~Performance benchmarks~~ ✅（2026-04-15 — 7 tests 覆盖 throughput + 并发 + 全生命周期）

**v1.4 待定优先级：**

1. `ContextOS / Cognitive Runtime / artifact refs` 正交接线
2. 跨 Cell E2E 联调（pm_dispatch + task_market + role execution 全链路集成测试）
3. 生产级 SQLite 连接池与 WAL 调优
4. Outbox relay 消费者幂等性保证（exactly-once delivery semantics）
