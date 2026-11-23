# 工具调用失败根因与宿主侧恢复 — 2026-11-23

## 现象（真实证据）

对 `~/.polaris/internal-engineering-bench/workspaces` 下 55 个 engineering 运行、
11000 次工具调用的全量取证：

| 结果 | 次数 | 占比 |
| --- | --- | --- |
| ok | 9503 | 86.4% |
| command_failure（真实构建/测试失败） | 1164 | 10.6% |
| host_validation（宿主拒绝畸形参数） | 320 | 2.9% |
| interrupted / timeout / other | 13 | 0.1% |

按工具看，失败集中在少数几个「宿主本可吸收」的形态：

- `report_review` 79/92 失败（86%）：条款矩阵覆盖校验。
- `verify_project` 179/221 失败：其中 162 是真实 verification 失败（应保留），
  15 是宿主参数拒绝。
- `execute_command` 917/4707：绝大多数是真实构建失败与 gate 拒绝。
- `browser_observe` 102/524：78 次宿主参数拒绝（action 类型、整数越界、时间预算）。
- `write_file` 148/1177：83 次宿主参数拒绝（缺 `file` 键、`content: null`）。
- `edit_file` 43/1042：25 次 `search` 零命中，只回一句错误，模型只能盲改。

## 根因

1. **条款引用契约对弱模型过难。** 宿主从目标机械抽取条款（含引导句、复选框），
   要求 QA 逐条「逐字/唯一摘录/编号」覆盖。真实运行里 QA 经常自造标签
   （`C1 judgment-table header row`）或只交 0/1 条占位，匹配数为 0，
   门户直接拒绝，QA 只能重试，直到预算耗尽。
2. **浏览器参数校验是严格类型校验。** `wait_ms` / 帧偏移必须是 int，
   action 类型必须精确等于四个枚举；模型给 `250.0`、`type`、`press` 就整轮报废。
3. **`write_file` 只认 `file` 键。** 模型给 `path` 或 `content: null` 直接拒绝。
4. **`edit_file` 零命中无恢复信息。** 只说 `observed 0`，模型不知道最近似文本。
5. **Wrap-up gate 把只读 `execute_command` 一并拦截。** 验证已通过后 `ls`/`cat`
   也被拒，纯属烧回合。

## 方案（全部落在 Polaris 元平台，通用、fail-closed 边界不变）

- 条款抽取：丢弃「引导句」（短且以 `：`/`:` 结尾）与表格头/分隔行，
  归一化 `[ ]`/`[x]` 复选框为普通条款。
- 条款引用：除逐字/唯一摘录/编号外，支持数字区间与列表（`1-5`、`1,2,3`）
  以及枚举标记（`C1`、`条款1`、`Clause 3`）。自造标签只要带序号即可解析。
- 覆盖门禁**有界纠正**：部分矩阵先退回（最多 2 次），仍不完整则接受并按
  `unknown` 诚实评级；`assess_fidelity` 依旧要求全覆盖才 `passed`，
  所以接受部分矩阵不会放宽验收，只终止烧预算的重试。
- 浏览器：action 类型别名表（`type`→`fill`、`press`→`keypress` 等），
  整数参数接受整值 float/数字串并夹取到边界，`keypress` 的 `value` 折叠为 `key`；
  归一化写入 observation 的 `normalization` 字段，可审计。
- `write_file`：接受 `path`/`file_path` 别名；`content: null` 视为缺省走既有
  「空文件 + 提醒」分支。
- `edit_file`：零命中时返回最近似文本片段与行号，便于一次改对。
- Wrap-up gate：仅拦截会改文件的 `execute_command`；只读命令放行。

## 数据流

模型原生调用 → `EngineeringTools.normalize`（别名/归一化）→ 各工具 handler
（浏览器 `_settings` 归一化、文件工具恢复信息）→ `report_review` 覆盖校验
(fd`match_clause_findings`) → observation（含 `normalization` / 恢复提示）。

## 边界与风险

- 引用解析只认「唯一」匹配；歧义摘录仍不建立覆盖（fail-closed 不变）。
- 条款抽取只丢布局/引导行，不改写真实需求文本。
- 浏览器归一化只影响渲染提示与参数形状，不放大权限；不允许的 action 仍拒绝。
- 不接受「按位置对齐」式猜测，避免把裁决错误归到别的条款。

## 验证

- 单元测试：条款抽取/引用、浏览器归一化、write/edit 恢复、gate 只读放行。
- 离线回归：对语料中 79 次失败的 `report_review` 重新判定覆盖，量化可挽救比例。
- 同运行验证：重跑 L1-01，比较工具失败率与终态。
