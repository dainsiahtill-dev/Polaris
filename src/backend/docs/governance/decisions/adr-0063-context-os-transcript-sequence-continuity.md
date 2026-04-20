---
status: 已实施
context: "Context OS 在 snapshot 续接时 sequence 回退，导致 latest intent 与 active window 焦点错位"
decision: "将续接轮次 sequence 改为基于 existing transcript 最大值单调递增，并仅在 resolving turn 显示 resolved follow-up"
consequences: "run_card/latest_user_intent 与真实最近轮次对齐；resolved follow-up 不再在后续轮次残留；active window recency 语义恢复"
---

# ADR-0063: Context OS Transcript 续接序号与 Follow-up 可见性收口

## 背景

在 Context OS attention runtime 的 existing_snapshot 场景中，出现了两个联动问题：

1. `_merge_transcript()` 对新消息使用 `enumerate()` fallback 赋号，导致续接轮次从 `0` 重新开始。
2. `_build_run_card()` 只要收到 `pending_followup` 对象就直接输出，已 resolved 的 follow-up 会在后续轮次残留。

这会导致：

- 最新 user 输入不一定被识别为 `latest_user_intent`；
- active window 可能围绕旧轮次构建；
- run_card 继续显示已完成的 follow-up，形成注意力污染。

## 决策

1. 在 `_merge_transcript()` 中引入续接序号语义：
   - 计算 `next_sequence = max(existing.sequence) + 1`；
   - 无显式 `sequence` 的新消息使用 `next_sequence`；
   - 显式 `sequence` 仍可保留，但会推动 `next_sequence` 前进，保持单调。

2. 在 `_build_run_card()` 中收紧 follow-up 可见性：
   - `pending` 状态始终可见；
   - `confirmed/denied/paused/...` 仅在“当前 resolving turn”可见；
   - 后续轮次默认隐藏 resolved follow-up 字段。

3. 同步补充回归测试，覆盖：
   - existing_snapshot 续接后 latest intent 对齐；
   - transcript sequence 单调递增；
   - latest event 在 active window 中；
   - resolved follow-up 在后续轮次不再出现在 run_card。

## 影响

### 正向

- 续接会话的 recency 语义恢复，latest intent 不再回退到旧轮次。
- run_card 的 follow-up 信息更符合用户当前对话阶段。
- attention runtime 与 continuity 的行为一致性提升。

### 代价

- `_merge_transcript()` 的赋号策略更严格，依赖 sequence 稳定性的外部调用需遵循单调语义。
- run_card 对 resolved follow-up 的展示从“持久展示”改为“当前轮次展示”，调试习惯需要更新。

## 验证

1. `pytest polaris/kernelone/context/tests/test_attention_runtime.py -q`
2. `pytest polaris/kernelone/context/tests/test_context_os.py polaris/kernelone/context/tests/test_continuity.py -q`
3. 手工复现：
   - `请帮我实现登录功能` -> `需要我帮你实现吗？` -> `需要` -> `好的，继续`
   - 验证 `latest_user_intent == "好的，继续"`，`pending_followup_*` 为空，`transcript` 序号递增。

## 关联资产

- `docs/governance/templates/verification-cards/vc-20260327-context-os-transcript-sequence-continuity.yaml`
