# 智能折叠：单条焦点窗规则 + 平面对称阈值 — 2026-11-23（取代两层的旧版）

## 问题

旧折叠只作用于 `role == "tool"` 结果消息里的 FileCard / CommandCard。真实 run 取证：
模型自己的 `write_file` / `edit_file` **参数**（文件全文）从不折叠，占请求的
**62.4% + 20.3% = 82.7%**。

后来加的两层算法（时间窗 + 32k 工作集预算）在真实 run 上暴露出相反的问题：

- **预算层（grace=2）压过了时间窗**。41 次折叠里 41 次是 `working_set_pressure`，
  时间窗层 0 次。32k 预算太小，压力层永远先开火。
- **折叠读结果 = 强迫重读**。`read_file` 27/27 次都是重读一条已被折叠的路径，
  `src/core/simulation.ts` 被读 5 次并触发 3 次 `stall_intervention`。
- 最大的负载压不掉：当前编辑目标被 pin，预算层只去折小的旧负载。

## 现在的算法：一条规则，两个平面

模块 `engineering_tool_payload_projection.py`。折叠**只有一条判据**：

```
activity_age >= fold_window(24) 且 payload >= 平面字节下限 且 无 pin
```

**没有工作集预算层，没有 grace**。"现在太大了所以折" 是一种压力阀，它折掉的
正是模型还在用的负载；真正该折的东西，焦点窗已经能识别。

### 平面对称阈值（替代成本不同）

| 平面 | 下限 | 替代成本 |
| --- | --- | --- |
| 调用平面（`write_file`/`edit_file` 正文） | `KERNELONE_CONTEXTOS_FOLD_MIN_BYTES` = 1024 | ≈ 0，正文已落盘 |
| 结果平面（FileCard / CommandCard） | `KERNELONE_CONTEXTOS_FOLD_RESULT_MIN_BYTES` = 4096 | 一次完整重读 |

### 硬保护

- `current_edit`：最近 12 条消息的写/改路径。
- `current_focus`：**焦点窗（最近 `fold_window` 条消息）里被读或写过的任何路径**。
  这是断掉"折叠 → 重读 → 再折叠"循环的关键：模型反复回来的文件永远在焦点里。
- `current_reasoning`：最近 8 条 assistant 文本提及的路径。
- `acceptance_clause`：验收条款提及的路径。
- `unresolved_failure`：`exit != 0` 且在窗口内。
- 刚重新水化的卡片（`KERNELONE_CONTEXTOS_REHYDRATION_GRACE_ACTIVITIES` = 2）。

`forced`（stall 干预的显式覆盖）只绕过年龄窗，**不绕过焦点窗**。

### 折叠信封保留最小预览

折过的 FileCard 不再清空 `content_preview`，而是保留前
`KERNELONE_CONTEXTOS_FOLD_KEPT_PREVIEW_BYTES`（默认 512）字节，并标
`content_bounds.truncated`。模型想确认"这文件大概是什么"时不必重读，
只有真要改它才付这次 `read_file`。

**折叠生效条件**：必须有可解释的替代（蒸馏摘要优先，否则确定性结构摘要），
且投影消息**严格更小**。否则保持 ACTIVE，绝不为折而折。

**协议完整**：只改 `tool_calls[i].function.arguments`，`id` / `name` 与配对的
`tool` 结果不变；折叠信封带 `fold_state` / `fold_reason` / `summary` /
`recovery` / `last_fold_activity_seq`。

## 三个认知插件的实测与定位（22 个有活动的真实 run）

| 插件 | 实测 | 结论 |
| --- | --- | --- |
| 蒸馏器 | 571 次产出 / 714 次折叠，**195 次真的用上模型摘要 = 27%**；16/20 个 run 首次交付早于首次折叠 | 有效，覆盖率有限 |
| 制图师 | 通过 **32** / 拒绝 **542** = **5.6%**（`narrative_disagrees_with_facts` 334、`unparsable_or_non_object` 110、`invalid` 96） | 当前形态主要做无用功 |
| 吞噬兽 | **17 个有卡的 run，第一张卡全部出现在第 61 轮** | 结构上注定晚到，实际影响≈0 |

**共同病根**：让小模型生产结构化内容，再由宿主严格校验。校验越严，浪费越大。

### 修法

- **制图师**：宿主确定性 RunCard（`deterministic_run_card`）。phase / completed / blocker / next_actions 全部由持久事实推导，
  制图师通过校验的叙述只覆盖 `narrative` 字段。
  **它只作为可观测事实发布（`run_card_active`），不注入 provider 请求**：作为第二条 system 消息加进去会让 native 编码器把它并入
  角色身份，破坏"角色指令逐字节不变"这条不变量（实测 `budget_visibility` / `qa_working_state` / `visual_projection` 三条既有测试同时回归）。
  模型的阶段感继续由既有的 stage gate、ContextOS 记忆卡与 Resident AGI 承担；5.6% 通过率不再让运行实况 UI 显示"尚未绘制"。
- **吞噬兽**：提前量 50 → **20**（第一个 epoch 在第 30 轮可用，而不是第 61 轮），并修掉 `EpochRuntime.schedule`
  每轮取消在途任务的毛病（与蒸馏器同一个 bug）。
- **蒸馏器**：候选门槛按平面分离 —— 调用平面 `distill_min_bytes()`（512），结果平面 `distill_result_min_bytes()`（= 折叠门槛的一半 = 2048）。
  不再摘要永远不会被折的小卡片。

## 读文件未命中的恢复

`read_file tools/serve.js` 在 TypeScript 项目里是真实未命中，但旧回复只说"不存在"，模型只能再猜一轮（实测：turn 1251 读 `.js` 失败，
turn 1265 改读 `.ts` 才成功）。现在宿主给出相近候选：同主干不同后缀（`tools/serve.ts`）、编译对应路径（`dist/tools/serve.js`），
都经过存在性校验；全仓同名兜底扫描有 4000 文件上限并跳过 `node_modules` / `.git` / `.polaris` 等目录。

## 蒸馏器的定位

蒸馏器的门槛派生自折叠门槛（`distill_min_activities = fold_window - 14`，
`distill_min_bytes = fold_min_bytes / 2`），并且**从不在在途时取消任务**。
折叠只剩年龄层之后，蒸馏器第一次真正跑在折叠前面（此前首次折叠 seq 179
早于首次蒸馏 seq 235，模型摘要 41 次里只用了 2 次）。

## 边界

- 折叠可逆：正文在工作区，`read_file` 取回；历史参数可按 `tool_call_id` 查日志。
- 实时、确定性、无异步依赖（蒸馏静默时用结构摘要），单遍 O(messages)。
- 视觉工作集（浏览器截图）由 `select_visual_working_set` 按**模型窗口**收敛，
  没有独立的 32k 预算；mandatory 捕获只有在装不下窗口时才降级。
- 整轮丢弃（`compress_native_tool_history`）是另一条独立机制，按窗口尺度触发。

## 验证

- 折叠相关测试全绿（`test_engineering_tool_call_projection` /
  `file_card_projection` / `file_card_projection_lifecycle` /
  `command_card_projection` / `fold_events` / `low_context`）。
- 新增回归：平面字节下限、焦点窗保护、折叠信封保留预览、无预算层时焦点窗内不折。
- 真实 L1-L12 run 复核重读次数、stall 干预数与请求 token。
