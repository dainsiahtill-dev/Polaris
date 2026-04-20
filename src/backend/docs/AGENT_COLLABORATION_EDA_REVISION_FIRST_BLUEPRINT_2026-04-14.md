# Revision-First EDA Task Market Blueprint

- 版本：`v1.1 sync companion`
- 日期：`2026-04-14`
- 状态：`Companion Blueprint`
- 适用范围：`runtime.task_market` 上所有会受到需求漂移、任务改写、验收变更、人工介入影响的多 Agent 工作流

> 本文档是 `docs/AGENT_COLLABORATION_EDA_TASK_MARKET_BLUEPRINT_2026-04-14.md` 的配套蓝图，不是第二套 authority。
> 如与主蓝图、`cells.yaml` 或 subgraph 冲突，以 graph 真相和主蓝图为准。
> 这份文档回答的问题是：当任务发布后，需求继续变化时，Task Bazaar 如何保持真相不撕裂。

---

## 1. 核心判断

原始 Task Bazaar 蓝图已经给出了：

1. `Pull -> Compute -> Push`
2. `Task Market + Fact Stream + Lease + DLQ`
3. `Saga + CQRS + Outbox`

但在真实多 Agent 系统里，仅有这些还不够。原因很简单：

**需求变更不是异常，而是常态事件。**

只要允许以下任一行为发生：

1. 人类修改计划文档
2. PM 改优先级、改验收、改 scope
3. 已发布任务被重拆、取消、 supersede
4. QA 在执行中发现必须回到设计阶段

那么系统就必须先回答 revision 问题，否则所有 consumer 都可能在过期语义上继续工作。

---

## 2. 当前代码事实

这不是从零设计。当前仓已经具备 revision-first 的部分基座：

1. `PublishTaskWorkItemCommandV1` 与 `TaskWorkItemRecord` 已包含：
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
2. `runtime.task_market` 已提供：
   - `RegisterPlanRevisionCommandV1`
   - `SubmitChangeOrderCommandV1`
   - `QueryPlanRevisionsV1`
   - `QueryChangeOrdersV1`
3. `human_review`、`reconciler`、`saga` 已进入代码，说明系统已经具备吸纳“变更后重判”的基础点位。

当前真正缺的不是字段，而是以下治理行为：

1. revision drift detection
2. impact analysis
3. supersede / revalidate / compensate 的正式状态机
4. revision-aware reconciliation
5. 与 `outbox`、projection、HITL 的统一接线

---

## 3. Truth Hierarchy

revision-first 口径下，真相层级固定如下：

1. `plan revision`
   - 需求真相
2. `task market state`
   - 执行真相
3. `fact stream`
   - 审计真相
4. `summary / dashboard / projection`
   - 派生视图，不是架构真相

任何“原地改旧任务语义”的方案，都违反这条层级。

---

## 4. Mutation Discipline

需求修改不得通过静默覆盖旧任务实现，必须显式经过：

1. `register plan revision`
2. `submit change order`
3. `impact analysis`
4. `supersede / needs_revalidation / cancel_requested`
5. `reconcile`

这意味着：

1. 旧任务可以结束，但不能被偷偷改意义。
2. 新需求必须带新的 `plan_revision_id`。
3. summary / context / artifact 也必须按 revision 失效与重建。

---

## 5. Worker Discipline

所有角色 worker 统一遵守：

1. `Pull`
2. `Validate Revision`
3. `Compute`
4. `Push`

提交前至少验证：

1. `plan_revision_id`
2. `requirement_digest`
3. `constraint_digest`
4. 相关 `change_order` 是否已 supersede 当前任务

如果 revision 漂移，worker 不得继续提交旧结果，而应：

1. `requeue_design`
2. `requeue_exec`
3. `needs_revalidation`
4. `waiting_human`

---

## 6. Change Order Model

建议把 change order 固化为正式治理资产，而不是 metadata 拼接：

```text
change_order_id
plan_id
from_revision_id
to_revision_id
change_type
changed_by
reason
doc_refs
affected_tasks
created_at
```

