# TRANSACTION_KERNEL_RUNTIME_DISCIPLINE_OPTIMIZATION_BLUEPRINT_20260424

## 1. 背景
- 日期: 2026-04-24
- 范围: `roles.kernel` 事务内核运行时纪律优化（增量）
- 目标:
  1. 在 `turn_request_id` 之上补齐 `span_id` / `parent_span_id` 关联谱系。
  2. 将工具失败熔断从“纯总量”升级为“按 tool/effect/failure 维度统计并触发”。
  3. 保持现有对外行为兼容，采用最小可回滚改动。

## 2. 文本架构图
```text
[TurnTransactionController.execute_stream]
        |
        +--> generate correlation context:
             turn_request_id + turn_span_id + parent_span_id(optional)
        |
        +--> for each TurnEvent:
             attach {turn_request_id, span_id, parent_span_id}
        |
        +--> TurnTruthLogRecorder (append-only JSONL)
             payload includes correlation lineage fields
        |
        +--> ToolBatchExecutor
              |
              +--> ToolFailureCircuitBreaker.evaluate_batch(
                     receipts + invocation metadata(call_id/tool/effect)
                   )
                   |
                   +--> per-dimension counters:
                        key=(tool_name, effect_scope, failure_class)
                   +--> trigger fail-closed RuntimeError when threshold crossed
```

## 3. 模块职责
- `polaris/cells/roles/kernel/public/turn_events.py`
  - 扩展 turn 事件结构：增加 `span_id` / `parent_span_id`。
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
  - 统一注入 correlation lineage 到事件流与 TruthLog payload。
- `polaris/cells/roles/kernel/internal/transaction/tool_failure_circuit_breaker.py`
  - 增加分维度失败统计与阈值判定。
- `polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py`
  - 传递 invocation 元信息给 breaker，触发 fail-closed。

## 4. 核心数据流
1. `execute_stream()` 生成 `turn_request_id` 与 `turn_span_id`（可接收上游 `parent_span_id`）。
2. 每个 `TurnEvent` 赋予:
   - `turn_request_id`: turn 请求关联主键
   - `span_id`: 当前事件 span
   - `parent_span_id`: 父级 span（默认 turn span 或上游传入）
3. 事件写入 TruthLog 时完整落盘上述字段，支持离线追踪。
4. 工具批执行后，breaker 根据 `receipts + invocations` 统计失败维度 key：
   - `tool_name`
   - `effect_scope` (`read` / `write` / `async` / `unknown`)
   - `failure_class` (`error` / `timeout` / `aborted`)
5. 达到阈值时立即 fail-closed，终止当前 turn。

## 5. 技术选型理由
- 继续沿用 dataclass + append-only JSONL，避免引入第二套事件协议。
- breaker 维度统计采用内存字典，复杂度线性，易观测、易回滚。
- span 字段在既有事件模型增量扩展，确保下游兼容。

## 6. 委派执行（4+ 专家）
- Expert A（Kernel Contracts）: 事件契约字段扩展与兼容性检查。
- Expert B（Kernel Runtime）: controller 关联注入与 ContextVar 传播。
- Expert C（Tool Governance）: breaker 分维度阈值与执行链路接入。
- Expert D（QA/Verification）: pytest 回归与门禁执行（ruff/mypy/pytest）。
- Expert E（Observability）: TruthLog 字段完整性核验与可追踪性验证。
