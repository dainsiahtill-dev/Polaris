# ADR-0045: Roles Session Continuity Memory And Context Layering

- Status: Accepted
- Date: 2026-03-25
- Scope: `roles.session`, `roles.runtime`, `roles.kernel`, `polaris-cli`
- Related Verification Card: `vc-20260325-roles-session-continuity-memory`

## Context

角色对话出现的异常不是单一的 `<thinking>` 泄露问题，而是更深层的上下文治理失配：

1. 新 CLI 会话会无意复用旧 session。
2. 旧 history 既通过 `history` 传入，又被塞进 `context_override` 作为 system JSON 重复注入。
3. `compression_strategy=summarize` 在 kernel 中并未生成真实 continuity summary，只是退化成滑动窗口。
4. 低价值元话题（如改名、身份问答）与高价值工程上下文没有分层，导致模型把旧闲聊反复当作当前任务。

这让显式 resume、不同宿主入口和长对话场景都容易出现“旧话题反复”“模型围绕过时上下文继续思考”的行为。

## Decision

采用统一的 `session continuity memory` 策略：

1. 默认新会话
   - `terminal_console` 与 `RoleConsoleHost.stream_turn()` 在没有显式 `session_id` 时都创建新 session。
   - 只有显式 resume 才加载旧 session。

2. 分离 runtime metadata 与 model context
   - `role/host_kind/session_id/history/capability_profile/workspace` 等内部运行时字段不再进入 `context_override`。
   - `context_override` 只保留真正该给模型看的上下文。

3. 引入 rolling continuity summary
   - `RoleConsoleHost` 对旧消息做“摘要锚点 + 最近窗口”投影。
   - continuity summary 持久化到 session `context_config.session_continuity`，并带 `compacted_through_seq` 水位。
   - resume 时优先复用已存在 summary，只对新落入旧窗口的消息做增量滚动。

4. kernel 侧让 `summarize` 成为真实策略
   - `RoleContextGateway._process_history()` 在历史超窗时生成 continuity summary。
   - `RoleContextGateway._apply_compression()` 对 `summarize` 策略优先执行真实摘要，而不是假装开启摘要却仅做滑窗。
   - `KernelOne` context compaction 在无 LLM 时退化为 deterministic continuity summary，而不是直接 truncate。

## Consequences

### Positive

- 新会话默认不再被旧 session 污染。
- 显式 resume 时，模型看到的是“连续性摘要 + 最近窗口”，而不是整段旧聊天。
- `summarize` 策略语义与实际行为一致，减少 profile 配置名实不符。
- 低价值元话题更容易在 continuity summary 中被衰减，不再压过工程主线。

### Negative

- continuity summary 目前仍是 deterministic heuristic，不是基于专用总结模型的语义记忆。
- 摘要质量仍受信号规则影响，某些边界案例可能需要后续调优。

## Verification

已通过：

- `python -m pytest -q polaris/delivery/cli/director/tests/test_stream_protocol.py`
- `python -m pytest -q polaris/cells/roles/kernel/tests/test_transcript_leak_guard.py`
- `python -m pytest -q polaris/delivery/cli/tests/test_terminal_console.py`
- `python -m pytest -q polaris/cells/roles/kernel/tests/test_stream_visible_output_contract.py polaris/cells/roles/kernel/tests/test_run_stream_parity.py`
- `python -m pytest -q polaris/cells/roles/kernel/tests/test_kernel_stream_tool_loop.py`

## Follow-up

1. 若后续引入专用 summary model，应复用同一 `session_continuity` 数据面，不再创建第二套记忆机制。
2. continuity summary 的高低信号判定可以继续产品化为可配置策略，但不能回退到“整段历史原样回灌”。