`change_type` 至少包括：

1. `doc_patch`
2. `scope_add`
3. `scope_remove`
4. `acceptance_patch`
5. `priority_patch`
6. `manual_task_edit`
7. `task_cancel`

---

## 7. Impact Analysis

Change order 到来后，不应直接修改活跃任务，而应输出 impact set：

1. `unaffected`
2. `needs_revalidation`
3. `superseded`
4. `cancel_and_compensate`

推荐规则：

1. `queued` 任务
   - 直接 supersede 或重发新 revision
2. `in_progress` 任务
   - 标记 `cancel_requested`
   - 在 heartbeat / ack 时检查是否终止
3. `resolved` 任务
   - 标记 `needs_revalidation`
4. `dead_letter` / `waiting_human`
   - 由 PM / HITL 决定是否迁移到新 revision

---

## 8. DAG 与层级任务

只要 PM 允许手工拆分或 revision 重排，就必须有 revision-scoped DAG validator：

1. 所有 `depends_on` 必须在当前 `plan_revision_id` 中可解析，或者显式声明为外部依赖。
2. 发现 cycle 时必须拒绝发布。
3. `root_task_id` / `parent_task_id` 必须服务于 reconciliation，而不是仅做展示字段。

当前代码已经有层级字段，但 DAG validator 尚未落地为正式发布门禁。

---

## 9. Saga / Compensation

revision-first 与 Saga 的关系非常直接：

1. 任务被 supersede 时，系统不仅要改 stage，还要判断副作用是否需要补偿。
2. compensation 必须绑定 effect receipt，而不是只绑定“任务成功了什么”。
3. revision 变化触发的补偿链，与执行失败触发的补偿链，应走同一套 registry。

因此：

1. `revision-first` 不会替代 `Saga`
2. `revision-first` 反而要求更严格的 effect receipt discipline

而这又进一步说明 `outbox_atomic` 是 blocker，而不是可选增强。

---

## 10. Reconciliation

事件驱动用于推进，reconciliation 用于收敛。revision-first 口径下尤其如此。

推荐混合模式：

1. `event-triggered dirty mark`
2. `periodic sweep`

dirty 来源至少包括：

1. child status changed
2. change order arrived
3. revision superseded
4. summary invalidated
5. compensation completed / failed
6. human review resolved

对账目标至少包括：

1. parent aggregate status
2. stale revision tasks
3. invalid summary refs
4. compensation backlog
5. pending HITL backlog

---

## 11. HITL 在 revision-first 中的角色

`waiting_human` 不能只表示“失败后人工救火”，还应表示：

1. revision 冲突无法自动裁决
2. 跨 revision 迁移需要审批
3. compensation 失败需要兜底
4. 高风险 scope change 需要人工放行

这也是为什么 `RequestHumanReviewCommandV1`、`ResolveHumanReviewCommandV1` 已经落地后，下一步仍必须把 authority path 接到主链，而不是停留在 helper API。

---

## 12. 与主蓝图的关系

revision-first 不是要取代 Task Bazaar，而是对主蓝图做四个约束增强：

1. 所有 work item 都必须有 revision identity。
2. 所有 consumer ack 前都必须校验 revision digest。
3. 所有 supersede / revalidate / compensate 都必须有审计证据。
4. 所有 summary / projection / context 都必须按 revision 失效与重建。

一句话：

**Task Bazaar 解决“谁来做、做到哪一步”，Revision-First 解决“当前做的这件事，语义还是不是最新的”。**

---

## 13. 当前最值得推进的落点

基于当前仓现状，下一步优先级建议是：

1. 在 `task_market` 上补 `outbox`，先解决状态与事实原子性。
2. 在 `publish / claim / ack` 路径补 revision drift enforcement。
3. 在 PM 发布侧补 DAG validator 与 impact analyzer。
4. 在 reconciliation 侧补 revision-aware dirty mark 与 parent roll-up。
5. 最后再做 summary / dashboard / broader HITL authority。

如果顺序反过来，系统会继续在过期语义上堆派生资产，后续更难收敛。
