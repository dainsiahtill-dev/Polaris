---
status: accepted
date: 2026-04-16
---

# ADR-0071: TransactionKernel 单提交点与 Context Plane / Handoff Contract 收口

## 背景

roles.kernel 当前仍处于“旧 TurnEngine 主路径 + 新事务控制器旁路 + ContextOS 修补链 + cognitive_runtime handoff contract 并存”的混合状态，产生了三个结构性问题：

1. **执行授权点不唯一**  
   `RoleExecutionKernel.run()` / `run_stream()` 与 `turn_runner.py` 仍直接实例化旧 `TurnEngine`，而 `TurnTransactionController` 只是存在但未切主的替代实现。

2. **上下文真相与控制面混流**  
   ContextOS、tool loop、telemetry、system warning 与 prompt assembly 仍然耦合，导致 hidden continuation、raw tool output 回灌 prompt、投影污染难以彻底消除。

3. **handoff contract 已存在却未被收口使用**  
   `ContextHandoffPack` 已经在 `polaris/domain/cognitive_runtime/models.py`、`factory.cognitive_runtime` 公开契约与 graph 中落地；如果本次重构再在 `roles.kernel` 新建私有 `handoff_pack.py`，会立即制造第二套真相。

## 决策

### 1. 唯一事务提交点

将 `TurnTransactionController` 收敛并切主为 `TransactionKernel`，形成唯一的 turn 事务执行内核。

约束如下：

1. 一个 turn 内只允许一个 `TurnDecision`
2. 一个 turn 内最多一个 `ToolBatch`
3. 工具执行后禁止返回 `Deliberate/Decode`
4. 再次请求工具时不再 continuation，而是统一 `handoff_workflow`

旧 `TurnEngine` 保留外部签名，但退化为 facade/shim，不再持有 while-loop 和内部 continuation 逻辑。

### 2. Context Plane / Data Plane 隔离

上下文系统采用四层正交结构：

1. `TruthLog`
2. `WorkingState`
3. `ReceiptStore`
4. `ProjectionEngine`

并明确以下边界：

1. `TruthLog` append-only
2. `PromptProjection` read-only 生成
3. control-plane 字段（decision、telemetry、policy verdict、budget status）禁止写入 data plane

这意味着：

1. thinking 只进入 telemetry / ledger
2. raw tool output 不再直接进入 prompt
3. Projection 不得反向修改 truth

### 3. handoff canonical contract 收口

本次重构**不新建第二套 `HandoffPack` model**。

本文档、蓝图和代码中的 `HandoffPack` 仅作为逻辑名称，实际 canonical contract 统一为：

1. `polaris.domain.cognitive_runtime.ContextHandoffPack`
2. `polaris.cells.factory.cognitive_runtime.public.contracts`

如果 TransactionKernel / ExplorationWorkflowRuntime 需要新增字段：

1. 优先扩展 `ContextHandoffPack`
2. 同步更新 factory.cognitive_runtime 公开 contract
3. 必要时同步 graph / descriptor / router / store 测试

禁止在 `roles.kernel` 再创建平行 handoff schema。

### 4. 文档统一策略

以下文档形成一套统一真相链：

1. `docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
2. `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
3. `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`

旧 `docs/TURN_ENGINE_TRANSACTIONAL_TOOL_FLOW_BLUEPRINT_2026-03-26.md` 保留为前序蓝图，但不再作为当前目标态裁决来源。

## 后果

### 正向

1. roles.kernel 获得唯一事务提交点，turn 内 hidden continuation 可以被代码和测试明确禁止。
2. ContextOS 的 truth / projection / receipt 边界变得可审计，prompt 污染治理不再依赖经验性修补。
3. handoff / rehydrate / export contract 继续复用 graph 已声明的 `factory.cognitive_runtime` 能力，避免第二套 schema。
4. 文档、治理卡片与实现切口保持一致，后续迁移不需要再次“先审计哪份文档是真的”。

### 代价

