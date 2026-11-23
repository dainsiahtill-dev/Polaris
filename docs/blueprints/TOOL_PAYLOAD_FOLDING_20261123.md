# 智能折叠重构：从卡片到整个工具负载 — 2026-11-23

## 问题

旧折叠只作用于 `role == "tool"` 的结果消息里的 FileCard / CommandCard。真实 run 取证：
模型自己的 `write_file` / `edit_file` **参数**（文件全文）从不折叠，占请求的
**62.4% + 20.3% = 82.7%**；唯一能整轮丢弃的 `compress_native_tool_history`
因为工作集默认 256k + 窗口 1M 而全程空转（0 次折叠、0 个压缩标记）。

## 算法：两层 + 硬保护

模块 `engineering_tool_payload_projection.py`（原 `engineering_file_card_projection.py` 重构）。
两个平面共用一套策略：

- **结果平面**：`role == "tool"` 的 FileCard / CommandCard。
- **调用平面**：assistant `tool_calls` 中参数携带工作区正文的调用
  （仅 `write_file` / `edit_file` —— 正文可用 `read_file` 取回，折叠不丢独特证据；
  长 shell 脚本故意排除，它只存在于参数里）。

**硬保护（任何层都不折）**：当前编辑目标（最近 12 条消息的写/改路径）、
当前推理提及的路径（最近 8 条 assistant 文本）、验收条款提及的路径、
未解决失败（`exit != 0` 且在窗口内）、刚重新水化的卡片（grace=2）。

**第 1 层 · 时间窗**：`activity_age >= 24` 且 `payload >= 1024 字节` 且无 pin
→ 折叠，语义 reason（`superseded_by_newer_*` / `stalled_repeated_*` /
`outside_current_focus`）。这一层不需要预算，确定性的。

**第 2 层 · 工作集预算**：若投影后的工作集仍 > `32768 tokens`
（`KERNELONE_CONTEXTOS_FOLD_WORKING_SET_TOKENS`），按 `(age 降序, 可省 token 降序)`
继续折，直到底部 grace（2 个 activity）为止，直到装得下。

**折叠生效条件**：必须有可解释的替代（蒸馏摘要优先，否则确定性结构摘要），
且投影消息**严格更小**。否则保持 ACTIVE，绝不为折而折。

**协议完整**：只改 `tool_calls[i].function.arguments`，`id` / `name` 与配对的
`tool` 结果不变；折叠信封带 `fold_state` / `fold_reason` / `summary` /
`recovery` / `last_fold_activity_seq`。

## 实测（当前 run 的真实请求，离线重放）

| 请求 seq | 消息数 | 折叠前 | 折叠后 | 节省 | 折叠数 |
| --- | --- | --- | --- | --- | --- |
| 1229 | 223 | 138,796 | 106,866 | 31,930 (23%) | 41 |
| 1067 | 202 | 120,183 | 89,351 | 30,832 (26%) | 39 |
| 680 | 132 | 71,454 | 44,648 | 26,806 (38%) | 27 |
| 480 | 97 | 48,067 | 32,290 | 15,777 (33%) | 13 |

## 边界

- 预算层只折 `age >= grace` 的负载；当前工作与当前推理永不被折。
- 折叠可逆：正文在工作区，`read_file` 取回；历史参数可按 `tool_call_id` 查日志。
- 实时、确定性、无异步依赖（蒸馏静默时用结构摘要），单遍 O(messages)。
- 视觉工作集（浏览器截图）是另一条预算（`visual_payload_cost`），不在本模块。

## 验证

- 13 个调用平面测试 + 21 个原有卡片测试 + 147 个 context/loop/visual 测试全绿。
- 离线重放真实 run：写/改密集期请求下降 23%–38%。
- 同运行验证：新 run 对比折叠前后的请求 token 与终态。
