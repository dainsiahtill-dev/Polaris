# ADR-0091: Scout Recon-Gate（recon_mode 角色的读侧落地不变量）

## Status

- **Status**: Accepted
- **Date**: 2026-06-10
- **Author**: AI Agent (via scout_matrix anti-jitter audit)
- **Related**: ADR-0071 (single-commit kernel), ADR-0081 (transaction kernel freeze),
  `docs/blueprints/SCOUT_RECON_GATE_BLUEPRINT_20260610.md` (repo root),
  `vc-20260610-scout-recon-gate.yaml`

## Context

事务内核已经保证**写侧**落地：`must_materialize` 契约下，没有 authoritative
write receipt 的 `FINAL_ANSWER` 会在 `_handle_final_answer`
（`turn_transaction_controller.py`）被阻断（Invariant A）。但**读侧没有对称保证**：
一个只读侦察角色（`scout`，`context_policy.recon_mode: true`）可以在
**零**读/检索工具执行的情况下输出最终答案——即「无依据侦察答案」。

scout_matrix 抗抖动审计中多次观测到 `observed_tools=none` 的自信作答；
benchmark 侧由 critical validator `scout_min_recon` 拦截，但该保证只活在
评测夹具里，生产 scout 调用（`scout_probe` → roles.scout cell）没有引擎级下限。

## Decision（不变量）

### R1 读侧落地不变量（recon-required finalize gate）

`recon_required` 内核中，`FINAL_ANSWER` 在 ledger 中**不存在至少一次成功的
侦察工具执行**时，必须被阻断为 `BlockedReason.NO_RECON_PERFORMED`
（终态 `recon_bypass_blocked`），与 `must_materialize` 阻断路径逐字对称：
转移 `COMPLETED`、`ledger.finalize()`、failed `CompletionEvent`、
`mutation_obligation.mark_blocked`。

### R2 拒绝豁免

与写侧门禁完全一致：可见输出命中 `REFUSAL_MARKERS` 的合法拒绝**不**强制侦察。

### R3 信号来源与零爆炸半径

`recon_required` 是**角色级**属性（非每轮意图），在
`TurnEngine._create_transaction_kernel` 构建 `TransactionConfig` 时一次性派生：
`profile.context_policy.recon_mode` OR（scout 角色 + `KERNELONE_SCOUT_RECON_MODE`
env 灰度开关，语义与 `RoleContextGateway._recon_mode_active` 镜像）。
默认 `False`——非侦察角色路径逐字节不变。

### R4 侦察工具集单一事实来源

判定集合 `SCOUT_RECON_TOOLS` 唯一定义于
`polaris/kernelone/tool_execution/tool_categories.py`（既有工具分类 SSOT）。
`unified_judge`（评测）与内核谓词 `has_successful_recon_execution`
（`transaction/contract_guards.py`）必须从同一常量导入；禁止再出现字面副本。

### R5 ADR-0071 兼容（block-only v1）

门禁不引入第二个 `TurnDecision`、不注入额外 `ToolBatch`、无隐藏续写——
纯阻断终态。蓝图 §3 中的 bootstrap 注入（drive 路径）**推迟**：
(a) `_handle_final_answer` 无 context/tool_definitions，改签名会波及
StreamOrchestrator；(b) 内核代答凭空构造检索 query 本身就是伪落地风险。
若未来需要自纠，应复用 ADR-0090 I3 的 corrective-retry 通道在决策层实现。

## Consequences

- scout 生产下限：永不输出零侦察的非拒绝最终答案（独立于任何 benchmark）。
- pm/architect/chief_engineer/director/qa：`recon_required=False`，行为不变。
- 评测与内核对「什么算侦察」永久一致（R4）。
- 被阻断的 turn 返回 `blocked_reason=no_recon_performed`，调用方
  （scout cell / 调度角色）可见可重试。
