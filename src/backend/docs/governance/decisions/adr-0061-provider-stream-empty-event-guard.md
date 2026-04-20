---
status: 已实施
context: "真实 `anthropic_compat` / Kimi 流式响应已经返回 SSE 事件，但共享 helper 因 JSON decode 依赖缺失把所有 data 帧静默丢弃，TurnEngine/Kernel 又把空回合误判为成功完成，导致 benchmark 审计出现 empty complete"
decision: "共享 stream helper 必须显式 decode SSE/JSON 流并在零结构化事件时失败；TurnEngine 必须拒绝无内容无工具的空回合；Kernel 只有在存在真实 tool_calls/tool_results 时才允许 tool-only fast path"
consequences: "provider 契约异常会暴露成可诊断错误而非假成功；chief_engineer/director 等 benchmark case 恢复真实 tool trace；后续第三方 provider 即使出现非标准流或空流，也不会再穿透成 PASS"
---

# ADR-0061: Provider 空流护栏与空回合拒绝

## 上下文

在 `X:/BaselineTest` 的真实 `agentic_benchmark` 回归里，`chief_engineer` 与 `director` case 都收敛到同一种失败形态：

1. 角色被正确解析到 `anthropic_compat-1771249789301` / `kimi-for-coding`。
2. provider 实际返回 `status=200`、`content-type=text/event-stream;charset=utf-8` 的 SSE 流。
3. runtime 却只看到最终 `complete(content='')`，没有任何 chunk、thinking 或 tool_call。

进一步排查后发现，这不是 “SSE 不稳定” 或 “需要改成 WebSocket”，而是两个结构性缺口叠加：

1. `provider_helpers.invoke_stream_with_retry()` 在解析 SSE `data:` 帧时调用了 `json.loads(data_str)`，但模块没有 `import json`。异常被 `except Exception: continue` 静默吞掉，于是所有事件都被丢弃。
2. 即使 provider 最终 yield 了零事件，`TurnEngine` 只拒绝 thinking-only 回合，没有拒绝真正的 blank turn；`Kernel` 还把“空文本”直接等同为 tool-only turn，没有要求真实工具证据。

结果就是：上游明明已经异常，系统却把它包装成“正常完成但内容为空”的假成功。

## 决策

### 1. Shared stream helper 必须兑现结构化事件契约

`invoke_stream_with_retry()` 作为共享 helper，必须承担下面三件事：

- 显式导入并使用 `json` 解析 SSE `data:` payload。
- 当响应 `content-type` 为 `application/json` 时，走 JSON fallback，而不是假设所有流都长成 SSE。
- 当整个响应周期一个结构化事件都没解出来时，抛出 `provider_stream_empty`，而不是静默返回空集合。

这使“provider stream contract 没有兑现”成为可见错误，而不是被吞掉。

### 2. TurnEngine 必须拒绝 blank turn

只要一轮 assistant turn 同时满足：

- `clean_content` 为空
- `thinking` 为空
- `parsed_tool_calls` 为空

就必须返回 `assistant_visible_output_empty` 错误。

thinking-only 仍然是该规则的一个子集，但 blank turn 也要被同样拒绝。

### 3. Kernel 的 tool-only fast path 必须要求真实工具证据

`validate_output=True` 时，只有当 `tool_calls` 或 `tool_results` 至少有一项非空时，才能把回合视为 tool-only 并跳过文本校验。

“没有文本”本身不是 tool-only 的充分条件。

## 后果

### 正向收益

- `anthropic_compat`/Kimi 的真实 benchmark case 会恢复结构化 stream trace，而不是出现空完成。
- provider 合同异常、空 body、事件解析失败会更早暴露，便于直接追根因。
- runtime 不再把“没有任何可见产出”的回合包装成成功结果。

### 代价

- 共享 helper 的错误面会更显式，某些过去被吞掉的 provider 问题现在会直接失败。
- 需要补更多 provider/helper 层回归测试，确保新 provider 不再依赖“静默 continue”。

## 验证

1. `python -m pytest -q tests/test_provider_helpers.py tests/test_anthropic_compat_streaming.py polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py tests/test_roles_kernel.py`
2. `python -m polaris.delivery.cli agentic-eval --workspace X:/BaselineTest --suite agentic_benchmark --case-id chief_engineer_blueprint_review`
3. `python -m polaris.delivery.cli agentic-eval --workspace X:/BaselineTest --suite agentic_benchmark`

预期：

- shared helper 能解出真实 SSE/JSON 事件，并对 empty stream 报错。
- blank turn 不再产生 `complete(content='')`。
- `agentic_benchmark` 在 `X:/BaselineTest` 恢复 PASS，并重新留下非零 tool trace。

## 关联资产

- `docs/governance/templates/verification-cards/vc-20260327-provider-helper-empty-stream-guard.yaml`
- `docs/governance/templates/verification-cards/vc-20260327-stream-tool-provider-shape-mismatch.yaml`
- `ADR-0059`
