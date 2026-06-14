# Blueprint — Deterministic-Bootstrap Clobber + Retry-Path Empty-Output + Verify-Quality Gate (2026-06-14)

权威背景: 弱模型实战闭环 (PM/CE/QA=MiniMax-M3 云, Director=本地 qwen3.6-27b-int4@16k)。
目标: 产物【真实可运行】, 不只是 step resolved。本蓝图修复 r21 暴露的"resolved-but-not-runnable"根因链。

## 0) 证据与诊断 (r21, L2-12 brick-breaker, concurrency=1)

5-专家诊断面板 (run wf_8ba48cde-a03) 收敛出 **一条因果链, 而非四个独立 bug**:

- **(A)** deterministic bootstrap 写回退把【错误文件】(main.js)写成 8 行占位桩 `polaris-deterministic-bootstrap`,
  因为它从多文件裂变 prompt 里 regex 抓取【所有】文件名, 写 `viable_targets[0]`, 完全不知道当前执行
  step 的【单一声明 target_file】。执行 PM-0001-1-S4 (target=readme.md) 时, 模型发出违约写, 回退触发,
  写了 main.js (log L42)。这在其【合法属主 step PM-0001-1-S3】运行前就凭空创建了 main.js。
- **(B)** file_ownership_ledger (FIX-1) 正确地把 main.js 归属给 S3 并串行化跨父写者 — 但文件已是占位桩,
  跨父契约于是告诉 S3 "main.js 已存在, read+EDIT 它"; 模型读到 8 行桩 → 困惑 → 3/3 次
  `director_no_materialized_changes` (~1470s 卡死, 2 次 requeue)。**ledger 工作正常, 被上游桩拖死。**
- **(C)** 即便解锁, S3 的 retry/re-ask 路径无法恢复: `retry_tool_batch_after_contract_violation →
  _call_llm_for_decision → core.py:701` 在 reasoning 截断 (finish_reason=length, reasoning_chars=786)
  时【硬抛】RuntimeError, 且重试调用没有 reasoning-sized 输出预算。主 turn 路径 (F5/F7/F4) 已修, 重试子路径未覆盖。
- **(D)** 空洞 verify (纯 `test -f`) 让占位桩冒充的 sibling 也能 pass。

占位桩不会假绿真实 verify (node --check PASS 但 `grep -q class Paddle` FAIL) → 是【纯毒化】, 非 verify-bypass。

## 1) 修复 (ranks 1-4, 全部通用, must-precede-r22)

### Rank 1 — bootstrap 回退按【声明 target】定标 (retry_orchestrator.py)
- 当前 turn 上下文已携带 `context_override["construction_step"]` (director_consumer:802), 内含单一 `target_file`。
  retry orchestrator 已接收完整 `original_context` 消息列表 → 直接抽取, **零跨层 plumbing**。
- 非叶子/repo-fix 上下文 (无 construction_step): 保留"用户命名 + 不存在"守卫, **但当 >1 个候选时 return None
  拒绝瞎猜** (不再盲取 viable_targets[0])。

### Rank 2 — 叶子施工步禁用【写】回退 (retry_orchestrator.py)
- 当 `original_context` 携带 construction_step 卡 (= 叶子施工步) → `build_deterministic_bootstrap_followup_write_decision`
  直接 return None。只保留 READ bootstrap 路径 (证据收集), 模型必须自己发出真实写, 否则诚实失败。
- 理由: 占位桩永远满足不了叶子步的真实 verify, 只会毒化属主 → 诚实 `no_materialized_changes` (fail-closed)
  严格优于毒化产物。

### Rank 3 / F10 — retry 路径预留 reasoning-sized 输出 floor (r22 实锤后落地)
**r22 实证机理 (5-专家面板 wf_b49bd94d-dfe):** 占位桩根因修复后 (ranks 1+2 生效), main.js 步唯一阻断 =
retry/bootstrap-followup 调用注入最多 16000 字符文件内容 (`_BOOTSTRAP_READ_CONTENT_TOTAL_CHARS`), 填满
16384 窗口 → `clamp_output_tokens_to_window` 把生成预算压到 256-token floor → 推理模型推到一半耗尽
(finish_reason=length, reasoning_chars=633) → 无可见写 → no_materialized_changes → 死循环。本地 qwen 没有
MiniMax 那样的 empty-output self-heal (那个升预算 8192→16384 只对 MiniMax 生效)。

