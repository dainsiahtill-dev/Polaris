# KernelOne Domain-Aware Runtime 融合蓝图

状态: Draft  
日期: 2026-03-25  
范围: `polaris/cells/roles/runtime/`、`polaris/cells/roles/profile/`、`polaris/kernelone/context/`、`polaris/cells/roles/kernel/`

> 本文是蓝图，不是 graph truth。  
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md`、Cell manifest 为准。

---

## 1. 决策摘要

我们把“所属领域”提升为运行时一等输入，与 role 并列参与策略决策，形成统一的执行路由轴：

`Role + Domain + SessionOverride -> StrategyProfile -> RoleOverlay -> Runtime Turn`

核心裁决：

1. Domain 不是 UI 标签，而是 `roles.runtime` 公共契约字段。
2. Domain 解析必须零信任，统一规范化后再进入策略解析。
3. Role 与 Domain 解耦：治理角色保持稳定，执行域能力通过 domain + overlay 组合表达。
4. 向后兼容优先：未传 domain 时先走角色默认域（PM/Architect/ChiefEngineer=`document`，Director=`code`），未知角色再回退 `code`。

---

## 2. 目标与非目标

### 2.1 目标

1. 支持同一 role 在不同工作域下使用不同策略（写代码、写作、研究、通用任务）。
2. 让 `RoleRuntimeService` 成为唯一 domain 解析与策略握手入口。
3. 建立可审计链路：请求 -> 规范化 domain -> profile/overlay 选择 -> receipt/debug。
4. 为 Phase 2 深度接入（Prompt Chunk / Repo Intelligence / Reasoning Strip）提供统一路由前提。

### 2.2 非目标

1. 本轮不新增顶层治理角色。
2. 本轮不改写 graph truth 与 Cell owner 边界。
3. 本轮不把 domain 判断散落到 delivery/host 层。

---

## 3. Domain 语义模型

### 3.1 Canonical Domain 集合

1. `code`: 写代码、调试、重构、测试落地。
2. `document`: 写文档、写方案、写报告、结构化写作。
3. `research`: 调研、分析、归因、方案对比。
4. `general`: 其他通用工作，执行时映射到 code 基础策略以保证稳定。

### 3.2 Alias 归一化（零信任）

1. `coding/dev/engineering/programming -> code`
2. `writing/docs/documentation -> document`
3. `analysis/investigation -> research`
4. `other/others/misc -> general`

---

## 4. 融合架构

### 4.1 请求入口与契约

`ExecuteRoleTaskCommandV1` 与 `ExecuteRoleSessionCommandV1` 新增可选字段：

1. `domain: str | None`
2. 统一在 contracts 层做 lower/trim/null-normalize

运行时接入顺序：

1. command.domain
2. context["domain"]
3. metadata["domain"]
4. role default domain (`pm/architect/chief_engineer -> document`, `director -> code`)
5. global fallback `code`

### 4.2 Runtime 解析与策略握手

`RoleRuntimeService` 负责：

1. `_resolve_execution_domain`: 统一解析 execution domain。
2. `_strategy_domain_from_execution`: `general -> code` 映射，避免策略空洞。
3. `resolve_strategy_profile`: 支持 `prefer_domain_default`，控制“domain 默认优先”还是“role 默认优先”。
4. `create_strategy_run`: 把 domain 写入 `StrategyRunContext`，作为 run identity 一部分。
5. `resolve_strategy`: overlay 自动选择时加入 domain 偏好。

### 4.3 Overlay 选择规则

`RoleOverlayRegistry` 按以下优先级选 overlay：

1. `(role + parent_profile + target_domain)` 精确命中
2. `(role + target_domain)` 域优先命中
3. `(role + parent_profile)` 传统命中
4. 该 role 第一个已注册 overlay 回退

这让同一 role 在不同 domain 下能自动切换执行变体，例如：

1. `director + code -> director.execution / director.coder`
2. `director + document -> director.writer`

### 4.4 Context 与安全链路

Domain-aware 只改变“策略选择”，不改变边界铁律：

1. `ReasoningStripper` 仍在 history materialization 前执行。
2. Prompt chunk 预算仍受统一 budget gate 控制。
3. 所有副作用仍走 Cell -> KernelOne contract -> adapter 链路。

---

## 5. 与现有能力融合映射

Domain 路由生效后，Phase 2 对接按“共享底座优先”落地：

1. `code`  
   RepoIntelligenceFacade + symbol/ranker + code-oriented chunk taxonomy。
2. `document`  
   outline/section-first chunk 组装，弱化 repo map 开销。
3. `research`  
   证据链与引用优先，强化 history/receipt 可追溯性。
4. `general`  
   保守映射到 code foundation，避免未知路径引发退化。

---

## 6. 兼容性与回滚

1. 未传 domain：按角色默认域解析（PM/Architect/ChiefEngineer=`document`，Director=`code`）。
2. domain 非法值：规范化失败后回退到角色默认域；未知角色回退 `code`。
3. 回滚开关：可临时禁用 domain-first（保持 role-first）而不撤销字段。

---

## 7. 蓝图验收标准

1. Domain 在 contracts/runtime/request/strategy/receipt 链路全程可追踪。
2. 至少覆盖 3 类测试：
   - domain 规范化
   - role+domain overlay 命中
   - request 构建传播
3. 不修改 graph truth，不引入第二套边界真相。
4. 默认路径（无 domain 输入）与角色语义一致，且未知角色保持 `code` 兼容回退。
