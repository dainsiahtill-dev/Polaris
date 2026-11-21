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

## 首次验证预算（L1-06 后续修复）

L1-06 run `3ee2e82d5e3144c4958f618580ef4094` 在 5352/5400 秒才第一次 verify，随后 Director deadline exceeded；验证还选择了不存在的 cmake/ctest，并写 `build/verify_out` 触发 EROFS。

新增 `KERNELONE_ENGINEERING_FIRST_VERIFICATION_FRACTION`（默认 0.4）：当 Director 到 run 预算 40% 仍无 verify_project 结果时，wire 停止广告 write_file/edit_file/repo_tree/repo_rg，只保留 verify_project、execute_command、read_file、browser_observe；即使 provider 仍生成写入调用，也在 dispatch 前拦截。execute_command 始终保留用于构建/依赖准备，避免 EROFS 死锁。

新增 `test_first_verification_budget_stops_late_source_writes`；目标测试 61 passed。

## Markdown 表头不是验收条款（L1-01）

L1-01 run `53eac3bdbcdb410cb2b9609b5f41775f` 的 QA 已批准所有功能条款，但 `| 输入／操作 | 预期结果 |` 被错误抽成独立 clause。QA 只能将其标为 unknown，最终 run interrupted/unknown。

`_goal_clause_candidates` 现在识别“紧接分隔行的 Markdown 表头”并跳过，保留真实数据行；目标验收测试 27 passed，clause/fidelity 回归 19 passed。

## 调用参数与 artifact 归一化（L1-05/L1-06 统计后）

- `write_file.file` 含换行时前置报出“正文应放 content”，避免整篇文档进入路径后由 OS 报 File name too long；NUL 给出明确 UTF-8 纠错。
- `repo_rg` 对只读查询中的幻觉元数据（如 `context_labels`）忽略并在结果中注明，查询参数仍严格校验。
- `browser_observe` viewport 宽高钳制到宿主范围；frame/action 时间仍严格。
- artifact 在命令执行前拒绝不支持的类型（如 `.txt`），只允许 SVG/HTML/PNG/JPEG，并明确必须在本调用 `POLARIS_SANDBOX_OUTPUT` 生成。
- EROFS 反馈覆盖 `open ./mpqa` 与 `cp: cannot create regular file ...`，指出实际写入目标，并说明 `POLARIS_SANDBOX_OUTPUT` 与 `/tmp`/HOME 单调用生命周期。
- 无效 artifact_ref 提示使用当前 run 中 execute_command 返回的 `artifact_[0-9a-f]{32}`。

目标测试 78 passed，ruff/mypy 通过。
