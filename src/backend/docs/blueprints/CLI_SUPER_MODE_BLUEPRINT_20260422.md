# CLI SUPER Mode Blueprint

Date: 2026-04-22
Status: Proposed
Scope: `polaris/delivery/cli/**`

## 1. Goal

为 CLI console 增加 `--super` 模式。SUPER 不是新的底层业务角色，也不进入 `roles.runtime` / `RoleConsoleHost` 的 canonical role 集合；它是 delivery 层的全能编排模式，用于根据用户输入意图动态选择一个或多个既有角色顺序执行。

第一阶段目标：

1. `--super` 打开后，console 以 `super` 身份对外展示。
2. 同一条用户输入可被路由到不同既有角色。
3. 对“代码修改/完善/修复”类请求，固定采用 `pm -> director` 两跳模式：
   - PM 先把原始需求转成可执行计划
   - Director 再基于 PM 产物执行
4. 其它意图走单跳：
   - 架构/蓝图/方案 -> `architect`
   - 根因分析/代码审查/技术评估 -> `chief_engineer`
   - 测试/验证/QA -> `qa`
   - 纯规划/任务拆解 -> `pm`
   - 未识别 -> 回退到用户指定 `--role`（默认 `director`）

## 2. Non-Goals

本阶段不做：

1. 不把 `super` 注册为底层 `roles.runtime` 真角色。
2. 不改 `RoleConsoleHost`、`RoleRuntimeService`、`roles.kernel` 的基础协议。
3. 不做多 agent 并发执行。
4. 不做 PM 合同对象级结构化 handoff；先基于 console 级显式文本 handoff 实现闭环。
5. 不改 `/role <name>` 现有语义；SUPER 主要由 `--super` 启用。

## 3. Architecture Decision

采用 **delivery-layer orchestration**：

`CLI args -> run_role_console(super_mode=True) -> SuperModeRouter -> one or more host.stream_turn(role=...) calls`

原因：

1. `RoleConsoleHost.stream_turn(..., role=...)` 已支持逐回合角色覆写，复用优先。
2. `director` 已有模糊需求防御。如果直接把模糊请求送给 Director，会被拒绝；先 PM 再 Director 正好解决这一点。
3. 该能力属于 CLI transport / session choreography，不应侵入 KernelOne/roles runtime。

## 4. Modules

### 4.1 `polaris/delivery/cli/super_mode.py`

新增轻量编排模块，包含：

1. `SuperRouteDecision`
   - `display_role`: 对外显示的逻辑角色（固定 `super`）
   - `roles`: 实际执行序列，如 `("pm", "director")`
   - `reason`: 路由原因，用于 debug / tests
2. `SuperTurnResult`
   - `role`
   - `session_id`
   - `final_content`
   - `saw_error`
3. `SuperModeRouter`
   - 基于关键字 / 意图信号做确定性路由
4. `build_director_handoff_message()`
   - 把 PM 输出转成对 Director 明确且可执行的 handoff 文本

### 4.2 `polaris/delivery/cli/terminal_console.py`

增强点：

1. 增加 `super_mode: bool = False`
2. 允许 prompt / banner 显示 `super`
3. 将原有 `_stream_turn` / `_run_streaming_turn` 返回结构化结果，保留现有打印行为不变
4. 新增 `_run_super_turn(...)`
   - 根据 `SuperModeRouter` 决定执行序列
   - 为每个下游角色分配/复用 role-bound session
   - 第一跳输出收集为 `SuperTurnResult`
   - 如为 `pm -> director`，将 PM 最终文本 handoff 给 Director

### 4.3 CLI parser / router

1. `polaris/delivery/cli/__main__.py`
   - `console` 子命令新增 `--super`
2. `polaris/delivery/cli/router.py`
   - `_route_console()` 透传 `super_mode`

### 4.4 Prompt rendering

1. `polaris/delivery/cli/cli_prompt.py`
   - 增加 `super` 的 prompt symbol
2. `terminal_console.py`
   - 增加 `ROLE_PROMPT_SYMBOLS["super"]`
   - onboarding / help 文案补充 `--super`

## 5. Data Flow

### 5.1 Single-hop flow

`user message`
-> `SuperModeRouter.decide()`
-> `roles = ("architect",)` 之类
-> `host.stream_turn(role="architect")`
-> stream to user

### 5.2 PM -> Director flow

`user message`
-> `SuperModeRouter.decide()` returns `("pm", "director")`
-> `host.stream_turn(role="pm", message=original_user_message)`
-> collect PM final content
-> `build_director_handoff_message(original_message, pm_output)`
-> `host.stream_turn(role="director", message=handoff_message)`
-> stream Director result to user

## 6. Director Handoff Contract (Console-local)

采用显式文本 handoff，而不是隐式 prompt 注入：

```text
[SUPER_MODE_HANDOFF]
original_user_request: ...
planning_role: pm
execution_role: director
pm_plan:
...
[/SUPER_MODE_HANDOFF]
```

要求：

1. handoff 文本必须包含原始请求
2. 必须包含 PM 输出全文或安全截断版本
3. 必须明确告诉 Director：基于 PM 计划执行，不要重新做高层规划

这样可以降低 Director 的模糊需求拦截概率，同时让日志可审计。

## 7. Intent Routing Rules (V1)

### 7.1 `pm -> director`

命中以下任一意图：

- `修复` `完善` `修改代码` `实现` `开发` `重构` `refactor` `fix` `implement` `improve code`
- 与代码/文件/模块组合出现的动词

### 7.2 `architect`

- `架构` `设计方案` `蓝图` `ADR` `architecture` `design`

### 7.3 `chief_engineer`

- `根因` `分析` `review` `审查` `评审` `排查` `troubleshoot`

### 7.4 `qa`

- `测试` `验证` `回归` `qa` `验收`

### 7.5 `pm`

- `计划` `拆分任务` `排期` `roadmap` `plan`

### 7.6 fallback

- 回退到 CLI `--role`

## 8. Risks

1. 文本 handoff 不是结构化任务合同，长期应收敛到 PM contract/result 对象级 handoff。
2. 关键字路由会有误判；因此 V1 保持规则简单可解释，并允许 fallback。
3. SUPER 可能让用户误以为是并发多角色系统；本阶段实际上是串行 orchestration。

## 9. Verification Plan

### Unit tests

1. parser 接受 `--super`
2. router 正确透传 `super_mode=True`
3. SUPER 模式代码类请求走 `pm -> director`
4. handoff message 包含原始请求和 PM 输出
5. 单跳意图（architect/chief_engineer/qa/pm）正确路由

### Quality gates

1. `ruff check --fix polaris/delivery/cli/__main__.py polaris/delivery/cli/router.py polaris/delivery/cli/terminal_console.py polaris/delivery/cli/cli_prompt.py polaris/delivery/cli/super_mode.py polaris/delivery/cli/tests/test_terminal_console.py polaris/delivery/cli/tests/test_cli_log_level_option.py`
2. `ruff format` 同路径
3. `mypy` 同路径
4. `pytest -q polaris/delivery/cli/tests/test_terminal_console.py polaris/delivery/cli/tests/test_cli_log_level_option.py`

## 10. Rollback Strategy

该功能是 CLI 参数和交付层逻辑增强。若出现回归，可通过移除 `--super` 路径回退，不影响既有 `--role director|pm|...` 单角色控制台。
