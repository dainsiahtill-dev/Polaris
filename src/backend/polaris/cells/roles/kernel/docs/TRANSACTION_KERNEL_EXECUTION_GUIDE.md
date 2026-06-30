# TransactionKernel 执行指南

本指南是 roles kernel 当前执行边界的维护说明。旧 `TurnEngine` 执行门面已经退休；
新代码不得重新导入、包装或实例化旧执行类。

## 当前权威边界

- `RoleExecutionKernel`：角色执行的公开 facade，负责 role profile、prompt、上下文、工具网关和结果适配。
- `TransactionKernel`：当前 canonical turn execution facade，负责把角色请求收敛到事务化执行路径。
- `TurnTransactionController`：内部事务控制器，负责单次 turn 的状态机、账本、工具批次和 finalization。
- `transaction_factory.create_transaction_kernel`：从 `RoleExecutionKernel` 装配 `TransactionKernel` 的唯一工厂。
- `transaction.recon_policy.resolve_recon_required`：Scout recon gate 的共享策略来源。

## 禁止事项

- 禁止新增 `turn_engine/engine.py`。
- 禁止从 `polaris.cells.roles.kernel.internal.turn_engine` 导入 `TurnEngine`。
- 禁止恢复 `TurnEngineCompatMixin` 或旧 `_call_model/_decode/_execute_tools/_maybe_compact` helper API。
- 禁止让测试依赖旧执行 facade 的 stream/non-stream parity 行为。
- 禁止把 `TurnEngineContextRequest` 这类历史命名契约误当成旧执行器仍存在的证据。

## 新代码接入方式

1. 面向产品或 role adapter 的代码调用 `RoleExecutionKernel`。
2. 需要事务内核能力时，通过 `create_transaction_kernel(...)` 装配，不直接 new controller。
3. 新的执行策略、预算、recon、finalization、tool batch 行为应进入 `internal/transaction/` 子模块。
4. Tool/policy/context 的公共契约应通过现有 public service 或 ContextOS contract 引用，不新增旁路 facade。

## 回归门禁

以下门禁用于防止旧执行面回流：

```bash
rtk pytest src/backend/polaris/tests/architecture/test_turn_engine_compat_fence.py -q
rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_recon_gate.py -q
rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py -q
```

如需修改 turn 执行主路径，必须同时更新：

- architecture fence
- transaction/kernel 目标测试
- `POLARIS_LEGACY_SHIM_CONVERGENCE_LEDGER_20260630.md`
