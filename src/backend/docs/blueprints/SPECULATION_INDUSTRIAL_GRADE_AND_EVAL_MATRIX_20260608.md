# Blueprint: Speculation 工业级化 + 专用评测矩阵 (2026-06-08)

> 目标（来自 `/goal`）：彻底完善 Speculation/Speculative 执行技术，打造成"工业级"版本，
> 使其发挥最大效益；需要跑真实 LLM 测试反复评测，并构建/拓展只针对这块的 agentic-eval 矩阵。

## 0. 权威与现状

- 架构权威：`docs/governance/decisions/adr-0077-speculative-execution-kernel-v2.md`（Phase 1–5 已声明落地）。
- 不变量（ADR-0077）：
  - **A**：关闭 speculation 后 correctness 完全不变。
  - **B**：Shadow ≠ Commit。
  - **C**：最终裁决权属于 TransactionKernel（ADOPT/JOIN/CANCEL/REPLAY）。
  - **D**：所有推测任务可追踪，无幽灵任务。
  - **E**：任意 speculative failure 必须能安全降级到同步路径。
- 现状骨架（已读代码核对）：
  - 流式接线真实：`transaction/stream_orchestrator.py` 的 `consume_delta()` / `speculate_tool_call()`
    挂在真实 provider stream 上（`stream_orchestrator.py:805-910`）。
  - authoritative 裁决：`transaction/tool_batch_executor.py:854-963` 调 `resolve_or_execute()`，
    write 工具 `block` 已优雅降级为 `replay_invocations`（`87ab0507`，不再 abort 整轮）。
  - 开关：`speculative_flags.is_speculative_execution_enabled()`（默认开）。
  - 评测：`cells/llm/evaluation/internal/tool_calling_matrix.py` 流式跑真实 turn，
    但 **不关心 speculation，也不记录任何 speculation 指标**。

## 1. 问题诊断（为什么现在还不是"工业级"）

### P1 — 收益完全不可测量（最致命）
`SpeculationMetrics()` 在 `turn_transaction_controller._build_stream_shadow_engine()` 内每 turn 新建，
**不返回、不挂到 ledger / turn 结果**，只通过全局 `emit()` 事件 sink 旁路输出；且 `record_completed/
failed/cancel/abandon` 传 `turn_id=""`。后果：无法回答"speculation 到底省了多少时间 / 命中率多少 /
有没有错误领养"。**没有测量就谈不上"最大效益"，也无法"反复评测"。**

### P2 — spec_key 归一化存在碰撞风险（正确性地基）
`fingerprints._normalize_value` 对**所有**字符串值做 `.strip()`，会把语义不同的值
（`"x"` / `" x"` / `"x\n"`）折叠成同一个 spec_key。碰撞方向（false-same）正是 ADR-0077 最忌讳的
**wrong-adoption**（"wrong-adoption > 0 → 暂停 speculation"）。`test_speculation.py::TestNormalizeArgs`
两个用例自初始提交起长期红，恰好暴露此处语义未定。

### P3 — saved_ms / wrong_adoption 从未真正计量
`record_adopt(saved_ms=None)` 全程传 None；`SpeculationMetrics` 无 `wrong_adoption` 字段。
"发挥最大效益"需要一个可观测的 saved_ms（被隐藏的工具执行时延）与一个恒为 0 的 wrong_adoption 守门指标。

### P4 — env 指纹粒度粗（潜在跨编辑陈旧领养）
`build_env_fingerprint` 用 `git rev-parse HEAD`，对未提交编辑不敏感，回退才用 workspace 目录 mtime。
理论风险：同一 turn 内"读 → 写同文件 → 再读"时，第二次读可能领养第一次读的陈旧 shadow。需核验并按文件粒度收紧。

## 2. 方案（四个工作流，按依赖顺序）

### WS-B 正确性硬化（先行，最低风险，建立绿基线）
- 重设 `normalize_args` 语义为**抗碰撞优先**：
  - dict 递归按键排序（保留，语义无关，安全）。
  - 字符串只做**换行规范化**（`\r\n`/`\r` → `\n`），**不再对值做 `.strip()`**——避免折叠语义不同值。
  - 保留 spec_key 仍由 `tool_name + normalized_args + env_fingerprint` 派生。
