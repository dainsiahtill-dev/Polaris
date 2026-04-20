# ADR-0054: Native Tool Calling Only Execution Boundary

- 状态: Accepted
- 日期: 2026-03-26
- 相关 VC: `vc-20260326-native-tool-calling-only`

## 背景

Polaris 近期已将上游 LLM 工具调用契约收敛为 provider-native `tool_calls`
/ `function_call`。但实际执行链仍残留历史兼容逻辑：

1. `TurnEngine` 仍会从 assistant text wrapper 中解析可执行工具调用。
2. `kernelone.llm.toolkit.parsers` 仍把 prompt/xml/tool_chain 当成运行时工具协议。
3. provider adapter 仍把 `ToolCall` transcript item 序列化为 `[TOOL_CALL]` 文本，
   再喂回模型。

这导致系统表面上采用 native tool calling，实际却是 native + textual 双协议混跑。

## 决策

从本 ADR 起，Polaris 的 **执行边界** 收敛为：

1. 运行时只执行 provider-native `tool_calls`。
2. `assistant.content` / `thinking` / wrapper / XML / prompt-based 工具协议
   不再具备可执行语义。
3. 文本 wrapper 仅允许用于：
   - 可见面清洗
   - 协议违规检测
   - 历史兼容审计
4. `kernelone/editing/* + protocol_kernel` 继续作为 canonical 编辑协议栈，
   但它属于“工具执行后的编辑内容归一化”，不属于 LLM 工具调用协议本身。

## 后果

### 正面

1. `TurnEngine` 真正成为 native tool calling 执行引擎。
2. 消除 transcript / example / quoted wrapper 被再次执行的循环风险。
3. 工具执行审计与 provider contract 对齐，不再出现双协议歧义。
4. 编辑协议与工具调用协议边界清晰：
   - tool calling -> native only
   - editing payload routing -> `kernelone/editing/*`

### 负面

1. 大量依赖 wrapper fallback 的旧测试需要重写。
2. 某些兼容路径如果仍只产生文本 wrapper，将不再触发工具执行，而会直接暴露为协议违规。

## 实施要点

1. `TurnEngine` / `kernel._parse_content_and_thinking_tool_calls()` 改为 native-only。
2. `OutputParser.parse_execution_tool_calls()` 改为 native-only normalizer。
3. `TurnDecisionDecoder` 关闭 textual fallback。
4. `kernelone.llm.toolkit.parsers.parse_tool_calls()` 改为 native-only unified parser。
5. provider adapter 不再把 `ToolCall` transcript item 渲染成 `[TOOL_CALL]` 文本。
6. `tool_call_protocol` 降级为 sanitization / detection helper，不再承担执行语义。

## 验证

1. native tool_calls 的 `run()` / `run_stream()` 路径通过回归。
2. text wrapper 不会再触发工具执行。
3. transcript leak guard 继续保证 wrapper 不进入用户可见历史。
4. `kernelone/editing` 的 apply path 保持独立，通过自身测试。
