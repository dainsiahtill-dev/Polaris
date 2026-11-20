# QA 强制报告与工具参数归一化（2026-11-21）

## 动态证据

L1-04 run `9bd1cfe73dee41d1b59cc74fec040280`：Director 在 2072 秒 verification passed；QA 又执行 47+ 次 provider 请求、43 次 read_file、42 次 execute_command。turn_limit 后平台连续 6 次请求 `report_review`，MiniMax 仍返回 execute_command，最终 QA unknown / interrupted。

最小真实 provider 复现：物理请求已是 Anthropic 正确的 `tool_choice={"type":"tool","name":"report_review"}`，MiniMax 仍返回两个 execute_command；只广告 report_review 后，MiniMax 正确调用它。

## 设计

1. QA 已有当前 frame 的 source receipt 且进入 deadline/turn budget 收尾时，wire 只保留 report_review，并保留强制 tool_choice。
2. 解析后再次校验：该状态下任何非 report_review（或无调用）都在 dispatch 前拒绝，不产生工具效果；没有 source receipt 时仍允许一次读取。
3. execute_command 的 timeout 超过宿主上限时钳制到 120 秒；下限、布尔和非法文本仍 fail-closed。空 write_file、倒置行号不猜语义。

## 验证

- `test_qa_forced_report_limits_wire_and_rejects_ignored_provider_choice`
- `test_qa_reserves_last_minute_of_240s_for_evidence_bound_report`（OpenAI + Anthropic）
- `test_execute_timeout_above_host_ceiling_is_clamped_during_normalization`
- 目标测试 60 passed。
