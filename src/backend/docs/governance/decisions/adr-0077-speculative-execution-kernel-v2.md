---
status: accepted
date: 2026-04-17
---

# ADR-0077: Speculative Execution Kernel v2 — 从事务语义外推测到受控 Latency-Hiding Kernel

## 背景

`polaris/cells/roles/kernel/internal/stream_shadow_engine.py` 当前是一个骨架实现（skeleton）：

- `consume_delta()` 仅做缓冲区累积 + 关键词触发（`<tool_call>` / `` ```tool ``），confidence=0.1。
- `speculate_tool_call()` 仅检查 `ToolBatchRuntime.READONLY_TOOLS`，非只读直接拒绝。
- `turn_transaction_controller.py` 在流式阶段显式 `tool_call` 到达后才启动 `asyncio.create_task()` 做后台执行。
- `_drain_speculative_tasks()` 使用固定 0.2s timeout，未完成的直接 `task.cancel()`，没有 cancel-or-salvage 语义。
- `TurnLedger` 仅记录 `hit_rate` / `false_positive_rate`，缺少 `adopted` / `joined` / `replayed` / `abandoned` 等细粒度指标。

这套机制在"只读、可取消、低并发"边界内可控，但它存在 7 个结构性缺口：

1. **参数触发条件过粗**：以 JSON 闭合或关键词为触发，而非"参数稳定度"。
2. **工具边界仅看 readonly**：`web_search` 会消耗配额，`repo_rg` 会吃 CPU/IO，`db_select` 可能触发锁争用。
3. **缺少显式领养/接管机制**：没有 ADOPT / JOIN / CANCEL / REPLAY 状态机。
4. **缺少参数指纹（spec_key）**：无法安全判断"同名不同义"。
5. **取消做不干净**：timeout 后直接强杀，没有 cooperative cancellation 和 finally 清理。
6. **没有预算系统**：无 abandonment 熔断、无 per-tool semaphore、无队列压力感知。
7. **没有事务分层**：目前仅支持单 tool_call 投机，无法扩展为检索链推测、环境预热、写工具 prepare/validate 等更丰富的形态。

同时，主流模型接口已明确支持流式工具参数（OpenAI `ResponseFunctionCallArgumentsDelta`、Anthropic fine-grained tool streaming），说明"边收参数边做事"方向与业界一致。近年的 agent 加速研究也指出，端到端延迟的很大一部分来自工具执行而非模型本身，推测预执行有真实收益，但前提是必须有内核级的事务语义约束。

## 决策

### 1. 升级目标：从 Skeleton 到 Speculative Execution Kernel

将 `StreamShadowEngine` + `SpeculativeExecutor` 升级为 **Polaris TransactionKernel 内部的一个受事务语义约束的 latency-hiding kernel**。

核心不变量：
- **不变量 A**：关闭 speculation 后，系统 correctness 完全不变。
- **不变量 B**：Shadow 不等于 Commit。推测执行产生的是"候选结果"，不是正式业务提交。
- **不变量 C**：最终裁决权属于 TransactionKernel。ShadowEngine 只能猜，TransactionKernel 负责 ADOPT / JOIN / CANCEL / REPLAY。
- **不变量 D**：所有推测任务必须可追踪，不得存在不可见的后台幽灵任务。
- **不变量 E**：任意 speculative failure 必须能安全降级到普通同步工具执行路径。

### 2. 四维策略替代 READONLY_TOOLS

废弃简单的 `READONLY_TOOLS` 二分法，改用 `ToolSpecPolicy` 四维标签：

| 维度 | 可选值 | 含义 |
|------|--------|------|
| `side_effect` | `pure` / `readonly` / `externally_visible` / `mutating` | 副作用级别 |
| `cost` | `cheap` / `medium` / `expensive` | 成本级别 |
| `cancellability` | `cooperative` / `best_effort` / `non_cancelable` | 可取消级别 |
| `reusability` | `cacheable` / `adoptable` / `non_reusable` | 可复用级别 |

并附加字段：`speculate_mode`（forbid / prefetch_only / dry_run_only / speculative_allowed / high_confidence_only）、`min_stability_score`、`timeout_ms`、`max_parallel`、`cache_ttl_ms`。

### 3. 参数稳定度判定：四段状态机

引入 `ParseState`：

```
INCOMPLETE → SYNTACTIC_COMPLETE → SCHEMA_VALID → SEMANTICALLY_STABLE
```

**只有到 `SEMANTICALLY_STABLE` 才真正允许 speculative start**。

稳定度分数综合：
- schema 校验（25%）
- end tag 命中（15%）
- 关键字段静默度（35%）
- 覆写惩罚（15%）
- canonical hash 一致性（10%）

### 4. ShadowTaskRegistry 显式状态机

每个 speculative task 必须有明确状态和生命周期：

```
CREATED → ELIGIBLE → STARTING → RUNNING → COMPLETED → (ADOPTED | EXPIRED | ABANDONED)
```

异常路径：`RUNNING → FAILED`、`RUNNING → CANCEL_REQUESTED → CANCELLED`。

Registry 必须保证：
- 同一 `spec_key` 同时最多一个 active task。
- `adopt` / `join` / `cancel` 必须具备锁保护。
- 任务完成后必须过 TTL 自动过期。

### 5. 正式执行四动作

当 TransactionKernel 进入 authoritative execution 时，Resolver 只能做：

| 动作 | 条件 |
|------|------|
| **ADOPT** | shadow 已完成，spec_key 完全匹配 |
| **JOIN** | shadow 还在跑，spec_key 完全匹配 |
| **CANCEL** | 逻辑分支变化，旧 task 不再需要 |
| **REPLAY** | 无匹配、参数不一致、task 失败/已取消/过期 |

### 6. 结构化并发与 Cancel-or-Salvage

- 每个 turn 一个 `TaskGroup`（或等价结构化并发容器）。
- 每个工具 runner 接受 `cancel_token` 与 `deadline`。
- 所有资源释放放进 `finally`。
- 取消时不是一刀切强杀，而是三选一：
  1. **立刻取消**（高成本、刚开始、不可能复用）
  2. **允许完成并转入短 TTL cache**（快结束、结果可复用）
  3. **正式流程接管（JOIN）**

### 7. 预算治理与熔断

每次 speculative start 前做预算准入：
- 当前 speculative 并发数
- 最近 abandonment ratio
- 外部 API quota 剩余
- CPU / memory / queue pressure

熔断条件：
- abandonment ratio > 60% → 降级到仅 S0/S1
- speculative timeout ratio > 20% → 降级
- wrong-adoption > 0 → 暂停 speculation

运行模式：`Turbo` / `Balanced` / `Safe`。

### 8. 工具分层（S0~S4）

- **S0**：环境预热（repo index warmup、连接预建立）—— 最稳，优先做
- **S1**：低成本只读（stat_file、list_dir、cache_lookup）—— 高置信度可推
- **S2**：高成本只读（repo_rg、db_select、web_search）—— 高稳定度 + 预算允许才推
- **S3**：写操作前置只读阶段（prepare / validate）—— 只能 speculative 到前两阶段
- **S4**：真正有副作用（write、delete、send、submit）—— 禁止正式 speculative execute

### 9. 扩展形态（后续 Phase）

在基础 correctness 骨架之上，逐步支持：
- 检索链推测（repo_rg → 预读 top-k 文件头部）
- Web research 推测（search 后 prefetch 前 2~3 个 URL）
- 写工具三阶段化（Prepare → Validate → Commit）
- Computer Use 限定支持（snapshot prefetch，不点击 destructive button）
- 有限分支推测（最多 2-way fork）

## 实现状态

### Phase 1 已完成（2026-04-17）

以下模块已落地并通过全部回归测试（ polaris/cells/roles/kernel/tests/ 1016 passed, 1 skipped）：

- `polaris/cells/roles/kernel/internal/speculation/models.py`
  - `ToolSpecPolicy` 四维策略骨架（side_effect / cost / cancellability / reusability）
  - `ShadowTaskState` 显式状态机（CREATED → STARTING → RUNNING → COMPLETED → ADOPTED / CANCELLED / ABANDONED / EXPIRED）
  - `ShadowTaskRecord`、`CancelToken`、`ShadowExecutionError`
- `polaris/cells/roles/kernel/internal/speculation/fingerprints.py`
  - `normalize_args`（递归排序 + 字符串归一化）
  - `build_spec_key`（SHA-256 of tool_name + normalized_args + env_fingerprint）
  - `build_env_fingerprint`（git HEAD 回退 mtime）
- `polaris/cells/roles/kernel/internal/speculation/registry.py`
  - `ShadowTaskRegistry`：asyncio.Lock 保护、spec_key 去重、adopt / join / cancel / drain_turn
  - `EphemeralSpecCache`：短 TTL 结果缓存
- `polaris/cells/roles/kernel/internal/speculation/resolver.py`
  - `SpeculationResolver.resolve_or_execute()` 实现 ADOPT / JOIN / REPLAY 三动作（CANCEL 由 Registry 直接提供）
- `polaris/cells/roles/kernel/internal/speculation/metrics.py` & `events.py`
  - 统一推测执行事件日志（speculation.shadow.started / .completed / .cancelled / .abandoned / .resolve.adopt / .resolve.join / .resolve.replay）
- `polaris/cells/roles/kernel/internal/stream_shadow_engine.py`
  - 升级为 Facade：保留旧接口兼容，注入 Registry + Resolver
  - `speculate_tool_call()` 在 Phase 1 改为向 Registry 注册 shadow task 并启动后台执行
- `polaris/cells/roles/kernel/internal/speculative_executor.py`
  - 新增 `execute_speculative()` 支持 `timeout_ms` + `cancel_token` 透传
- `polaris/cells/roles/kernel/internal/tool_batch_runtime.py`
  - `ToolExecutionContext` 扩展 `cancel_token`、`deadline_monotonic`、`speculative`、`spec_key`
  - `_execute_single()` 在 runner 中检查 cancel_token 与 deadline
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
  - `_build_stream_shadow_engine()` 构建并注入 Registry + Resolver
  - `_execute_tool_batch()` 在正式执行前对每个 invocation 调用 `resolve_or_execute()`，命中则合成 receipt、未命中则走 authoritative batch
  - `_drain_speculative_tasks()` 扩展为同时调用 `registry.drain_turn()` 清理残留任务

### 新增测试

- `test_speculation_fingerprints.py`（9 tests）
- `test_speculation_registry.py`（10 tests）
- `test_speculation_resolver.py`（12 tests）
- `test_speculation_cancellation.py`（6 tests）
- `test_speculation_salvage.py`（9 tests）
- `test_speculation_task_group.py`（5 tests）
- `test_speculation_budget.py`（13 tests）
- `test_speculation_param_stability.py`（9 tests）
- `test_speculation_integration.py`（8 tests）

### Phase 5 已完成（2026-04-17）

- `ChainSpeculator`：监听 Registry 完成事件，自动触发下游推测（`repo_rg` → `read_file`，`web_search` → `fetch_url`）
- `ResultExtractor`：启发式提取文件路径和 URL，支持域名白名单过滤
- `ShadowTaskRegistry._chain_index` 与级联取消：`cancel()` / `abandon_turn()` 自动清理下游 shadow task
- `WriteToolPhases`：写工具 `prepare` / `validate` / `commit` 三阶段语义，`prepare` 可 speculative，`commit` 必须走 authoritative
- `SpeculationResolver`：扩展 `resolve_or_execute()` 支持 write tool 的 prepare shadow 结果复用
- `StreamShadowEngine.speculate_tool_call()`：写工具触发 prepare shadow 而非直接拒绝
- `TurnTransactionController._execute_tool_batch()`：写工具批次在 prepare adopt/join 后仍强制走 commit 的 authoritative 路径

### 新增 Phase 5 测试

- `test_speculation_chain.py`（13 tests）
- `test_speculation_web_prefetch.py`（8 tests）
- `test_speculation_write_phases.py`（6 tests）
- `test_speculation_integration.py` 扩展 3 个集成测试

### Phase 2 已完成（2026-04-17）

- `SalvageGovernor`：三决策策略（CANCEL_NOW / LET_FINISH_AND_CACHE / JOIN_AUTHORITATIVE）
- `TurnScopedTaskGroup`：turn 级结构化并发容器，支持 salvage 模式下的部分任务保留
- `CancellationCoordinator`：`refuse_turn` 与 `cancel_turn` 批量清理，消除 ghost tasks
- `ToolBatchRuntime._execute_single()`：执行前后增加 `check_cancel()` 埋点
- `TurnTransactionController`： refusal abort 检测、`_drain_speculative_tasks()` 与 TaskGroup 生命周期对齐

### Phase 3 已完成（2026-04-17）

- `BudgetGovernor`：基于 S0~S3 分层的预算准入控制
- `ShadowTaskRegistry.start_shadow_task()`：集成预算快照评估，超阈值时拒绝高成本推测
- 运行模式支持 `turbo` / `balanced` / `safe`，具备 abandonment ratio > 60% 降级和 wrong-adoption 熔断

### Phase 4 已完成（2026-04-17）

- `CandidateDecoder`：增量解析流式 delta，提取 `tool_name` 与 `partial_args`
- `StabilityScorer`：基于 schema 校验、end tag、关键字段静默度、覆写惩罚的综合稳定度评分
- `StreamShadowEngine.consume_delta()`：注入 CandidateDecoder + StabilityScorer，支持 `ParseState` 语义稳定触发

### 遗留到后续 Phase

- 无。Phase 1~5 已全部完成并回归通过。

## 影响

### 需要修改的文件

1. `polaris/cells/roles/kernel/internal/stream_shadow_engine.py` — 升级为 facade，保留兼容接口
2. `polaris/cells/roles/kernel/internal/speculative_executor.py` — 降级为纯执行器适配层
3. `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` — 流式阶段和 authoritative 阶段接入 Resolver
4. `polaris/cells/roles/kernel/internal/tool_batch_runtime.py` — `ToolExecutionContext` 扩展 + cancel_token 透传
5. `polaris/cells/roles/kernel/internal/` — 新增 `speculation/` 子目录（可选，但推荐）

### 新增模块（推荐目录结构）

```
polaris/cells/roles/kernel/internal/
├── speculation/
│   ├── __init__.py
│   ├── candidate_models.py
│   ├── candidate_decoder.py
│   ├── stability_scorer.py
│   ├── tool_policy.py
│   ├── shadow_registry.py
│   ├── resolver.py
│   ├── budget.py
│   ├── cancel.py
│   ├── salvage.py
│   ├── fingerprints.py
│   ├── events.py
│   └── metrics.py
```

### 对外接口变更

- `StreamShadowEngine` 保留 `consume_delta()` 和 `speculate_tool_call()` 签名（向后兼容）。
- 新增 `resolve_or_execute()` 作为 authoritative 路径入口。
- `ToolBatchRuntime.execute_batch()` 通过 `ToolExecutionContext` 扩展字段，不破坏现有调用。

### 测试要求

- 保留现有 `test_speculative_execution.py` 8 个测试作为 regression baseline。
- 新增 `test_speculation_registry.py`、`test_speculation_resolver.py`、`test_speculation_budget.py`、`test_speculation_cancellation.py`、`test_speculation_param_stability.py`、`test_speculation_integration.py`。

### 监控与可观测性

- 统一事件日志 schema（按 `speculation.{domain}.{action}` 命名）。
- `TurnLedger` 扩展细粒度计数器：`started`、`completed`、`adopted`、`joined`、`replayed`、`cancelled`、`abandoned`、`wrong_adoption`、`saved_ms`。

## 相关文档

- `docs/blueprints/STREAM_SHADOW_ENGINE_RELIABILITY_ASSESSMENT_20260417.md`
- `src/backend/docs/邪术层详细设计稿.md`
- `docs/governance/templates/verification-cards/vc-20260417-speculative-execution-kernel-v2.yaml`
