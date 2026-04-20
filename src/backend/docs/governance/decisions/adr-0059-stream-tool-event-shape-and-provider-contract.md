---
status: 已实施
context: "ADR-0058 修复 benchmark 合同穿透与多轮 tool loop 后，真实 agentic benchmark 仍暴露流式工具事件在 provider 形态、参数装配与 provider 合同上的结构性错位"
decision: "KernelOne/roles runtime 统一以 payload shape 为准解析流式工具事件；Anthropic 风格占位空参数必须延迟到 JSON delta 收齐后再定稿；provider 只要宣称支持原生工具，就必须在普通调用与结构化流式调用中等价透传 tools/tool_choice 并输出可审计事件"
consequences: "stream tool loop 不再因 provider 标签与 payload 形状错位而吞掉工具；Anthropic 兼容流不再把 `{}` 占位参数误当最终参数；MiniMax 等 provider 的 benchmark case 可以稳定产生原生 tool trace"
---

# ADR-0059: 流式 Tool 事件形态对齐与 Provider 合同收敛

## 上下文

`ADR-0058` 落地后，`agentic_benchmark` 已从 6/6 全失败收敛到部分通过，但真实运行仍暴露三类同源问题：

1. `TurnEngine.run_stream()` 收到的已经是 provider-neutral 的规范化 `tool_call` 事件，却仍然按 provider 标签选择 parser，导致 `anthropic_compat`/Kimi 元数据下的 OpenAI 形状工具事件被错误分派到 Anthropic 解析器，最终表现为工具零执行、首轮直接完成。
2. Anthropic 兼容流式协议会先在 `content_block_start` 发出 `tool_use(input={})` 占位壳，再通过后续 `input_json_delta` 逐步补齐真正参数。旧实现把这个空对象过早当成最终参数，随后与 delta 片段拼接，造成参数丢失或 JSON 污染。
3. 部分 provider 在普通调用与流式调用上对原生工具支持不对齐。只在某一路径传 `tools/tool_choice`，或只能输出文本流而不能输出结构化 stream events，会让 runtime 误以为“模型没调工具”，而不是 provider 契约未兑现。

这三个问题表面上分散在 TurnEngine、stream executor 和 provider adapter，根因却一致：系统把“provider 名称”误当成了比“真实 payload shape 与 contract”更可靠的真相源。

## 决策

### 1. 流式工具事件解析以 payload shape 为第一真相

只要事件已经进入 KernelOne/TurnEngine 的规范化路径，就必须优先根据 payload 结构识别工具调用，而不是依赖 provider 标签做硬分流。

这意味着：

- OpenAI 形状的 `tool_calls` / `function` payload 即使挂着 `anthropic` hint，也必须能被解析。
- 真正的 Anthropic `tool_use` payload 仍保留原生解析路径，但 provider 只作为 hint，而不是强制闸门。
- `TurnEngine` 在处理 stream `tool_call` 事件时，不能把规范事件误降级回“必须按原 provider 原封回放”的假设。

### 2. 占位空参数必须延迟定稿

对于 Anthropic 兼容协议中的 `input={}` / `arguments_text=="{}"` 起始占位：

- 在收到后续 `input_json_delta` 之前，它只能被视为 provisional state。
- stream executor 不得提前把 `"{}"` 写入最终 arguments buffer。
- 只有在流式结束且确实没有任何参数增量时，才允许把空对象视为合法最终参数。

因此，参数装配的 source-of-truth 变成“完整 delta 合并后的结果”，而不是第一个 start event。

### 3. Provider 原生工具支持必须保持 call/stream 等价

凡是 provider 声称支持 native tools，就必须在以下两条路径上保持同构：

1. `invoke()` / 非流式调用会真实透传 `tools`、`tool_choice`、`parallel_tool_calls`、必要的结构化输出配置。
2. `invoke_stream_events()` / 结构化流式调用会对同一份 payload 生效，并产出可被 stream executor 审计的原始 JSON 事件。

若 provider 只能输出文本流，不得在运行时被视为“已具备原生工具流式能力”。

## 后果

### 正向收益

- `chief_engineer_blueprint_review`、`pm_task_contract`、`architect_graph_first_boundary`、`qa_release_verdict` 等真实 benchmark case 可以稳定留下原生 tool trace，而不是退化成文本伪协议。
- TurnEngine、stream executor、provider adapter 三层对“工具调用何时成立”的判断标准一致，避免一层认为已经有工具，另一层却把它当普通文本。
- 后续新增 provider 时，是否支持 native tools 不再是“能不能拼出某种文本格式”，而是能否兑现统一的 call/stream contract。

### 代价

- provider 适配器必须补齐更多契约测试，不能只测非流式 happy path。
- stream executor 的状态机会更严格，后续若接入新的协议方言，需要显式建模 placeholder/delta/finalize 三段语义。

## 验证

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py polaris/cells/roles/kernel/tests/test_run_stream_parity.py polaris/cells/roles/kernel/tests/test_turn_engine_policy_convergence.py tests/test_kernelone_trace_and_stream.py tests/test_enhanced_providers.py tests/test_llm_agentic_benchmark.py`
2. `python -m polaris.delivery.cli agentic-eval --workspace . --case-id chief_engineer_blueprint_review`
3. `python -m polaris.delivery.cli agentic-eval --workspace . --case-id pm_task_contract`
4. `python -m polaris.delivery.cli agentic-eval --workspace . --case-id architect_graph_first_boundary`
5. `python -m polaris.delivery.cli agentic-eval --workspace . --case-id qa_release_verdict`
6. `python -m polaris.delivery.cli agentic-eval --workspace .`

预期：

- stream tool loop 在 provider hint 与 payload shape 不一致时仍能执行工具。
- Anthropic 兼容流的参数最终值来自合并后的 JSON delta，而不是 `{}` 占位壳。
- 全量 `agentic_benchmark` 达到 `PASS`，并留下非零 `tool_calls` 审计证据。

## 关联资产

- `docs/governance/templates/verification-cards/vc-20260327-stream-tool-provider-shape-mismatch.yaml`
- `docs/governance/templates/verification-cards/vc-20260327-agentic-benchmark-tool-loop-contract.yaml`
- `ADR-0058`

## 未来方向

下一步应把“provider 是否支持 native tools”上升为显式 capability contract，并在 provider 注册阶段就声明：

- 是否支持非流式 native tools
- 是否支持结构化流式事件
- 支持哪类 payload shape
- 是否存在 placeholder/delta 语义

这样 runtime 就能基于能力矩阵做分支，而不是靠 provider 名称和经验规则猜测。