1. 需要修改 roles.kernel 主执行入口，影响面跨 `kernel/core.py`、`turn_runner.py`、`turn_engine_migration.py`。
2. 需要把 `turn_contracts.py` 从兼容型 `TypedDict`/`dataclass` 升级为冻结 IR，可能波及现有测试和调用点。
3. handoff 字段扩展必须经过 `factory.cognitive_runtime` 与 graph 资产同步，实施成本高于在 roles.kernel 内私自定义模型。

## 当前实现进度（截至 2026-04-17）

- ✅ `TransactionKernel` 已切为主路径，`RoleExecutionKernel.run()` / `run_stream()` 不再直接实例化旧 `TurnEngine`
- ✅ `TurnEngine` 已退化为 facade，`while` loop 与 continuation logic 已移除
- ✅ `KernelGuard` 已上线并运行断言
- ✅ `turn_contracts.py` 已升级为 Pydantic v2 frozen models，包含 `effect_type` / `execution_mode`
- ✅ `ContextHandoffPack` 已成为 `roles.kernel` handoff 的唯一 canonical contract；`checkpoint_state` / `pending_receipt_refs` / `suggestion_rankings` / `lease_token` 均已落地
- ✅ `StateFirstContextOS` 双轨已退役：`_legacy_project_impl` 删除，`enable_pipeline` 移除，`project()` 仅走 `_project_via_pipeline`
- ✅ `ContextOSSnapshot.to_dict()` nuke 修复：超限自动创建 `ReceiptStore` 并内联 `_receipt_store_export`，round-trip 安全
- ✅ `ToolSpecRegistry` SSOT 已收口；`contracts.py` 手术式添加 `@warnings.deprecated`（`normalize_tool_args`、`_has_value`、`reset_tool_spec_registry_cache`）
- ✅ `ProjectionEngine` / `ReceiptStore` / `TruthLogService` / `WorkingStateManager` 已完成正式化硬化并接入 runtime + gateway 主路径
- ✅ `ENABLE_SPECULATIVE_EXECUTION` feature flag 已落地（默认关闭，兼容 `KERNELONE_ENABLE_SPECULATIVE_EXECUTION`）
- ✅ `SpeculativeExecutor` / `StreamShadowEngine` 已接入 stream 路径（flag 默认关闭，低风险可回滚）
- ✅ Phase 7 监控基线已落地：`TurnResult.metrics` 与 stream `CompletionEvent.monitoring` 已导出
  `transaction_kernel.violation_count` / `turn.single_batch_ratio` / `workflow.handoff_rate` /
  `kernel_guard.assert_fail_rate` / `speculative.hit_rate` / `speculative.false_positive_rate`

## 验证

以下测试已在 CI 中全绿通过：

1. `python -m pytest polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py -q` ✅
2. `python -m pytest polaris/cells/roles/kernel/tests/test_kernel_guard.py -q` ✅
3. `python -m pytest polaris/cells/roles/kernel/tests/test_turn_contracts_v2.py -q` ✅
4. `python -m pytest polaris/cells/roles/kernel/tests/test_transaction_kernel_handoff_contract.py -q` ✅
5. `python -m pytest polaris/cells/factory/cognitive_runtime/tests/test_public_contracts.py -q` ✅
6. `python -m pytest polaris/delivery/http/routers/test_cognitive_runtime_router.py -q` ✅
7. `python -m pytest polaris/kernelone/context/tests/test_context_os_pipeline.py polaris/kernelone/context/tests/test_context_os_models.py -q` ✅
8. `python -m pytest polaris/kernelone/tool_execution/tests/test_contracts_validation_integration.py -q` ✅
9. `python -m pytest polaris/cells/roles/kernel/tests/ polaris/kernelone/context/tests/ -q` ✅ (1753 passed, 5 skipped)

## 关联资产

- `docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
- `docs/blueprints/CONTEXTOS_SERVICE_HARDENING_BLUEPRINT_20260417.md`
- `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
- `docs/governance/templates/verification-cards/vc-20260417-contextos-service-hardening.yaml`
- `docs/TURN_ENGINE_TRANSACTIONAL_TOOL_FLOW_BLUEPRINT_2026-03-26.md`
- `docs/KERNELONE_CONTEXT_OS_COGNITIVE_RUNTIME_HARDENING_PLAN_2026-03-27.md`
