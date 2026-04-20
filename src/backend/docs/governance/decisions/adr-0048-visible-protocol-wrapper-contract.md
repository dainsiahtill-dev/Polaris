# ADR-0048: Visible Output Must Strip Protocol Wrappers Before Reaching Any Host

状态: Accepted
日期: 2026-03-25

---

## 1. 背景

在修复 unified terminal console 的 `thinking_chunk` 消费后，真实 CLI 烟测仍暴露出新的可见面问题：

1. `<thinking>` 已经恢复
2. 但正文仍会出现 `<output>...</output>`
3. tool_call / tool_result 虽然仍存在，但它们和正文协议文本边界继续混在一起

这说明问题并不在终端末端，而在 `roles.kernel` 的用户可见输出合同还不完整。

---

## 2. 问题

当前可见输出链分裂成了两套不一致的规范化逻辑：

1. streaming path
   - `_BracketToolWrapperFilter`
   - `StreamThinkingParser`
2. final materialization path
   - `OutputParser.parse_thinking()`
   - `TurnEngine._sanitize_assistant_transcript_message()`

其中：

1. streaming path 会去掉 `[TOOL_CALL]` 和 `<thinking>`
2. final path 会去掉 textual tool wrapper
3. 但两边都没有完整处理 `<output>`

于是 `<output>` 会同时污染：

1. visible stream
2. complete.content
3. transcript history

---

## 3. 决策

把 `<output>` 及同类“用户不可见协议包裹”收敛为 kernel 级 canonical sanitize contract。

### 新规则

1. `StreamThinkingParser` 必须把 `<output>` 当作 answer/content container 处理：
   - 标签本身不可见
   - inner content 正常进入 content stream

2. `OutputParser.parse_thinking()` 返回的 `clean_content` 必须额外剥离可见协议包裹：
   - `<output>...</output>` 标签
   - `<answer>...</answer>` 标签
   - 非用户可见的 tool_result 类标签

3. Host 层只负责消费 canonical visible contract，不再各自补充“再剥一层标签”的末端修补。

---

## 4. 为什么不是只改 terminal_console

因为 `terminal_console` 只是其中一个 host。

如果只在 host 末端剥标签：

1. transcript 仍被污染
2. 其他 host / projection / router 仍会继续泄漏
3. 同一个 assistant turn 在不同入口会出现不同可见结果

根因必须收敛到 `roles.kernel` 的 visible contract。

---

## 5. 后果

### 正面影响

1. `<output>` 不再进入任何用户可见 host
2. transcript/history 也不再积累协议文本
3. tool_call/tool_result 与正文的边界重新稳定
4. thinking / content / tool 三种流统一建立在同一 canonical materialization 上

### 代价

1. 需要维护 stream parser 与 final materializer 的一致性测试
2. 如果未来再引入新的协议标签，也必须在 kernel 级统一注册，而不是由各 host 自己修

---

## 6. 验证

1. `python -m pytest -q polaris/cells/roles/kernel/tests/test_stream_visible_output_contract.py`
2. `python -m pytest -q polaris/cells/roles/kernel/tests/test_turn_engine_semantic_stages.py`
3. `python -m pytest -q polaris/delivery/cli/tests/test_terminal_console.py`
4. 真实 CLI 烟测：`python -m polaris.delivery.cli console ...`

---

## 7. 后续方向

后续若继续演进协议输出，必须遵守同一原则：

1. provider/raw protocol tags 只能存在于解析输入面
2. user-visible stream/transcript 只能看到 canonical sanitized output
3. host 层不再自行发明协议清洗逻辑
