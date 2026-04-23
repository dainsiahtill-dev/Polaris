# CLI SUPER Mode Full Pipeline Orchestration Blueprint

日期: 2026-04-23
状态: Draft
作者: Codex

## 1. 背景

当前 `polaris.delivery.cli.super_mode` 更像一个意图路由器，而不是一个完整的多角色流水线编排器。

现状问题：

1. `SUPER` 只能做一次性 `roles=(...)` 跳转，缺少显式阶段状态。
2. Architect / PM / Chief Engineer / Director 之间主要依赖文本 handoff，没有把 `runtime.task_market` 作为唯一业务 broker。
3. PM 已有任务落盘与发布能力，但默认直接把任务投递到 `pending_exec`，绕过了 `ChiefEngineer -> pending_exec` 的正式链路。
4. Chief Engineer 与 Director 的现有 consumer / contracts 已存在，但 CLI SUPER 没有复用这些公开契约去做 claim / ack / requeue。

这导致 `SUPER` 无法稳定跑完整链路：

`Architect -> PM -> runtime.task_market -> ChiefEngineer -> runtime.task_market -> Director`

## 2. 目标

把 CLI `SUPER` 升级为一个显式的、可审计的阶段编排器，满足以下要求：

1. Architect 负责理解需求、生成方案和文档型输出。
2. PM 负责拆解任务，并通过 `runtime.task_market` 发布 `pending_design` 工作项。
3. Chief Engineer 从 `runtime.task_market` 领取 `pending_design`，生成 blueprint，并把任务推进到 `pending_exec`。
4. Director 从 `runtime.task_market` 领取 `pending_exec`，执行代码修改。
5. 整个链路复用已有 graph truth 与公开契约，不新造第二套业务 broker。

## 3. Graph 对齐

以如下 graph truth 为准：

1. `docs/graph/subgraphs/pm_pipeline.yaml`
2. `docs/graph/subgraphs/execution_governance_pipeline.yaml`

关键链路：

```text
Architect(readonly/spec)
  -> PM(readonly/task planning)
  -> runtime.task_market.publish(stage=pending_design)
  -> ChiefEngineer.claim(stage=pending_design)
  -> runtime.task_market.ack(next_stage=pending_exec)
  -> Director.claim(stage=pending_exec)
  -> Director.execute(code changes)
```

## 4. 设计原则

### 4.1 Stage machine first

`SUPER` 不再只返回 `roles=("pm", "director")`，而是返回一个带阶段语义的 plan。

建议阶段：

1. `architect_plan`
2. `pm_publish`
3. `chief_engineer_blueprint`
4. `director_execute`
5. `qa_verify`（本轮先保留扩展位）

### 4.2 Task market is business broker

`runtime.task_market` 是唯一业务 broker。

因此：

1. PM 发布后默认落到 `pending_design`
2. Chief Engineer 从 `pending_design` 领取并推进到 `pending_exec`
3. Director 从 `pending_exec` 领取并执行

### 4.3 Role switch must stay visible

CLI `SUPER` 仍然通过 role console 显式切换角色执行每个阶段，而不是完全退化成后台 service 调用。

### 4.4 Handoff contract must be structured

新增或固化以下 handoff message：

1. `SUPER_MODE_ARCHITECT_HANDOFF`
2. `SUPER_MODE_PM_HANDOFF`
3. `SUPER_MODE_CE_HANDOFF`
4. `SUPER_MODE_DIRECTOR_TASK_HANDOFF`

其中 CE / Director handoff 必须携带：

1. `task_id`
2. `lease_token`
3. `trace_id`
4. `stage`
5. `payload`

## 5. 运行时模型

### 5.1 SuperRouteDecision 升级

在 `super_mode.py` 中把路由结果从简单 roles 列表提升为：

```text
SuperRouteDecision
- roles
- reason
- fallback_role
- pipeline_kind
- use_architect
- use_pm
- use_chief_engineer
- use_director
```

### 5.2 SUPER pipeline context

在 `terminal_console.py` 内部维护一个轻量 pipeline context：

```text
SuperPipelineContext
- original_request
- architect_output
- pm_output
- extracted_tasks
- published_task_ids
- ce_claims
- director_claims
```

该 context 只作为 turn 内 orchestrator 的本地工作上下文，不作为新的持久化业务真相。

### 5.3 Task publication policy

`_persist_super_tasks_to_board()` 需要支持指定 stage：

1. `pm -> pending_design`
2. `chief_engineer -> pending_exec` 由 ack 推进，不由 PM 直接发布

## 6. 执行流程

### 6.1 Architect stage

输入：原始用户请求

输出：

1. 文本方案
2. 可选 TASK_LIST

### 6.2 PM stage

输入：

1. 原始请求
2. Architect 输出

输出：

1. TASK_LIST
2. 任务被发布到 `runtime.task_market.pending_design`

### 6.3 Chief Engineer stage

输入：

1. task market claim 结果
2. PM task payload
3. 原始请求 / architect 摘要 / PM 摘要

输出：

1. blueprint 文本
2. `runtime.task_market.ack(next_stage=pending_exec, metadata={blueprint_id,...})`

### 6.4 Director stage

输入：

1. task market claim 结果
2. blueprint metadata
3. PM / Architect 摘要

输出：

1. 实际代码修改
2. 多回合执行直到完成

## 7. 边界与非目标

本轮不做：

1. 把 QA 全量拉入 SUPER 主链
2. 改造 task_market 的核心 FSM
3. 改造角色自身 prompt 体系

本轮只做 CLI SUPER orchestration 的接线与编排增强。

## 8. 风险

1. PM/Architect 输出为空时，后续阶段可能没有足够上下文。
2. Chief Engineer / Director 如未正确消费 task payload，可能出现 claim 成功但业务执行脱节。
3. 如果 CLI 直接用 service claim/ack，需要避免与后台 consumer 重复消费同一任务。

## 9. 验证计划

1. `test_super_mode_router_routes_contextos_request_to_full_pipeline`
2. `test_run_role_console_super_mode_full_pipeline_claims_ce_then_director`
3. `test_persist_super_tasks_to_board_publishes_pending_design`
4. `test_super_mode_director_loop_still_continues_until_complete`

质量门禁：

1. `ruff check`
2. `ruff format`
3. `mypy`
4. `pytest`