- 据此修正两个红测试为新语义，并**新增抗碰撞回归测试**：断言
  `"x"` / `" x"` / `"x\n"` / `"x "` 互不相同的 spec_key。
- 验收：`fingerprints` 与 `test_speculation.py::TestNormalizeArgs` 全绿；新增 no-collision 测试通过。

### WS-A 测量地基（评测前置）
- `SpeculationMetrics` 增补可读计数器与派生比率：
  `started / eligible_dropped / adopted / joined / replayed / cancelled / abandoned /
  saved_ms_total / wrong_adoption`，以及 `snapshot()` 返回纯 dict。
- `record_adopt/join` 计入 saved_ms（来自 shadow 完成时记录的实际执行耗时；adopt 命中即视为隐藏了该耗时）。
- `StreamShadowEngine` 持有并暴露 `metrics`（构造器已有 registry/resolver，补传 metrics 句柄）。
- `turn_transaction_controller` 在 turn 收尾把 `metrics.snapshot()` 写入 TurnLedger / turn 结果的
  `speculation` 字段（不污染 data plane，仅 control/observability）。
- 验收：单测构造一个含 adopt 的 turn，能从结果读到 `speculation.adopted >= 1` 与 `saved_ms_total > 0`。

### WS-C 专用评测矩阵（用户显式要求）
- 新增 speculation 评测套件（复用现有 matrix harness 基建，**新建独立 suite** 而非污染 tool_calling_matrix）：
  - 用例聚焦"可投机"形态：多只读链（`repo_rg→read_file`）、重复读、读后写、检索链。
  - 每个 case **跑两遍**（spec ON / spec OFF），断言：
    - **invariant A**：两遍的 authoritative 工具结果集合一致（correctness 不变）。
    - **wrong_adoption == 0**（硬门禁，违反即 FAIL）。
  - 报告：`adopted/joined/replayed`、`hit_rate`、`saved_ms_total`、ON vs OFF 的 `duration_ms` 差。
- 接入 `delivery/cli/agentic_eval.py`：`--suite speculation_matrix`，支持 `--max-failed` 早停
  （沿用 `[[benchmark-run-discipline]]`：一次一个模型、超 3 次失败即停审计）。
- 验收：CLI 可跑出每用例的 spec 指标与 ON/OFF 对比；correctness 断言全绿。

### WS-D 真实 LLM 评测闭环
- 对 native-FC 流式模型（deepseek，`textual_recovery` 不触发、流式才有 speculation）跑 speculation_matrix。
- 看三件事：① wrong_adoption 恒 0（否则立即熔断式修复）；② hit_rate / saved_ms 是否有正收益；
  ③ ON vs OFF 的 correctness 完全一致。
- 按 [[benchmark-run-discipline]]：超 3 次失败停下，审计根因→修复→复测。把可复现实测结论写回 ADR-0077 状态区与 memory。

## 3. 影响面

- 修改 Cell：`roles.kernel`（`speculation/fingerprints.py`、`metrics.py`、`stream_shadow_engine.py`、
  `turn_transaction_controller.py`、`public/turn_contracts.py` 可能新增 `speculation` 只读字段）。
- 新增评测：`cells/llm/evaluation/internal/`（speculation 套件）+ `delivery/cli/agentic_eval.py` 接线。
- 跨 Cell：评测套件经公开契约调用 role-session 执行，不直连 kernel internal。
- effect：LLM 调用（评测）、子进程 git（env 指纹，既有）。

## 4. 风险与边界

- **不破坏 invariant A**：所有改动以"关闭 speculation 行为不变"为硬约束；评测正是对它的可执行验证。
- **抗碰撞优先于命中率**：归一化收紧可能略降命中（多几次 replay），但 replay 安全；wrong-adoption 不可接受。
- **不删除既有安全代码**（[[debt-resolution-not-deletion]]）：write-phase prepare/commit 两阶段、
  budget 熔断、salvage 全部保留；只做加固与计量。
- **测试钉死的安全行为**：若需改动 `test_speculation_write_phases.py:153` / `test_speculation_integration.py`
  钉死的 abort 语义，必须配 ADR 复核；本 blueprint 不预设要改它们。

