# Truth Reconciliation — TransactionKernel / ContextOS / Tool Refactor Closure Matrix

状态: Active Audit Document / Current-Facts Companion  
日期: 2026-04-16  
来源: `docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md` 逐项对账

> 2026-04-17 说明：
> - 本文是当前事实对账文档，不负责下一阶段实施步骤。
> - `ContextOS` 四层正式化执行与验收已在 `../../docs/blueprints/CONTEXTOS_SERVICE_HARDENING_BLUEPRINT_20260417.md` 完成并收口。
> - 对应修前治理卡见 `src/backend/docs/governance/templates/verification-cards/vc-20260417-contextos-service-hardening.yaml`。

---

## 图例

- **Auth** — Implemented and authoritative (主路径)
- **Part** — Implemented but partial / dual-track
- **Exp** — Experimental (代码存在但未接入主路径)
- **Miss** — 确实缺失
- **Dead** — 历史残留，应删除

---

## 执行内核 (Slice B)

| 项 | 位置 | 状态 | 备注 |
|---|---|---|---|
| TransactionKernel 切主 | `roles/kernel/internal/transaction_kernel.py` | **Auth** | `_use_transaction_kernel()` 默认 True |
| TurnEngine facade | `roles/kernel/internal/turn_engine/engine.py` | **Auth** | while loop 已移除 |
| KernelGuard | `roles/kernel/internal/kernel_guard.py` | **Auth** | 测试覆盖 |
| LEGACY_FALLBACK | `kernel/core.py:265` | **Auth** | 一次性逃生阀 |
| turn_engine_migration.py | — | **Dead** | 已删除 |

## 协议层 (Slice C)

| 项 | 位置 | 状态 | 备注 |
|---|---|---|---|
| turn_contracts frozen IR | `roles/kernel/public/turn_contracts.py` | **Auth** | 9 类全 frozen，effect_type 完整 |
| Decoder protocol panic | `roles/kernel/internal/turn_decision_decoder.py:104` | **Auth** | optional_finalize 再出 tool 即 panic |
| Decision/Finalization Caller | `roles/kernel/internal/llm_caller/invoker.py` | **Auth** | `services/llm_invoker.py` 冗余副本已删除 |
| thinking 隔离 | `turn_materializer.py` / `turn_transaction_controller.py` | **Auth** | 仅进 telemetry/ledger |

## ContextOS 四层 + Workflow (Slice D/E)

| 项 | 位置 | 状态 | 备注 |
|---|---|---|---|
| ProjectionEngine | `kernelone/context/projection_engine.py` | **Auth** | 已接管 projection payload 构建、control-plane 剥离、receipt ref 注入与 message 投影 |
| ReceiptStore | `kernelone/context/receipt_store.py` | **Auth** | 已支持 `offload_content` / `export_receipts` / `import_receipts`，并被 runtime/snapshot/gateway 统一使用 |
| TruthLogService | `kernelone/context/truth_log_service.py` | **Auth** | 已提供 `append_many` / `replace` / replay/export；runtime 在 pipeline 回写时统一经此服务 |
| WorkingStateManager | `kernelone/context/working_state_manager.py` | **Auth** | 已提供 canonical `WorkingState` 持有与 replace/export/current；runtime 回写统一经 manager |
| StateFirstContextOS 双轨 | `kernelone/context/context_os/runtime.py` | **Auth** | `_legacy_project_impl` 已删除，`enable_pipeline` 已移除，仅保留 `_project_via_pipeline` |
| Snapshot nuke 修复 | `kernelone/context/context_os/models.py` | **Auth** | `to_dict()` 超限自动创建 ReceiptStore 并替换为 `<receipt_ref:...>`；`_receipt_store_export` 保证 round-trip |
| ExplorationWorkflowRuntime | `roles/kernel/internal/exploration_workflow.py` | **Auth** | `ExplorationWorkflowRuntime` 类存在（L82），`ExplorationWorkflow = ExplorationWorkflowRuntime` 别名（L426），handoff 字段扩展已完成 |
| HandoffPack profile 扩展 | `domain/cognitive_runtime/models.py` | **Auth** | `checkpoint_state` / `pending_receipt_refs` / `suggestion_rankings` / `lease_token` 均已存在（L222-225） |

## Tool Runtime (Phase 4)