**F10 落地 (prong B, retry-path-local, 不扰动主 turn 预算):** 通过 temperature_override 同款 context_override 通道
threads 一个 reasoning-sized 输出 floor:
- `resolve_retry_output_floor()` (env `KERNELONE_RETRY_OUTPUT_FLOOR_TOKENS`, 默认 2500, off/0 禁用)。
- retry_orchestrator 两处 retry 调用 (`_execute_retry_batch` llm_call_kwargs + bootstrap-followup) 注入 `max_tokens_floor`。
- turn_transaction_controller `_call_llm_for_decision[_stream]` + stream_orchestrator impl: 收 `max_tokens_floor` → request_payload。
- core `_build_context_override_with_prebuilt_messages`: → `override["llm_max_tokens"]` → `resolve_max_tokens` 返回它
  → executor `TokenBudgetManager.enforce(requested_output_tokens=floor)` 【预留 floor + 压缩输入(含 chat_messages)】
  → `clamp_output_tokens_to_window` 看到压缩后的 prompt → 保住 floor 输出。floor=2500 强制 prompt 压到 ~11.8k, 远低于 ~16.1k retry prompt → 压缩必触发。
- 尊重 qwen 16384 硬上限: 压输入而非升窗口。

**Prong A / F12 (r23 实锤后落地为主修复):** r23 证明 F10 floor 在重试路径上【无效】(reasoning_chars 仍 468-690,
与 r22 633 基本不变) —— 因为 bloat 在 chat_messages(输入), reserved-output floor 不压缩输入。根本修复 = 从源头
避免那次重试: from-scratch 叶子步第一轮直接强制 `write_file` tool_choice, 跳过浪费的 read(接口符号已全在 contract 里),
于是 read-first→violation→bootstrap-retry→截断 级联根本不发生。
- `turn_transaction_controller._resolve_from_scratch_write_tool_choice`: 仅当 must-materialize + 有 construction_step
  + 非 edit_on_prior + 目标文件不存在 + write_file 可用 → 返回强制 write tool_choice; 注入第一轮 decision call。
- edit_on_prior / 已存在文件 → 保持 read-first (改建式 Fix-13 需先读)。env `KERNELONE_FIRST_TURN_WRITE` 可关。
- F10 floor 保留作 edit-on-prior 重试的安全网(无害), 但 main.js(from-scratch)由 Prong A 直接解决。

**r23 量化 (F10 only):** step 5/7 resolved (0.71, 从 0/7 跃升), 可运行率 5/7, product_coherent=False,
main.js dead_letter (empty-output×6)。r24 = Prong A 验证。

### Rank 4 — CE verify 质量门 (step_contract / step_verify)
- CE 裂变发布时, 对【代码扩展类 target_file】拒绝"全空洞"verify (所有子句都是 test -f / marker-grep, 无语法/行为/签名判据)。
- 纯结构规则, 针对 CE 自己声明的字符串; 不解析 HTML/JS/游戏语义; 文档/样式 target 豁免; verify 不可解析时 fail-OPEN。

## 2) 推迟到 r22 后 (ranks 5-7, precede_r22=False)
- Rank 5: 删除 synthesizer 里写死的业务模板 (WorkspaceArtifactStatus / DagService) — CLAUDE.md §8。rank 2 落地后这些分支对叶子步变死代码。
- Rank 6: QA 跨文件引用解析 + 声明接口在场门 (defense-in-depth, 新增 QA requeue 路径会扰动预算)。
- Rank 7: ledger owner-terminal-DLQ re-homing + 内容活性归属门。

## 3) 验证
- 单测: bootstrap 回退 (叶子→None / 非叶子>1 候选→None / 单候选保持) ; retry 截断存活 ; CE verify 质量门。
- 触面套件 + polaris/tests/unit 绿; ruff/mypy 静默。
- 实证: r22 (fresh L2-12, concurrency=1) — main.js 不再是桩, S3 resolved with real game, product_coherent 翻 True。