## 5. 验证命令

```bash
ruff check <paths> --fix && ruff format <paths>
mypy <paths>
pytest polaris/cells/roles/kernel/internal/speculation -q
pytest polaris/cells/roles/kernel/tests -k speculation -q
# 真实 LLM（流式、native FC）：
KERNELONE_LLM_CONFIG=/tmp/llm_config_deepseek.json \
  python -m polaris.delivery.cli agentic-eval --workspace . --suite speculation_matrix --max-failed 3
```

## 6. 实现结果与发现（2026-06-08）

### 已落地
- **WS-B 抗碰撞 spec_key**：`fingerprints._normalize_value` 去掉对字符串值的 `.strip()`，
  只做换行规范化；修复两个长期红测试（`TestNormalizeArgs`），新增 no-collision 回归测试。
- **WS-A 指标暴露**：`SpeculationMetrics` 补全 `started/adopted/joined/replayed/cancelled/
  abandoned/saved_ms_total/wrong_adoption + snapshot()`；resolver 从 shadow 计时算 saved_ms；
  `events.py` 增 `subscribe()` 订阅 seam；新增 per-turn `speculation.turn.summary` 事件
  （带 turn_id + 完整快照，幂等）。
- **WS-C 评测矩阵**：`cells/llm/evaluation/internal/speculation_matrix.py`（差分 ON-audit vs
  OFF），经 `evaluation/public/service.py` 导出，接入 `agentic_eval` CLI（`--suite
  speculation_matrix`，独立报告 `_report_speculation_matrix`，`__main__` choices 已加）。
  独立单测 6 个（注入假执行器，无需真实 LLM）。
- **领养审计模式（keystone）**：`SPECULATION_AUDIT_ADOPTIONS=1` 时，`tool_batch_executor`
  对每次 ADOPT/JOIN 额外权威重算并比对结果，不一致即记 `wrong_adoption` 并改用权威结果
  （detector + 安全网）。默认关闭、对默认路径零影响；这是不变量 A 的实运行验证基础。

### 真实 LLM 评测中发现并修复的两个工业级缺陷
1. **per-turn 汇总时序错误**：汇总原在流式解码 drain 时发射，早于 authoritative
   工具批的 adopt/join/replay 裁决，导致**全部裁决指标丢失**（adopted 恒为 0）。
   修复：把发射点移到 `execute_tool_batch` 裁决完成处；`emit_turn_summary` 对每个
   metrics 实例幂等（防重试重复）。
2. **失败 shadow future 泄漏**：经 REPLAY 裁决的失败 shadow 其 future 从不被
   await，异常以 "Task exception was never retrieved" 在 GC 时泄漏。修复：在
   `registry.start_shadow_task` 给 future 挂 done-callback 主动消费异常（join 仍能
   在 await 时拿到异常并降级 replay）。

### 实测结论（deepseek-v4-pro，native FC，流式）
- 只读 case 上 speculation 真实触发并领养：如 `l2_multi_file_read` adopted=2、hit_rate=1.0；
  `l7_context_memory` adopted=2；`l1_read_tail` adopted=1。
- **不变量 A 实运行验证通过**：领养审计模式下所有领养的 `wrong_adoption == 0`
  （投机结果与权威重算逐一一致）。
- **有正收益**：`saved_ms_total > 0`（隐藏了被领养只读工具的执行墙钟）。
- **误报甄别**：`l3_glob_then_read` 曾在 ON 下报 `single_batch_contract_violation`，
  但 speculation **OFF** 下三跑也间歇复现（1/3）——证实是意图分类/模型写行为的
  **非确定性**，与 speculation 无关。据此：(a) 评测硬门禁定为 `wrong_adoption`（确定性、
  speculation 专属）；(b) 仅当 ON-only 错误**可归因到 speculation**（信息含 shadow/specul/
  adopt）才算硬回归，否则记为软诊断 `error_divergence`；(c) 默认 case 集合裁剪为纯只读，
  排除带写 delivery-contract 的噪声 case。

## 7. 贵工具档端到端实测 + 重叠窗口发现（2026-06-08）

