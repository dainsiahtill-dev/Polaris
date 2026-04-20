---
status: 已实施
context: "Context OS 在短确认回复与低信号场景下出现注意力回退，导致 run_card/next focus 停留旧任务"
decision: "引入 assistant follow-up 单跳确认绑定（question -> next user reply），并在 active window 增加最新消息硬保留策略（默认至少 3 条）"
consequences: "用户回复“需要/继续”等短确认时可稳定承接上一条待确认动作；最近消息不会被过早清洗；ContextSlicePlan 不再把 active clear 事件错误标为 excluded"
---

# ADR-0060: Context OS 跟进意图绑定与最新窗口硬保留

## 背景

真实对话中常出现以下模式：

1. assistant: “需要我深入查看并修复吗？”
2. user: “需要”

旧实现的问题：

- `GenericContextDomainAdapter.extract_state_hints()` 只从当前文本提取语义，`需要`本身没有任务对象，无法更新 `current_goal/open_loops`。
- `StateFirstContextOS._collect_active_window()` 依赖 `recent_window_messages`，在 `recent_window_messages=1` 与低信号清洗下，最新确认消息可能被移出 active window。
- `ContextSlicePlan.excluded` 先判断 `clear` 再判断 `active`，导致少数近期 clear 事件出现“既 active 又 excluded”的冲突。

## 决策

1. 在 `runtime._canonicalize_and_offload()` 引入 follow-up 绑定：
   - 从 assistant 问句抽取 `followup_action`；
   - 仅在“下一条 user 回复”为短确认时强制路由为 `PATCH`；
   - 将 `followup_action/followup_confirmed` 写入事件 metadata，供 domain adapter 提取状态补丁。

2. 在 `GenericContextDomainAdapter.extract_state_hints()` 支持 follow-up metadata：
   - `followup_confirmed=true` 时，把 `followup_action` 提升到 `goals/open_loops`；
   - 同时写入一条 decision 摘要，确保运行时可审计。

3. 在 policy 中新增 `min_recent_messages_pinned`（默认 3）：
   - `_collect_active_window()` 至少保留最近 3 条消息；
   - 即使消息 route=clear，只要属于最近硬保留范围仍会进入 active window。

4. 修正 `ContextSlicePlan` 排除逻辑顺序：
   - 先过滤 active 事件，再处理 clear/inactive 分类，避免 active 与 excluded 冲突。

## 影响

### 正向

- “需要/继续/开始”类短回复不再导致上下文焦点回退。
- 最近 2-3 条会话可稳定保留，调试与用户体验更一致。
- slice plan 的 included/excluded 语义一致性提升。

### 代价

- Active window 在极端小预算下会多占用少量 token（默认最多 3 条消息保底）。
- follow-up 抽取规则需持续回归，避免误绑定。

## 验证

1. `pytest polaris/kernelone/context/tests/test_context_os_domain_adapters.py -q`
2. `pytest polaris/kernelone/context/tests/test_context_os.py -q`
3. `pytest polaris/kernelone/context/tests/test_continuity.py -q`

新增断言覆盖：

- short confirmation + follow-up metadata => `goals/open_loops` 正确提取；
- assistant follow-up question + user“需要” => `run_card.next_action_hint` 指向 follow-up action；
- `recent_window_messages=1` 仍保留最新 3 条消息；
- active window 事件不进入 `excluded`。

## 关联资产

- `docs/governance/templates/verification-cards/vc-20260327-context-os-attention-followup-window.yaml`
