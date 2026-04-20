# Revision-First 落地计划

- 版本：`v1.1 sync`
- 日期：`2026-04-14`
- 状态：`Execution Plan`
- 目标：在现有 `runtime.task_market` 基础上，把 revision-first 从字段存在提升到正式主链治理

> 当前仓并不是从 `WP-1` 零开始。revision-aware 字段、plan revision、change order、human review、projection API 已进入代码。
> 因此这份计划的重点不是“创建概念”，而是“补齐 authority、validator、outbox 和 durable mainline”。

---

## WP-00 Outbox Baseline

### 目标

先补 `outbox_atomic`，否则 revision/change order 与 fact publication 仍可能撕裂。

### 文件

1. `polaris/cells/runtime/task_market/internal/store_sqlite.py`
2. `polaris/cells/runtime/task_market/internal/service.py`
3. `polaris/cells/runtime/task_market/internal/outbox.py` 或等价模块
4. `polaris/cells/events/fact_stream/public/service.py`
5. `polaris/cells/runtime/task_market/tests/test_service.py`

### 交付

1. `outbox_messages` 表或等价持久化结构
2. relay worker / relay loop
3. state transition 与 fact emit 的 durable bind

### 完成标准

1. `task_market` 不再直接 best-effort 调 `append_fact_event`
2. fact relay 失败不丢状态，也不丢待发布事实
3. 为后续 revision/change_order 发布提供原子基础

### 当前状态

`未完成`

---

## WP-01 Revision Hardening

### 目标

把已存在的 revision-aware 字段和 contract 变成真正的主链约束。

### 已有基础

1. `PublishTaskWorkItemCommandV1` 已支持 revision/root/parent/dependency/digest 字段
2. `TaskWorkItemRecord` 已支持这些字段的持久化
3. `RegisterPlanRevisionCommandV1` / `SubmitChangeOrderCommandV1` 已存在

### 文件

1. `polaris/cells/runtime/task_market/public/contracts.py`
2. `polaris/cells/runtime/task_market/internal/models.py`
3. `polaris/cells/runtime/task_market/internal/service.py`
4. `polaris/cells/runtime/task_market/tests/test_contracts.py`
5. `polaris/cells/runtime/task_market/tests/test_service.py`

### 交付

1. claim / ack 前的 revision drift 校验
2. `plan_revision_id + requirement_digest + constraint_digest` 的一致性门禁
3. stale revision 的标准错误语义

### 完成标准

1. 旧 revision 的 worker 提交会被拒绝或回流
2. change order 后，旧任务不会静默原地改语义
3. 旧调用方在字段缺省情况下仍能兼容运行

### 当前状态

`部分完成`

---

## WP-02 DAG Validator + Impact Analyzer

### 目标

让 revision change 真正驱动任务重判，而不是停留在记录层。

### 文件

1. `polaris/cells/orchestration/pm_planning/internal/dag_validator.py`
2. `polaris/cells/orchestration/pm_planning/internal/impact_analyzer.py`
3. `polaris/cells/orchestration/pm_planning/internal/change_order.py`
4. `polaris/cells/runtime/task_market/internal/service.py`
5. `相关测试`

### 交付

1. revision-scoped DAG 校验
2. cycle path 输出
3. impact set 分类：
   - `unaffected`
   - `needs_revalidation`
   - `superseded`
   - `cancel_and_compensate`

### 完成标准

1. PM 发布前可拒绝非法 DAG
2. change order 能映射到明确 impact set
3. `supersede / revalidate / cancel_requested` 成为显式状态治理动作

### 当前状态

`未完成`

---

## WP-03 PM Mainline Revision Authority

### 目标

让 PM 不只是发布 `pending_design`，还要成为 revision authority 的上游入口。

### 文件

1. `polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`
2. `polaris/cells/orchestration/pm_dispatch/tests/test_dispatch_pipeline.py`
3. `polaris/cells/orchestration/pm_planning/**`

### 交付

1. 发布 `pending_design` 时带完整 revision identity
2. 在 `mainline` / `mainline-design` 下登记 plan revision
3. change order 与 task publish 的顺序受控

