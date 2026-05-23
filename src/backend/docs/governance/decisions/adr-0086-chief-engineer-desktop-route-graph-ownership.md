---
status: accepted
date: 2026-05-23
---

# ADR-0086: Chief Engineer Desktop Route Graph Ownership

## 背景

PM 与 Director 的 v2 desktop/backend routes 已在 graph catalog 中分别归属到
`orchestration.pm_planning` 与 `director.execution`。Chief Engineer 的桌面
工作台同样依赖 `/v2/chief-engineer/*`，但对应 route
`polaris.delivery.http.v2.chief_engineer` 没有被登记到 catalog 或
`chief_engineer.blueprint/cell.yaml`。

同时，Chief Engineer 蓝图持久化实际写入
`{workspace}/runtime/blueprints/*.json`，但 cell manifest 只声明了
`runtime/state/blueprints/*`。

## 决策

1. `polaris.delivery.http.v2.chief_engineer` 归属 `chief_engineer.blueprint`。
2. `polaris/delivery/http/v2/chief_engineer.py` 加入 catalog 与 cell manifest
   的 `owned_paths`。
3. `runtime/blueprints/*` 加入 cell manifest 的 `state_owners`。
4. `fs.write:runtime/blueprints/*` 加入 cell manifest 的 `effects_allowed`。
5. 添加 governance regression，确保 Chief Engineer desktop route 和
   runtime blueprint persistence 不再脱离 graph/cell 事实。

## 后果

正向：

1. PM/Chief Engineer/Director 三个桌面角色的 v2 backend routes 都有显式
   graph owner。
2. Chief Engineer 蓝图持久化的状态和写入副作用可审计。
3. 后续 Agent 不需要从源码倒推 Chief Engineer route 归属。

代价：

1. `chief_engineer.blueprint` 继续作为迁移期能力 Cell 承载一个 delivery
   adapter owned path；未来若拆出独立 delivery Cell，需要迁移该 owned path。
2. Descriptor/context pack 未在本次局部修复中批量再生成，避免无关 Cell 的
   generated artifact churn；后续进行 descriptor wave 时应刷新。
