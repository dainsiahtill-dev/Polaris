# ToolCallEnvelope Wave 1 Blueprint (2026-07-03)

## 1. 问题陈述（量化证据）

2026-07-03 对抗审计确认：一个 provider-native 工具调用从 wire 到 Run Ledger 经过 **13 种内存表示**（8 typed + 5 raw dict），无任何对象全程流动；`tool_calls` vs `native_tool_calls` 别名在 8 文件 10 处各自兼容；工具名 7 种 key 形态、参数 12 键别名表；同一"调用数"由 **7 个独立字段**分别重算（bench r62/r63 的计数分裂即此类）；lifecycle receipt 在 ≥4 处独立合成（含 2 处手写 dict 孪生）；stream 路径整套平行实现。bench 战役用 14 轮（r51-r64）才把一条调用链打通，因为每个边界都要单独发现、单独补。

## 2. 目标态与 Wave 1 范围

目标态：**信封一次铸造、按引用流动、一切计数与 receipt 是信封迁移的只读投影**。

```
extract_native_tool_calls (唯一铸造点)
  └─ ToolCallEnvelopeV1{envelope_id, provider, tool_name_raw, tool_name_canonical,
                        arguments, provider_payload_hash,
                        transitions: [observed] (append-only)}
        │ 以 metadata 引用穿过既有 typed hop（全部加性可选字段）
        ▼
LLMResponse → provider dict → RawLLMResponse → ToolInvocation/ToolBatch
        │  每个过滤/丢弃点 append transition:
        │   filtered(reason=delivery_mode|out_of_scope|hallucination, authority=<module>)
        ▼
ToolExecutionResult/BatchReceipt → RoleTurnResult → completion/projection
        │   dispatched / effect_receipt(ref) / committed
```

**Wave 1（本蓝图）＝纯加性穿线，零行为变更**：
1. 新契约 `ToolCallEnvelopeV1`（frozen dataclass + to_payload/from_payload）落位 `control_plane.run_ledger.public.tool_lifecycle` 旁（与 `ToolCallLifecycleReceiptV1` 同居所，它已是既有 canonical artifact）。
2. `extract_native_tool_calls` 为每个解析出的 native call 铸造信封（envelope_id = provider call id，缺失时铸稳定 uuid），随调用 dict 以 `_envelope` 键流动；typed hop 增加可选 `tool_call_envelopes` 字段。
3. 既有过滤点（`apply_delivery_mode_filter`、`filter_out_of_scope_write_invocations`、finalization 幻觉丢弃、implementing-phase block）在其现有 anomaly-flag 逻辑旁 **追加** filtered transition——不改变过滤行为本身。
4. dispatch/effect/commit 点追加对应 transition。
5. 观测校验：completion 处将 `len(transitions by kind)` 与既有 7 个计数器对账，分歧写入 metadata `envelope_count_divergence`（只记录，不改判定）。

**Wave 2（后续）**：计数器全部改为信封派生，divergence 清零后删除独立计数字段。
**Wave 3（后续）**：删 10 个别名读写点；stream 路径改调共享管道；`ToolCallLifecycleReceiptV1` 改为信封投影，删除 2 处手写 dict 孪生。

## 3. 强约束

- **§6.6 canonical-gate 铁律**：信封同时保存 `tool_name_raw`（解析前原始 token）与 `tool_name_canonical`，绝不改写 raw（沿用 `CanonicalToolCall.tool_raw` 先例）；漂移检测依赖 raw/observed 同源。
- ADR-0071：TransactionKernel 单 commit point 不变；信封是证据载体，不承载执行决策。
- 加性字段全部 Optional/default，任何旧调用方无感知；序列化经 to_payload 显式进行，不得让 dataclass 泄漏进 JSON dump 断言。

## 4. 验证

- 全零行为变更验证：既有 kernel/transaction/adapter 套件全绿（wiring/controller/facade/completion/decoder/llm_caller）。
- 新增：铸造唯一性（同 call id 不复铸）、transition append-only、每个过滤点产生 filtered transition 的单测、divergence 对账单测（人为制造别名丢失场景 → divergence 非零且被记录）。

## 4.1 2026-07-04 增量落地记录

- `control_plane.run_ledger.public.tool_lifecycle` 已公开
  `normalize_native_tool_call_envelope_refs()`，Run Ledger 成为 native
  tool-call envelope refs 过滤与去重规则的 owner。
- `roles.runtime.public.result_mapping` 已改为消费该 public helper，不再维护
  本地 envelope payload 过滤规则。
- `roles.kernel.internal.llm_caller.tool_helpers` 已改为消费同一 public helper，
  保留 `native_tool_call_envelopes_from_metadata()` 调用面但移除本地 envelope
  过滤/去重规则。
- `roles.kernel.internal.turn_decision_decoder` 已改为消费同一 public helper，
  决策 metadata 不再维护 list-only 的 envelope 过滤规则。
- 验证：
  `rtk pytest src/backend/polaris/cells/control_plane/run_ledger/tests/test_tool_lifecycle.py src/backend/polaris/cells/roles/runtime/tests/test_service_helpers_characterization.py -q -k "tool_lifecycle or native_tool_call_envelope or extract_tool_calls"`；
  `rtk ruff check src/backend/polaris/cells/control_plane/run_ledger/public/tool_lifecycle.py src/backend/polaris/cells/control_plane/run_ledger/public/__init__.py src/backend/polaris/cells/control_plane/run_ledger/tests/test_tool_lifecycle.py src/backend/polaris/cells/roles/runtime/public/result_mapping.py`；
  `rtk mypy src/backend/polaris/cells/control_plane/run_ledger/public/tool_lifecycle.py src/backend/polaris/cells/control_plane/run_ledger/public/__init__.py src/backend/polaris/cells/roles/runtime/public/result_mapping.py`。
  追加验证：
  `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_llm_caller_helpers.py -q -k "native_tool_call"`；
  `rtk ruff check src/backend/polaris/cells/roles/kernel/internal/llm_caller/tool_helpers.py`；
  `rtk mypy src/backend/polaris/cells/roles/kernel/internal/llm_caller/tool_helpers.py`。
  decoder 追加验证：
  `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_decision_decoder.py -q -k "native_tool_call_envelopes"`；
  `rtk ruff check src/backend/polaris/cells/roles/kernel/internal/turn_decision_decoder.py src/backend/polaris/cells/roles/kernel/tests/test_decision_decoder.py`；
  `rtk mypy src/backend/polaris/cells/roles/kernel/internal/turn_decision_decoder.py`。

## 5. 风险与边界

- 信封 dict 引用穿过 provider-callback 层时可能被浅拷贝分离——穿线以 `_envelope` 键随调用 dict 本体走，凡深拷贝调用 dict 的站点自动携带。
- Wave 1 不动 stream 路径的平行实现（Wave 3 处理），只保证非流式全链穿通 + stream 铸造点同样铸造（即便下游暂不消费）。
