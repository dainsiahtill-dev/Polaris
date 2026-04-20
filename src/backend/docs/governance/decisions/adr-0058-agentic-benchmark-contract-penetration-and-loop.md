---
status: 已实施
context: "agentic benchmark 在真实 runtime 下 6/6 失败，暴露执行合同与评测合同脱节，以及 TurnEngine 单轮执行问题"
decision: "benchmark 合同显式穿透到 runtime prompt；TurnEngine 恢复 transcript-driven 多轮 tool loop；PromptBuilder 接收当前用户消息参与意图推断；ollama 原生工具流补齐；benchmark 判定器修复多行 structured steps 误判"
consequences: "benchmark judge 与 runtime 使用同一份执行约束；stream / non-stream 都能在工具结果后继续下一轮回答；本地 ollama 运行时不再把工具调用降级为纯文本建议"
---

# ADR-0058: Agentic Benchmark 合同穿透与多轮 Tool Loop 收敛

## 上下文

`python -m polaris.delivery.cli agentic-eval --workspace .` 在 2026-03-27 的真实运行中出现 6/6 失败。失败并非单点，而是两类结构性问题叠加：

1. `agentic_benchmark` 的 case 合同只存在于 judge，runtime 只收到原始用户 prompt，看不到“必须调用哪些工具、必须输出什么结构”。
2. `TurnEngine.run()` / `run_stream()` 名义上是 transcript-driven tool loop，实际上只执行一轮 LLM 决策；一旦首轮产生 tool_call，工具结果不会驱动第二轮总结。
3. `ollama` provider 在 `/api/chat` 路径没有把 `tools`/结构化流事件纳入执行链，导致 benchmark 所需的 native tool trace 在 provider 层丢失。
4. benchmark 的 `structured_steps` 判定器只检查整段文本开头，无法识别后续行上的编号步骤，产生误判。

这导致：

- PM / Architect / QA 即使失败，runtime 也不知道 benchmark 期望的确定性合同。
- Chief Engineer / Director 即使发出了原生 tool_call，也会在首轮后直接 `complete(content='')`。

## 决策

### 1. Benchmark 合同必须进入执行提示

`agentic_benchmark` 不再只把 case 合同留给 judge。每个 case 会生成一段 runtime appendix，明确：

- 当前处于 deterministic benchmark
- 必须优先使用哪些原生工具
- 必须命中的文件/路径证据
- 最终输出格式与禁止项

该 appendix 通过 `ExecuteRoleSessionCommandV1.metadata.prompt_appendix` 进入 runtime，成为 system prompt 的一部分，而不是事后审计信息。

### 2. TurnEngine 恢复真正的 transcript-driven loop

`TurnEngine.run()` 与 `run_stream()` 改为按 cycle 迭代：

1. 用当前 transcript 构建 `ContextRequest`
2. 发起 LLM 调用
3. 解析原生工具调用
4. 执行工具并记录结果
5. 将 assistant clean_content + tool receipts 追加回 transcript
6. 只有在“本轮无工具调用”时才完成；否则进入下一轮

因此，工具结果不再是终点，而是下一轮 LLM 的输入事实。

### 3. PromptBuilder 的意图推断必须看到当前消息

`RoleExecutionKernel._build_system_prompt_for_request()` 调用 `PromptBuilder.build_system_prompt()` 时必须传入 `request.message`，保证工具策略提示能根据当前任务意图而不是空消息推断。

### 4. Ollama 必须走结构化原生工具流，而不是文本兼容流

`ollama` provider 补齐两项能力：

- `/api/chat` / OpenAI compatibility 请求要真实携带 `tools`
- provider 实现 `invoke_stream_events()`，并通过专用 adapter 把 `message.thinking` / `message.tool_calls` 解码为 KernelOne 结构化事件

同时，ModelCatalog 对带 registry 前缀的本地模型名（如 `modelscope.cn/.../Qwen3...`）补充别名识别，避免工具能力被误判为不支持。

### 5. Benchmark 判定器接受多行结构化步骤

`looks_like_structured_steps()` 从“只看整段首行”改为“逐行识别编号步骤/项目符号”，与 case 文案“reply with short numbered steps”的真实语义保持一致，避免把合法答案误判为失败。

## 后果

### 正向收益

- benchmark judge 与 runtime 不再脱节，评测失败可以转化为执行期约束。
- stream / non-stream 行为收敛，Chief Engineer / Director 的工具调用不会在首轮后丢失。
- `ollama` 本地运行时终于具备 benchmark 需要的原生工具证据链，而不是依赖模型自行输出 `[TOOL_CALL]`/JSON 伪协议。
- benchmark 的失败信号更接近真实根因，而不是“模型没猜中 judge 想要什么”。

### 代价

- 部分旧测试原先把“单轮执行”误当成正确行为，需要回归到真正的 transcript-loop 语义。
- 多轮循环会增加一次或多次 LLM 调用，因此需要继续依赖 `ToolLoopController` 与 `PolicyLayer` 控制预算、stall 与 wall time。

## 验证

1. `python -m pytest -q tests/test_llm_agentic_benchmark.py polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py polaris/cells/roles/kernel/tests/test_run_stream_parity.py tests/test_enhanced_providers.py tests/test_kernelone_trace_and_stream.py tests/test_llm_token_budget.py tests/test_llm_caller.py tests/test_llm_deterministic_judge.py`
2. `python -m polaris.delivery.cli agentic-eval --workspace .`

预期：

- benchmark case 会看到 prompt appendix 合同
- `run()` / `run_stream()` 在首轮 tool_call 后继续第二轮
- CLI benchmark 达到 PASS

## 未来方向

下一步应把“执行合同”提升为显式 typed contract，而不是先拼接 appendix 文本，再由 prompt 解释。理想形态是：

- benchmark / readiness / qualification suite 共享统一的 typed execution contract
- runtime / quality checker / judge 消费同一份 contract
- 模型只负责在 contract 约束内决策，不再依赖字符串提示对齐
