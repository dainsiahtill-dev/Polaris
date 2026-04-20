# Roles Kernel LLM Caller 完善实施计划（2026-03-26）

- Status: Approved for Execution
- Scope: `polaris/cells/roles/kernel/internal/llm_caller.py` + 同 Cell 测试
- Non-goal: 不改动跨 Cell 对外合同；不把 `roles.kernel` 业务语义迁移到 `kernelone/`。

---

## 1. 背景与根因

当前 `LLMCaller` 同时承担了：

1. 请求装配（context + contract + provider capability）
2. LLM 调用执行（sync/structured/stream）
3. 生命周期审计（CALL_START/CALL_END/CALL_ERROR）
4. 缓存策略
5. structured fallback 协议处理

结果是：

1. 多入口路径（`call` / `call_structured` / `call_stream`）审计收口不一致
2. structured fallback 存在手工二次构造 `AIRequest`，容易绕开统一 contract
3. 缓存语义没有显式限制“仅纯文本、无工具、无结构化约束”场景

---

## 2. 目标

本轮只做“高收益、低风险、可回归”的第一阶段收口：

1. 统一生命周期事件发射，保证失败路径不静默
2. 收紧缓存适用边界，避免动作型回合缓存语义漂移
3. structured fallback 复用 `_prepare_llm_request()` 产物，不再手搓第二套请求语义
4. 保持现有 public API 与调用方兼容

---

## 3. 执行分解

## 3.1 Phase A：生命周期审计收口

目标：

1. 增加统一 `_emit_call_start/_emit_call_end/_emit_call_error` 内部 helper
2. 对 `native_tools_unavailable`、stream error 等 early-return 分支补齐 `CALL_ERROR`

影响文件：

1. `polaris/cells/roles/kernel/internal/llm_caller.py`

验收：

1. 每次 `CALL_START` 对应 `CALL_END` 或 `CALL_ERROR`
2. 关键 early-return 分支有明确错误事件

## 3.2 Phase B：缓存语义收紧

目标：

1. 增加 cache eligibility 判定：仅允许 plain-text / no-tools 场景
2. 防止未来 tool-call / structured 模式误命中文本缓存

影响文件：

1. `polaris/cells/roles/kernel/internal/llm_caller.py`
2. `polaris/cells/roles/kernel/tests/test_llm_caller.py`

验收：

1. 缓存命中不会用于 native tool 调用模式
2. response model 场景不走文本缓存

## 3.3 Phase C：structured fallback 对齐

目标：

1. fallback 路径复用 `prepared` 的 request options/context 基线
2. 仅在必要时移除 `response_format` 并追加明确 fallback instruction

影响文件：

1. `polaris/cells/roles/kernel/internal/llm_caller.py`
2. `polaris/cells/roles/kernel/tests/test_llm_caller.py`

验收：

1. structured fallback 不再手工重建脱离 contract 的简化请求
2. timeout / max_tokens / context 元数据与主路径一致

---

## 4. 风险与防御

1. 风险：事件补齐后影响现有观测基线
   防御：保持事件类型不变，仅补齐遗漏分支
2. 风险：缓存收紧导致命中率下降
   防御：优先保证语义正确性，后续再优化命中策略
3. 风险：structured fallback 改造触发解析差异
   防御：保留现有 parse/validate 逻辑，只替换 request 构建来源

---

## 5. 验证门禁

最小执行门禁：

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_llm_caller.py`
2. `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py`

如果第 2 条耗时过长，本轮至少保证第 1 条全通过，并在结果中记录残余风险。

---

## 6. 与既有治理资产关系

1. 复用 `vc-20260326-llm-maximum-convergence.yaml` 的总体方向
2. 本次新增专用 verification card 覆盖 `roles.kernel.llm_caller` 局部收口
3. 本次变更不修改 graph truth，不引入第二套边界真相

---

## 7. 执行快照（Round 1）

执行日期：2026-03-26

已完成：

1. `call_stream` 的 `native_tools_unavailable` 分支补齐 `CALL_ERROR` 事件。
2. provider 流式 `event_type=error` 分支补齐 `CALL_ERROR` 事件。
3. 移除 stream 内无效 `while True` + `saw_non_error_event` 冗余控制流。
4. 新增并通过 `test_llm_caller.py` 回归断言：
   - cache eligibility guard
   - structured fallback request baseline reuse
   - native tool mode 下禁用文本缓存

测试结果：

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_llm_caller.py` -> `58 passed`
2. `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py` -> `15 passed`