| 项 | 位置 | 状态 | 备注 |
|---|---|---|---|
| 文件写事务化 | `kernelone/llm/toolkit/executor/handlers/filesystem.py:59` | **Auth** | `_write_temp_verify_rename` 已上线 |
| 自动回滚 | `filesystem.py` | **Auth** | temp 验证失败自动清理 |
| Receipt 强制校验 | `roles/kernel/internal/tool_batch_runtime.py` | **Auth** | write 空 effect_receipt 抛 RuntimeError |
| FailureBudget session-scoped | `kernelone/tool_execution/failure_budget.py` | **Auth** | 已确认 |

## Speculative Execution (Phase 6)

| 项 | 位置 | 状态 | 备注 |
|---|---|---|---|
| StreamShadowEngine | `roles/kernel/internal/stream_shadow_engine.py` | **Auth** | 已接入 `TurnTransactionController._call_llm_for_decision_stream`（flag 控制） |
| SpeculativeExecutor | `roles/kernel/internal/speculative_executor.py` | **Auth** | 已由 stream shadow path 调用，只对只读工具触发 pre-exec |
| feature flag | `roles/kernel/internal/speculative_flags.py` | **Auth** | `ENABLE_SPECULATIVE_EXECUTION` 已落地（默认关闭，兼容 `KERNELONE_ENABLE_SPECULATIVE_EXECUTION`） |

## Tool Spec SSOT (Phase 7)

| 项 | 位置 | 状态 | 备注 |
|---|---|---|---|
| ToolSpecRegistry 接管 | `kernelone/tool_execution/tool_spec_registry.py` | **Auth** | 活跃使用；`contracts.py` 内所有函数已代理至 `ToolSpecRegistry`，`_ToolSpecsProxy` 提供 `_TOOL_SPECS` 向后兼容；`@warnings.deprecated` 已手术式落地（`normalize_tool_args`、`_has_value`、`reset_tool_spec_registry_cache`）；Phase 7 监控基线已导出到 `TurnResult.metrics` 与 stream `complete.monitoring` |

---

## 灰度观测执行面（2026-04-17）

| 项 | 结果 | 证据 |
|---|---|---|
| `tool_calling_matrix` 全量 stream 基准 | **76 / 78 PASS**（`97.44%`） | `run_id=3ce32d24`（同量级基线 `febb3027`） |
| 通过率门槛 | **已满足**（目标 `>=70/74`） | `C:/Temp/runtime/llm_evaluations/3ce32d24/AGENTIC_EVAL_AUDIT.json` |
| 本轮根因修复验证 | **2 / 2 PASS**（`bridge_emits_session_patch` + `l5_sequential_dag`） | `run_id=61e23065` |
| 残留形态 | bridge 子集仍存在跨 run 随机性（非稳定 deterministic fail） | `X:/.polaris/projects/temp-1df48b9917eb/runtime/llm_evaluations/3ce32d24/TOOL_CALLING_MATRIX_REPORT.json` 与 `.../e9047ff4/...` |

---

## Dead / To Delete

| 组件 | 动作 |
|---|---|
| `roles/kernel/internal/turn_engine_migration.py` | 已删除，无需动作 |
| `roles/kernel/internal/services/llm_invoker.py` | **已删除**（与 `llm_caller/invoker.py` 冗余） |

---

## 关键收敛裁决

1. **不要再补新层**。四层 ContextOS 文件已有代码，下一步是让它们**替代**旧内联实现，而不是继续新建服务。
2. **双轨已退役**（2026-04-17）。`runtime.py` 中 `_legacy_project_impl` 已删除，`enable_pipeline` 已移除，`project()` 直接走 `_project_via_pipeline`。
3. **speculative 层已完成受控接线**。当前以 feature flag 默认关闭方式接入 stream 路径，保持可回滚与低风险。
4. **Phase 7 监控基线已落地**。`transaction_kernel.violation_count`、`turn.single_batch_ratio`、`workflow.handoff_rate`、`kernel_guard.assert_fail_rate`、`speculative.hit_rate`、`speculative.false_positive_rate` 已进入结果与流式完成事件。
5. **nuke 序列化已修复**（2026-04-17）。`ContextOSSnapshot.to_dict()` 超限自动创建 `ReceiptStore` 并内联 `_receipt_store_export`，round-trip 安全。
6. **四层服务硬化已完成**。`ReceiptStore` / `TruthLogService` / `WorkingStateManager` / `ProjectionEngine` 已从占位符升级为 authoritative path，并完成 `kernel + context` 联合回归。