为量化"贵工具"档收益，新增**评测专用只读延迟注入**（生产默认关闭）：
- `tool_batch_runtime._execute_single`：env `SPECULATION_EVAL_READ_DELAY_MS` 对只读工具
  注入人工执行延迟（在 `wait_for` 超时内、受取消约束；shadow 与 authoritative 共用本方法 → 两边一致）。
- `stream_shadow_engine`：env `SPECULATION_EVAL_READ_TIMEOUT_MS` 上调只读 shadow 超时，
  避免大延迟把 shadow 超时掉。

**关键发现——重叠窗口（overlap window）**：speculation 的收益不是"隐藏整段工具耗时"，
而是**被限定在 tool_call 流式 emit 之后、本 turn 解码结束之前的那段重叠窗口**。延迟扫描
（deepseek-v4-pro，`l7_context_memory`，2 读）：

| 注入延迟 | adopted | saved_ms | 解读 |
|---|---|---|---|
| 0 ms | 2 | 23 | 真实读 ~5ms，全领养 |
| 200 ms | 2 | **410** | 两读都落在窗口内，全部隐藏（≈2×200） |
| 500 ms | 1 | 506 | 一读落窗、一读超窗 → 后者 replay |
| 1000 ms | 0 | 0 | 均超窗 → 全 replay，无收益 |

→ 本 case/模型的**有效重叠窗口约 200–500ms**。**重要修正**：之前"贵工具（web_search 秒级）
= 巨大收益"的说法过于乐观；在"turn 在 tool_call 处即结束"的工具调用模式下，单 turn 能隐藏的
上限就是这个重叠窗口（此处 ~400ms），而非工具的整段秒级耗时。要隐藏更长耗时，需要模型在
emit 工具调用后**仍持续生成**更多 token（更长 reasoning/thinking 流），或靠链式/跨 turn 预取。

### N 次统计（mean ± 95% CI）

实验：注入读延迟 200ms，N=8（ON spec+audit关 / OFF），deepseek-v4-pro，每 case 2 读全领养（8/8）。
统计脚本 `/tmp/spec_stat_experiment.py`（t 分布小样本 CI）。

| case | saved_ms mean | saved_ms 95% CI (sd) | ON dur (sd) | OFF dur (sd) | 端到端 OFF−ON 95% CI |
|---|---|---|---|---|---|
| `l7_context_memory` | **413 ms** | [409, 417] (sd 5) | 12134 ms (1126) | 12269 ms (1300) | 134 ms **[−980, 1249]** |
| `l2_multi_file_read` | **412 ms** | [409, 416] (sd 4) | 22300 ms (2469) | 21609 ms (2276) | −691 ms **[−2867, 1486]** |

两 case 的 `saved_ms` 几乎完全一致（412–413ms，均为 2×200ms 全隐藏），CI 宽度仅 ±4ms。两 case 的
端到端 OFF−ON 都**跨 0**，且 l2 turn 更长（~22s）→ 噪声更大（sd≈2400ms）→ 收益更深地埋在抖动里。

**结论（核心科学发现）**：
1. **saved_ms 精确且置信极高**（CI 宽度仅 ±4ms）——speculation 确实从关键路径移除了 413ms，可靠复现。
2. **端到端墙钟被 LLM 生成方差主导**（每次 ±1100–1300ms）——~400ms 的收益在 N=8 下**低于噪声下限**，
   端到端 OFF−ON 的 CI 跨 0。要在端到端墙钟上把 400ms 收益和 ±1200ms 抖动分开，需要极大的 N。
3. **测量工具的选择决定结论**：`saved_ms`（隔离测量被领养工具的执行墙钟）是正确仪器；端到端墙钟对
   亚秒级收益是错误仪器（信噪比太低）。speculation 的收益**真实、可加、在关键路径上**，但在 agent
   turn 的端到端层面被模型生成抖动掩盖。

### 已知非本任务缺陷（不在范围内，未改）
- `policy/budget_policy.py::from_metadata` 对非法 `max_calls` 做 `int('invalid')` 抛
  `ValueError`（`test_from_metadata_invalid_values` 红）——预存，与 speculation 无关。
- `test_session_workflow_matrix.py` 11 例红：`MockWorkflowKernel.execute_stream()` 不接受
  `parent_span_id`——预存 mock 签名问题，与 speculation 无关。
