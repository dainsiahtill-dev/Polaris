# Budget System-Plane Compression Blueprint (2026-06-15)

## 1. 背景与现象

弱模型 PM 规划（MiniMax-M3，budget=8000）在 L2-10 复跑中连续 3 次抛
`BudgetExceededError: assembled role context exceeds context enforcement budget`，
turn 在任何写入前崩溃，产物为 0。

实测预算诊断（`gateway.py:635` 的 `BudgetExceededError DIAGNOSTIC`）：

```
budget=8000 system_prompt_reserved=1241 total=8788 breakdown=[
  system 1241  role_definition (reserved system_prompt)
  system 1577  pm_planning_pipeline backend_kind
  system 5032  Current goal         ← 主凶
  system   33  项目结构
  system  144  仓库身份
  system   17  Context OS State
  system  159  Last event
  system  343  Run Card             ← ③ 已从 ~2570 压到 343
  user    242  规划指令
]
```

仅超预算 **788 tok**，却整轮崩溃。

## 2. 根因（两个叠加缺陷）

### 缺陷 A：压缩链是「对话历史专用」，从不碰 system 面板
- 非 state-first（PM 规划）走 `apply_compression → smart_content_truncation`，
  `compression_engine.py:197` 明确 `if role in {"system","user","tool"}: 保留不截`，
  **只截 `assistant` 消息**。
- PM 规划阶段 **没有 assistant 消息**，96% 预算压在单个 `Current goal` system 面板（5032）。
- `emergency_fallback`（`:248`）`system_msgs = [所有 system]` 全保留 →
  `:116-123` 「over limit 但原样返回」→ gateway `:619` 仍超 → raise。
- task #46 加的 `emergency_truncate`（docstring 引用 "live factory-bench L2-10"）只
  **丢历史 + 截最后一条 user**，那个 5032 system 面板碰都不碰；且它只在 state-first
  分支被调用，PM 规划这条路根本没用上。

### 缺陷 B：非 state-first 压缩目标用错预算口径
- gate（`:602`）用 `enforcement_budget_tokens = 8000 - reserved_system_prompt(1241) = 6759`。
- 但 `:609` 调 `apply_compression(messages, token_estimate)` 内部压到构造期固定的
  `self.max_context_tokens`（≈完整 8000），**没减去 system_prompt 预留**；压完在 `:615`
  再插 1241 system_prompt → 必然又溢出。state-first 分支（`:604`）则显式传
  `enforcement_budget_tokens`，口径正确。两条分支不一致。

## 3. 设计：compress-don't-crash 的「保证装下」最后一步

原则：enforcement 必须 **fit-or-degrade**，绝不因单个超大 system 面板而 raise。
压缩对话历史是常态路径；当历史压尽仍超、超量集中在非必需 system 面板时，
对该面板**内容**做 head+tail 截断（保底不清空），保证装下。

### 改动点

1. **`CompressionEngine.emergency_truncate`（`compression_engine.py:425`）扩展末段**：
   现有逻辑（丢历史 → 截最后一条 user 到 floor）跑完仍 > max_tokens 时，
   追加：对 system 消息按 token 体量降序，逐个对**最大的非首条** system 面板内容做
   head+tail 截断（保留 `_SYSTEM_PLANE_FLOOR_CHARS` 保底），直到装下或无可截。
   - 「首条 system」（role_definition）视为必需，不截。注意：此函数在 gateway 中
     于 system_prompt 插入（`:615`）**之前**调用，所以入参里 role_definition 尚不存在，
     被截的只会是 projection 面板（Current goal 等）——这正是我们要的。
   - 纯确定性，无 I/O，可单测。

2. **`gateway.py` 非 state-first 分支补「保证装下」**：在 `:608-610` 的
   `apply_compression` 之后、`:614` 插 system_prompt 之前，加一步：
   ```python
   if token_estimate > enforcement_budget_tokens:
       messages, token_estimate = self._compression_engine.emergency_truncate_with_limit(
           messages, enforcement_budget_tokens
       )
       compression_applied = True
   ```
   口径用 `enforcement_budget_tokens`（已减 system_prompt 预留），修复缺陷 B。
   state-first 分支已有等价调用，不动。

3. **保留** `:619` 的 raise 作为绝对兜底（仅当 system_prompt 自身就超 budget，
   已由 `:474` 提前处理；正常面板场景不再触达）。

## 4. 影响面与边界

- 改动文件：`compression_engine.py`（扩展一个已有方法）、`gateway.py`（加一步）。
- 不跨 Cell；不改契约；无新 effect。
- 行为变化：原本 raise 的超预算场景改为「截断 Current goal 尾部 ~900 tok 后正常规划」。
  L2-10 仅需削 ~788 tok（5032 面板的 ~16%），规划信息损失极小，远胜崩溃零产物。
- 风险：若未来某 system 面板被设计为「必须完整」，head+tail 截断会损其尾部。
  缓解：首条（role_definition）永不截；保底 floor 防清空；截断标记可观测。

## 5. 验证

- 单测（无长跑）：
  1. `emergency_truncate`：构造单个 5032-tok system 面板 + 小 user → 断言结果 ≤ max_tokens
     且 role_definition（首条）完整、Current goal 被 head+tail 截断。
  2. gateway 级：PM-planning 式 system-heavy、无 dialogue 的 messages →
     `_build_context_impl` 不抛 `BudgetExceededError`，token ≤ budget。
- 门禁：`ruff check --fix` + `ruff format` + `mypy` + `pytest`（相关测试文件）。
- 活体：下次正常批次顺带验证 L2-10 不再 budget-crash（不为此单独盲跑）。

## 6. 后续优化（不在本次范围）

- 源头智能裁剪 PM 规划 `Current goal`（按需求段落优先级裁，而非盲 head+tail），
  提升被截后规划质量。本次先保证「能跑」，质量优化留作 follow-up。