### 完成标准

1. PM 发布的设计任务都能追溯到唯一 `plan_revision_id`
2. `mainline-full` 不会绕开 revision 记录
3. PM 不再静默重写旧任务语义

### 当前状态

`部分完成`

---

## WP-04 Durable Consumers

### 目标

把现在已经存在的 CE / Director / QA consumer 从 inline bounded loop 提升为 durable pull-consumer mainline。

### 文件

1. `polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py`
2. `polaris/cells/director/task_consumer/internal/director_consumer.py`
3. `polaris/cells/qa/audit_verdict/internal/qa_consumer.py`
4. `polaris/cells/runtime/task_market/internal/service.py`
5. `相关测试`

### 交付

1. pull-based claim/ack mainline
2. heartbeat / renew / stale ack reject
3. blueprint strict gate
4. revision drift enforcement on consumer side

### 完成标准

1. `mainline-full` 不再承担长期主链角色
2. worker 崩溃后任务依赖 lease 自动恢复
3. execution 不再绕开 blueprint/revision checks

### 当前状态

`部分完成`

---

## WP-05 HITL / DLQ / Compensation Authority

### 目标

把已存在的 human review / DLQ API 从 helper 能力提升为正式 authority 路径。

### 文件

1. `polaris/cells/runtime/task_market/internal/human_review.py`
2. `polaris/cells/runtime/task_market/public/hitl_api.py`
3. `polaris/cells/runtime/task_market/internal/dlq.py`
4. `polaris/cells/runtime/task_market/internal/saga.py`
5. `相关测试`

### 交付

1. Tri-Council escalation progression
2. DLQ replay policy
3. revision-driven compensation path
4. timeout / SLA / escalation metadata

### 完成标准

1. `waiting_human` 不只是 stage 名称，而是正式治理闭环
2. DLQ replay 受 policy 与审计保护
3. change order 导致的 supersede 能触发补偿路径

### 当前状态

`部分完成`

---

## WP-06 Projection / Schema / Observability

### 目标

补齐 v1.1 需要的读模型和治理资产。

### 文件

1. `polaris/cells/runtime/projection/task_market_projection.py`
2. `polaris/cells/runtime/task_market/public/projection_api.py`
3. `docs/governance/schemas/*.yaml`
4. `docs/governance/ci/fitness-rules.yaml`
5. 相关 dashboard / projection / trace 测试

### 交付

1. revision-aware dashboard summary
2. governance schema pack
3. `OpenTelemetry` / metrics 接线
4. `dirty mark + sweep` reconcile 可观测性

### 完成标准

1. dashboard 不再只是本地 helper summary
2. 关键 message model 有 schema asset
3. trace / backlog / DLQ / worker load 可观测

### 当前状态

`部分完成`

---

## 建议门禁

每个 Work Package 结束后至少执行：

```powershell
python -m ruff check <changed_python_files> --fix
python -m ruff format <changed_python_files>
python -m mypy polaris/cells/runtime/task_market
python -m pytest polaris/cells/runtime/task_market/tests -v
```

涉及 `pm_planning` / `pm_dispatch` / consumer 时追加：

```powershell
python -m mypy polaris/cells/orchestration/pm_planning polaris/cells/orchestration/pm_dispatch
python -m pytest polaris/cells/orchestration/pm_dispatch/tests/test_dispatch_pipeline.py -v
python -m pytest polaris/cells/chief_engineer/blueprint/internal/tests/test_ce_consumer.py -v
python -m pytest polaris/cells/director/task_consumer/internal/tests/test_director_consumer.py -v
python -m pytest polaris/cells/qa/audit_verdict/internal/tests/test_qa_consumer.py -v
```

治理回归建议：

```powershell
python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode fail-on-new
```

---

## 当前回合建议落地范围

本回合最值得优先推进的不是继续加新 worker，而是：

1. `WP-00 Outbox Baseline`
2. `WP-01 Revision Hardening`

没有这两层，revision-first 仍会停留在“字段和 API 已有”，而不是“主链真的按最新语义工作”。
