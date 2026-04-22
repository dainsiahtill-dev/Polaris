# CLI SUPER Mode Readonly Stage Handoff Alignment Blueprint

日期: 2026-04-22
状态: Draft
范围:
- `polaris/delivery/cli/super_mode.py`
- `polaris/delivery/cli/terminal_console.py`
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
- `polaris/cells/roles/kernel/internal/transaction/task_contract_builder.py`

## 1. 问题陈述

当前 `--super` 模式存在三类结构性缺陷：

1. 高层工程意图如果没有显式文件路径，容易被降级到 `director` 直执行，随后被 Director 清晰度门禁拦截。
2. `pm/architect` 只读规划阶段虽然被包上了 `[mode:analyze]`，但内核仍会注入 mutation task contract 和“后续必须写工具”的执行约束。
3. 只读规划阶段即使已经输出了任务列表，也不会在首个 `complete` 后立即 handoff，而是继续在同一请求里续跑更多 turn。

结果是：

- 用户看到“没有继续完成任务”；
- PM/Architect 规划阶段被错误地拖入多轮探索；
- Director 迟迟拿不到稳定、单次的 handoff 输入。

## 2. 根因

### 2.1 路由根因

`SuperModeRouter` 当前对 `code_delivery` 的判断过度依赖显式代码目标提示。像 `进一步完善ContextOS` 这类“高层技术目标 + 修改意图”没有命中稳定的工程规划分流，最终走到 fallback director。

### 2.2 Prompt 根因

`build_super_readonly_message()` 只是在用户消息里写入 readonly marker；但 decision prompt 构造层没有消费这个 marker：

- `_build_decision_messages()` 仍注入 mutation-oriented execution constraint
- `build_single_batch_task_contract_hint()` 仍基于原始用户请求推导 “This request requires mutation”

因此一个只读角色同时收到两套互相冲突的合同：

- 合同 A: 当前阶段只读分析
- 合同 B: 当前请求要求写代码，不准停在只读工具

### 2.3 终止根因

`_run_streaming_turn()` 默认会一直消费 `host.stream_turn()`，直到整个 session 流结束；不会在首个 `complete` 事件后停止。  
对 `super` 的规划角色来说，这个行为不对，因为该阶段的目标是“一次性产出计划后立即 handoff”。

## 3. 设计原则

1. 只读阶段必须拿到只读合同，不能再继承 mutation 强制写约束。
2. `super` 的规划阶段是“单次产出 -> 交棒”，不是普通多轮会话。
3. 高层工程改造意图优先进入 `architect -> director` 流水线，而不是直接打到 Director。

## 4. 修复方案

### 4.1 路由修复

在 `SuperModeRouter` 中新增“高层工程改造”分流：

- 若请求包含修改/完善/实现等工程动作
- 但目标更像能力/子系统/架构主题，而不是明确文件落点

则路由为 `architect -> director`。

### 4.2 Readonly 合同修复

当上下文包含 `[SUPER_MODE_READONLY_STAGE]` 时：

- `_build_decision_messages()` 注入 readonly planning system constraint
- `build_single_batch_task_contract_hint()` 直接跳过 mutation contract 生成

即：只读规划阶段只允许读取、分析、输出任务列表，不再被要求本回合写代码。

### 4.3 Handoff 终止修复

为 `_run_streaming_turn()` 增加 `stop_on_first_complete` 开关。

在 `super` 的 `pm/architect/chief_engineer/qa` 阶段启用该开关：

- 收到首个 `complete` 事件后立即结束本阶段
- 直接把结果作为 handoff 输入交给下一角色

## 5. 验证计划

1. `super_mode` 路由单测：
   - `进一步完善ContextOS` -> `architect,director`
   - `进一步完善 session_orchestrator.py` -> `pm,director`
2. readonly decision message 单测：
   - `[SUPER_MODE_READONLY_STAGE]` 不应生成 mutation task contract
   - 不应注入 “subsequent turns MUST call write/edit tools”
3. terminal console 单测：
   - PM 规划阶段首个 `complete` 后立刻交棒给 Director
4. 回归门禁：
   - `ruff check --fix`
   - `ruff format`
   - `mypy`
   - 相关 `pytest`

## 6. 风险

1. 路由过宽可能把本应直达 Director 的明确文件修改请求送去 Architect。
2. 如果 `stop_on_first_complete` 误用于普通 role console，会截断正常多轮会话。
3. readonly marker 的识别必须限定在 super stage，不能影响普通 analyze_only 请求。