## 8. 执行快照（Round 2）

执行日期：2026-03-26

已完成：

1. 修复 `call_structured` fallback 分支在 `response.ok=False` 时的错误归类问题：
   先按 provider 返回错误走 `CALL_ERROR` 与 `_classify_error`，不再误落为 `validation_fail`。
2. 收口 `call_stream` 异常兜底事件结构：
   所有异常 `yield {"type":"error"}` 统一携带 `metadata.native_tool_mode/tool_protocol`。
3. 为上述两处新增回归测试并通过。

测试结果：

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_llm_caller.py` -> `60 passed`
2. `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py` -> `15 passed`

## 9. 执行快照（Round 3）

执行日期：2026-03-26

已完成：

1. 为 `call_stream` 增加统一事件归一化层 `_normalize_stream_chunk`：
   同时兼容 dict/object 两类 chunk，兼容 `event_type`/`type`/`kind` 事件键。
2. 增强 tool-call 负载兼容：
   支持 OpenAI `function` 形态（`function.name` + JSON 字符串 `function.arguments`）自动归一。
3. 增强错误兜底识别：
   对“缺少 event_type 但 metadata.error 存在”的流式块自动归类为 `error`，避免静默漂移。
4. 增强流式审计元数据：
   `CALL_END`/`CALL_ERROR` 现在都附加 `stream_event_count`，便于后续 trace 对账。

新增回归测试：

1. `test_call_stream_normalizes_object_chunk_event_with_type_field`
2. `test_call_stream_tool_call_supports_openai_function_shape`
3. `test_call_stream_infers_error_when_event_type_missing_but_error_present`

测试结果：

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_llm_caller.py` -> `63 passed`
2. `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py` -> `15 passed`

## 10. 执行快照（Round 4）

执行日期：2026-03-26

已完成：

1. 断流重连与幂等去重：
   - `call_stream` 引入 reconnect loop（默认最多 1 次，可通过 `context_override.stream_max_reconnects` 调整）。
   - 对重连后 replay chunk 执行前缀消费去重；对 `tool_call` 事件执行签名去重。
2. 流式事务闭环校验：
   - `events.py` 的 `LLMEventEmitter` 增加 lifecycle tracker。
   - 对 `CALL_START`/`CALL_END`/`CALL_ERROR` 进行 run_id 级闭环跟踪，并对“无 start 就 close / reopen 未闭合”计数告警。
3. Chaos 覆盖增强：
   - 新增对象事件兼容、缺失 event_type 错误识别、OpenAI function tool_call 解析、重连去重、event limit 等回归用例。
4. 取消与背压能力：
   - 支持 pre-invoke cancel（`context_override.stream_cancelled/cancel_requested`）。
   - 支持 `asyncio.CancelledError` 向上传播并审计为 `cancelled`。
   - 增加 backpressure wait 统计与阈值告警（`stream_max_backpressure_wait_ms`）。
5. 生产观测与 SLO 字段：
   - 在 `CALL_END`/`CALL_ERROR` 中新增 `stream_elapsed_ms/stream_first_event_latency_ms/stream_event_count/stream_events_per_second/stream_backpressure_wait_ms/stream_reconnect_count` 等指标。

测试结果：

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_llm_caller.py` -> `68 passed`
2. `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py` -> `15 passed`
3. `python -m pytest -q polaris/cells/roles/kernel/tests/test_llm_events_lifecycle.py` -> `4 passed`
