# StreamShadowEngine 可靠性评估与演进蓝图

**日期**: 2026-04-17  
**范围**: `polaris/cells/roles/kernel/internal/stream_shadow_engine.py` 及其相关链路  
**评估结论**: 在"只读、可取消、可去重、可回退"边界内可靠，方向正确；但尚未成为通用推测执行内核，需补一层调度与事务语义。

---

## 1. 执行摘要

| 维度 | 判断 |
|------|------|
| 架构方向 | 与 OpenAI Responses 流式工具事件、Anthropic fine-grained tool streaming 同向，有价值 |
| 当前实现 | 骨架级（skeleton），仅有 `SpeculativeExecutor` + `StreamShadowEngine` + `TurnLedger` 监控 |
| 可靠性边界 | 仅在 `READONLY_TOOLS` 内、单工具、单 turn、低并发场景下可控 |
| 核心缺口 | 缺少参数稳定度判定、工具策略分级、影子任务状态机、Adopt/Join/Cancel/Replay 语义、预算治理、结构化取消 |
| 下一步 | 升级为 **Speculative Execution Kernel** 子系统，纳入 TransactionKernel 统一事务语义 |

---

## 2. 当前实现快照

**相关文件**:
- `polaris/cells/roles/kernel/internal/stream_shadow_engine.py`
- `polaris/cells/roles/kernel/internal/speculative_executor.py`
- `polaris/cells/roles/kernel/internal/speculative_flags.py`
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
- `polaris/cells/roles/kernel/tests/test_speculative_execution.py`

**当前行为**:
1. `ENABLE_SPECULATIVE_EXECUTION` feature flag 控制，默认关闭。
2. `StreamShadowEngine.consume_delta(delta)` 缓存流式 delta，但参数解析仅停留在关键词触发（`<tool_call>` / `` ```tool ``），confidence=0.1。
3. 真正的推测执行发生在 `turn_transaction_controller.execute_stream()` 中：当流事件明确输出 `tool_call` 时，才调用 `shadow_engine.speculate_tool_call()`。
4. `speculate_tool_call()` 检查 `tool_name` 是否在 `ToolBatchRuntime.READONLY_TOOLS` 中，非只读直接拒绝。
5. 推测任务以 `asyncio.create_task()` 启动，在 `finally` 中由 `_drain_speculative_tasks()` 收集或取消（timeout=0.2s）。
6. `TurnLedger` 统计 `speculative.hit_rate` 与 `speculative.false_positive_rate`。

**现状问题**:
- `StreamShadowEngine` 目前**没有真正从流式 delta 中边收边解析参数**，只是缓冲区 + 关键词触发。
- 推测执行等于正式执行（通过 `ToolBatchRuntime` 的 `readonly_parallel` 模式），没有 shadow/adopt 语义隔离。
- 取消策略粗暴：timeout 0.2s 后未完成的直接 `task.cancel()`，没有考虑 cancel-or-salvage。

---

## 3. 真正强的地方

### 3.1 与主流 API 形态一致
- OpenAI `Responses` 流式事件已支持 `ResponseFunctionCallArgumentsDelta`、`function_call.done`。
- Anthropic 正式提供 fine-grained tool streaming，强调"不等完整 JSON 校验即可接收参数"。
- 我们的 `StreamShadowEngine` 占位符与这一方向完全对齐，一旦参数解析补全即可产生真实延迟收益。

### 3.2 高价值场景天然适配
以下场景特别适合推测预执行：

| 场景 | 收益 | 风险 |
|------|------|------|
| `repo_rg` / `file_search` | 代码检索 I/O 主导 | 可缓存、可取消 |
| `read_file` / `stat_file` | 文件读取延迟显著 | 只读、无副作用 |
| `web_search` / `web_fetch` | 网络 RTT 高 | 需注意配额与 rate limit |
| embedding / rerank 预热 | 模型加载/索引构建慢 | 可缓存 |
| 容器/session/连接预建立 | 环境准备时间长 | 纯预热 |

这些场景的共同点是：**I/O 主导、结果可缓存、即使取消也不产生业务副作用**。

---

## 4. 不够可靠的根因：7 个 Failure Class

### FC-1: 参数"看起来完整"，但语义还没稳定
当前触发条件是"JSON 闭合"或关键词匹配，但实际问题更复杂：
- 字段值后面还会被模型改写（如 `query="auth"` → `query="auth middleware"`）。
- trailing newline / 转义变化会影响字符串匹配。
- schema 虽然合法，但语义仍在漂移。

**要求**: 引入**参数稳定度判定**（stability score），不以 JSON 闭合为唯一触发条件。

### FC-2: "只读"不等于"安全可推测"
`READONLY_TOOLS` 二分法过于粗糙。例如：
- `web_search` 会消耗 API 配额。
- `repo_rg` 吃 CPU / IO。
- `read_file` 对大文件会吃内存与解码时间。
- `db_select` 可能触发冷索引、锁争用、审计噪音。

**要求**: 按 **副作用级别 + 成本级别 + 可取消级别 + 可缓存级别** 四级分类（S0~S3），替代简单只读判断。

### FC-3: 缺少显式的"领养 / 接管"机制
当前正式执行时，如果推测任务已完成，没有 ADOPT/JOIN/CANCEL/REPLAY 状态机。可能导致：
- 重复发同一个请求。
- 正式流程拿到旧参数结果。
- 已失败任务被错误复用。
- 已取消任务还在悄悄占资源。

### FC-4: 没有"参数指纹"就无法安全复用
必须给每次 speculative run 一个 canonical key：
```
tool_name + normalized_args + environment_fingerprint + auth_scope + corpus_version
```
否则同样 `repo_rg(query="auth")`，repo HEAD 已变后结果却会被复用。

### FC-5: 取消做不干净，会产生 ghost tasks
Python 官方文档对 `asyncio` cancellation 的建议很明确：
- 任务取消会抛 `CancelledError`，清理逻辑应放在 `try/finally`。
- 如果显式捕获了取消异常，通常应在清理后继续向上传播。
- `TaskGroup` 这类结构化并发原语靠 cancellation 驱动，提供比裸 `gather()` 更强的安全保证。

当前 `_drain_speculative_tasks()` 在 timeout 0.2s 后直接 `task.cancel()`，但没有：
- 工具侧 cooperative cancellation。
- `finally` 中归还连接 / 文件句柄 / semaphore。
- 结果缓存标记为 `cancelled` 而不是静默丢失。

### FC-6: 没有预算系统，就会出现"聪明反被聪明误"
推测执行是拿额外资源换更低延迟。如果没有预算控制，系统会在高并发或长对话下变成：
- 模型还没想好，工具先把机器跑满。
- 90% 推测结果被丢弃。
- 用户感知不一定更快，系统成本却显著上升。

需要一套 **speculation budget**：
- 每 turn 最大 speculative tasks。
- 每会话最大 speculative CPU ms / API calls。
- 每工具并发上限 / 超时上限。
- abandonment rate 熔断。

### FC-7: 没有事务分层，就无法支持"更多情形"
当前更偏向单个 `tool_call` 的投机。真正想支持更多情形，要把推测分三层：

| 层级 | 名称 | 说明 |
|------|------|------|
| Level A | Tool-call speculation | 猜这个工具和参数 |
| Level B | Data speculation | 不等工具最终确定，先把可能会用的数据拉回来 |
| Level C | Environment speculation | 预建连接、预热索引、预开沙箱、预拉依赖 |

很多时候 **C 层最稳，B 层次之，A 层最激进**。

---

## 5. 核心升级：四维策略替代 READONLY_TOOLS

只看"只读"太粗。每个工具应该有 4 个标签：

| 维度 | 可选值 | 含义 |
|------|--------|------|
| `side_effect` | `pure` / `readonly` / `externally_visible` / `mutating` | 副作用级别 |
| `cost` | `cheap` / `medium` / `expensive` | 成本级别 |
| `cancellability` | `cooperative` / `best_effort` / `non_cancelable` | 可取消级别 |
| `reusability` | `cacheable` / `adoptable` / `non_reusable` | 可复用级别 |

因为很多"只读"工具依然会消耗外部配额、CPU、索引缓存、数据库连接，甚至触发审计或风控。

**建议的数据结构**:
```python
@dataclass
class ToolSpecPolicy:
    tool_name: str
    side_effect: Literal["pure", "readonly", "externally_visible", "mutating"]
    cost: Literal["cheap", "medium", "expensive"]
    cancellability: Literal["cooperative", "best_effort", "non_cancelable"]
    reusability: Literal["cacheable", "adoptable", "non_reusable"]
    speculate_mode: Literal[
        "forbid",
        "prefetch_only",
        "dry_run_only",
        "speculative_allowed",
        "high_confidence_only",
    ]
    timeout_ms: int = 1200
    max_parallel: int = 2
    cache_ttl_ms: int = 3000
    min_stability_score: float = 0.82
```

**工具分层示例**:

- **S0** — 环境预热（最稳，优先做）
  - repo index warmup
  - DB connection pre-open
  - HTTP keep-alive preconnect
  - sandbox/session warmup

- **S1** — 低成本只读（高置信度即可推）
  - `stat_file`, `list_dir`, `cache_lookup`, `read_small_file`

- **S2** — 高成本只读（只在高稳定度 + 预算允许时推）
  - `repo_rg` on large repo
  - `db_select` on cold tables
  - `web_search`, `web_fetch`, embedding/rerank

- **S3** — 写操作前置只读阶段（只能 speculative prepare）
  - `send_email` 前先 resolve recipient / render template
  - `git_apply_patch` 前先 dry-run / conflict check
  - `db_update` 前先 read current row / validate constraints

- **S4** — 真正有副作用（一律禁止正式 speculative execute）
  - `write_file`, `delete_file`, `send`, `purchase`, `submit`

---

## 6. 参数稳定度判定：四段状态机

不要用"JSON 闭合"作为触发条件。

OpenAI 明确支持流式处理 function call arguments；其 Structured Outputs 文档专门提醒要避免 JSON schema 与代码类型漂移，并建议用 Pydantic/Zod 或 `strict: true`。

这说明流式参数不是"看到右括号就开跑"的许可，而是给你一套**更早观察参数演化**的能力。

### ParseState 定义

```python
class ParseState(Enum):
    INCOMPLETE = "incomplete"
    SYNTACTIC_COMPLETE = "syntactic_complete"      # JSON 括号闭合
    SCHEMA_VALID = "schema_valid"                    # 通过 schema 校验
    SEMANTICALLY_STABLE = "semantically_stable"      # 参数停止漂移
```

**只有到 `SEMANTICALLY_STABLE` 才能真正 `speculative_start`**。

### 稳定度分数算法

综合以下指标：

1. **关键字段最近 N 个 delta 是否还在变化**
2. **字段是否发生覆写**
3. **是否命中 end tag / stop sequence**
4. **schema 是否完整通过**
5. **关键字段是否做过 canonical normalize**

**字段权重示例**：
- 高权重：`repo_rg.query`, `read_file.path`, `db_select.sql`
- 低权重：`top_k`, `encoding`, `timeout_ms`

### 候选工具调用结构

```python
@dataclass
class CandidateToolCall:
    candidate_id: str
    stream_id: str
    tool_name: str | None
    partial_args: dict[str, Any]
    parse_state: ParseState
    stability_score: float          # 0.0 ~ 1.0
    last_mutation_at: float
    semantic_hash: str
```

**工作流程**：
1. 收到 delta → 增量解析 JSON / tool call
2. 计算关键字段变化率
3. 满足 `policy.min_stability_score` 才进入 speculative queue

**注意**：不建议把 `thinking_chunk` 和 `content_chunk` 一视同仁。对工具参数流开高灵敏度监听，对普通思维文本只做弱信号观察。

---

## 7. ShadowTaskRegistry：显式状态机

Grok 提到 Future/Promise 管理，方向正确，但必须更明确：给每个 speculative task 一个显式状态机，否则并发久了必炸。

### 状态定义

```python
class ShadowTaskState(Enum):
    CREATED = "created"
    ELIGIBLE = "eligible"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    ADOPTED = "adopted"
    EXPIRED = "expired"
```

### 记录结构

```python
@dataclass
class ShadowTaskRecord:
    task_id: str
    origin_turn_id: str
    origin_candidate_id: str
    tool_name: str
    normalized_args: dict[str, Any]
    spec_key: str
    env_fingerprint: str
    policy_snapshot: ToolSpecPolicy
    state: ShadowTaskState
    started_at: float | None
    finished_at: float | None
    future: asyncio.Task[Any] | None
    cost_estimate: float
    cancel_reason: str | None = None
    adopted_by: str | None = None
```

### spec_key 计算

```python
spec_key = hash((
    tool_name,
    json.dumps(normalized_args, sort_keys=True),
    corpus_version,
    auth_scope,
    env_fingerprint,
))
```

**这是安全复用的核心**。没有这个键，就无法判断"同名不同义"，也无法做 ADOPT/JOIN。

---

## 8. 正式执行四动作：可靠性分水岭

当 `TransactionKernel` 真正进入 authoritative execution 时，只能做这 4 种动作：

| 动作 | 条件 | 效果 |
|------|------|------|
| **ADOPT** | 影子任务已完成，spec_key 完全匹配 | 直接复用结果 |
| **JOIN** | 影子任务还在跑，spec_key 完全匹配 | 正式流程接管并等待 |
| **CANCEL** | 逻辑分支变化，不再需要 | 发取消信号 |
| **REPLAY** | 参数不一致或影子任务失败 | 按正常路径重跑 |

```python
async def resolve_or_execute(
    tool_name: str,
    args: dict[str, Any],
    call_id: str,
    policy: ToolSpecPolicy,
) -> ToolResult:
    spec_key = _build_spec_key(tool_name, args)
    task = registry.lookup(spec_key)

    if task and task.state == ShadowTaskState.COMPLETED:
        return _adopt(task, call_id)
    if task and task.state == ShadowTaskState.RUNNING:
        return await _join(task, call_id)
    if task and not _is_compatible(task, args):
        await _cancel(task, reason="param_drift")
    return await _replay(tool_name, args, call_id)
```

**这一步是整个系统的"可靠性分水岭"**。
- 没有 **JOIN** → 会重复发请求。
- 没有 **REPLAY** → 会错误复用旧结果。
- 没有 **CANCEL** → 会堆 ghost tasks。

---

## 9. 结构化并发与取消传播

Python 官方文档明确建议：
- 取消会在下一次机会抛出 `CancelledError`。
- 推荐用 `try/finally` 做清理。
- 如果显式捕获了 `CancelledError`，通常应在清理后继续传播。
- `TaskGroup` 提供比裸 `gather()` 更强的安全保证。

### 实现规范

1. **每个 turn 一个 `TaskGroup`**。
2. **每个 speculative 分支一个子 registry**。
3. **每个工具 runner 接受 `cancel_token` 与 `deadline`**。
4. **所有资源释放放进 `finally`**。

```python
async def run_shadow_tool(
    call: ToolInvocation,
    cancel_token: CancelToken,
    deadline: float,
) -> Any:
    try:
        async with asyncio.timeout(deadline - time.monotonic()):
            await check_cancel(cancel_token)
            return await tool_runtime.execute(call)
    except asyncio.CancelledError:
        raise
    finally:
        await release_handles()
        await release_semaphore()
```

### Cancel-or-Salvage 策略

当 speculative task 不再被当前分支需要时，不是二选一（强杀 vs 放任），而是三选一：

1. **立刻取消**
   - 高成本任务、才刚开始、几乎不可能复用、当前已出现预算压力。

2. **允许完成并转入短 TTL 缓存**
   - 已经快结束、结果未来 1~3 秒内仍有复用价值、结果无副作用可复用。

3. **正式流程接管（JOIN）**
   - authoritative path 最终就是要它。

这比"完成后丢弃"省资源，也比"全部强杀"更少浪费。

---

## 10. 支持"更多情形"的扩展方案

### 10.1 检索链推测
不是只猜第一把工具，而是推一整段轻链：

```
repo_rg -> open_file -> read_span -> rerank
```

做法：
1. `repo_rg` 一旦稳定，先跑。
2. top-k 出来后，后台预读 top-3 文件头部或 symbol 摘要。
3. 正式流程如果要 `read_file`，大概率直接 ADOPT。

这种比纯猜下一工具更稳，因为它利用的是**数据依赖**，不是纯分支猜测。

### 10.2 Web Research 推测
链路通常是 `web_search -> fetch_url -> extract -> summarize`。

可做成三层：
1. 搜索结果出来就 speculative fetch 前 2~3 个 URL。
2. 先做轻量 HTML parse / metadata extract。
3. 正式采用时再做全文抽取与总结。

### 10.3 写工具三阶段化
把所有写操作都强制拆成：

```
Prepare -> Validate -> Commit
```

只允许 speculative 到前两阶段。

| 写操作 | Prepare | Validate |
|--------|---------|----------|
| `write_file` | 生成 patch、检查路径、校验编码 | 看冲突 |
| `git_push` | fetch remote | check divergence、模拟 merge |
| `send_email` | resolve contact | 生成 draft body、检查附件 |

### 10.4 Computer Use / UI 自动化
**只做**：
- screenshot / DOM snapshot prefetch
- accessibility tree indexing
- selector candidate ranking

**不做**：
- 点击 destructive button
- 提交表单
- 滚动到影响状态的位置

### 10.5 数据库场景
**只做**：只读查询预跑、explain / prepare statement、连接池预热、schema introspection。

**不做**：insert/update/delete 真正提交、带锁读写、触发外部 side effect 的存储过程。

### 10.6 长轨 Agent 的分支推测
如果模型在两个工具间摇摆，可做有限的双分支投机，但必须非常克制：
- 最多 2-way fork
- 总预算固定
- 先执行 shared prefix 或 cheapest branch
- 任一分支 authoritative 确定后立刻取消另一分支

---

## 11. 预算治理与熔断

每次 speculative start 前都做预算准入：

```python
if not budget.allow(tool, estimated_cost, confidence, queue_pressure):
    skip()
```

### 预算输入

- 当前 speculative 并发数
- 最近 abandonment ratio
- 外部 API quota 剩余
- CPU / memory pressure
- authoritative queue backlog

### 熔断条件

| 条件 | 动作 |
|------|------|
| abandonment ratio > 60% | 降级到仅允许 S0/S1 |
| speculative timeout ratio > 20% | 降级到仅允许 S0/S1 |
| wrong-adoption > 0 | 暂停 speculation，告警 |
| queue pressure 持续高于阈值 | 暂停新增 speculative tasks |

### 运行模式

- **Turbo**：偏激进，适合本地开发
- **Balanced**：默认
- **Safe**：仅 S0/S1 工具允许推测

---

## 12. 可观测性

### 核心指标

```python
speculative_started
speculative_completed
speculative_adopted
speculative_joined
speculative_cancelled
speculative_abandoned
replay_after_speculation
wrong_adoption_incident
median_saved_ms
wasted_cpu_ms
wasted_api_calls
```

### 统一日志字段

```json
{
  "turn_id": "...",
  "candidate_id": "...",
  "tool": "repo_rg",
  "spec_key": "...",
  "policy": "high_confidence_only",
  "stability_score": 0.91,
  "action": "start|adopt|join|cancel|replay|abandon",
  "reason": "...",
  "latency_ms": 842,
  "saved_ms": 617
}
```

---

## 13. 对"可靠性"的量化标准

不用"跑起来没报错"来定义可靠。用下面 5 条：

1. **关闭 speculation 后，系统 correctness 不变**。
2. **wrong adoption 接近 0**。
3. **ghost task 可证明被回收**。
4. **abandonment 有上限**。
5. **在真实 workload 下确实节省端到端延迟**。

### 延迟收益指标

- speculative hit ratio
- adopt ratio
- join ratio
- median saved latency per adopted task
- user-visible turn latency delta

### 代价指标

- abandonment ratio
- wasted CPU ms
- wasted external API calls
- cancellation success ratio
- timeout ratio

### 正确性指标

- wrong-adoption incidents
- stale-result incidents
- param-drift mismatches
- replay-after-adopt incidents

---

## 14. 最终评价与演进路径

**当前状态**：不是不可靠，而是"还没完全从邪术升级成内核"。

如果它现在只是：
- 流里抓到 `tool_call`
- 命中 `readonly` 白名单
- 后台先跑
- 正式执行时试图复用

那它还只是**聪明的小优化**。

如果把它补成：
- 参数稳定度判定
- `ToolSpecPolicy` 分级
- `ShadowTaskRegistry` 状态机
- ADOPT / JOIN / CANCEL / REPLAY
- `TaskGroup` + cancellation propagation
- Cancel-or-Salvage
- budget governor
- observability

它就会变成：**一个真正支撑多情形 agent 的 latency-hiding kernel**。

### 立刻补的优先级

**第一优先级**:
1. `ShadowTaskRegistry` 状态机
2. canonical arg normalization + `spec_key`
3. 显式 adopt/join/cancel/replay 流程

**第二优先级**:
1. 把 `READONLY_TOOLS` 升级成 `ToolSpecPolicy`
2. per-tool timeout / semaphore / budget governor

**第三优先级**:
1. cancellation token 和 `finally` cleanup
2. abandonment / hit ratio / wrong adoption 监控

**第四优先级**:
1. environment speculation
2. write-tool 的 prepare/validate/commit 分层

### 特殊场景处理

如果未来支持 Claude 系列或类似的流式拒绝机制，系统必须把 **refusal** 也当成 turn 级中断信号。Anthropic 的文档明确要求在 `stop_reason: "refusal"` 后重置上下文继续处理。对影子执行系统来说，这意味着一旦 turn 被拒绝，相关 speculative tasks 也应该同步取消或转为**不可采用状态**。

---

## 15. 与 Polaris 现有架构的衔接点

| 现有组件 | 升级后职责 |
|----------|-----------|
| `stream_shadow_engine.py` | 升级为 `CandidateToolCall` 提取器 + `StabilityScorer` |
| `speculative_executor.py` | 升级为 `ShadowTaskRegistry` 的启动入口 |
| `turn_transaction_controller.py` | 在 authoritative execution 阶段接入 `resolve_or_execute()` |
| `TurnLedger` | 扩展记录 `speculative_joined`、`speculative_replayed` 等细粒度指标 |
| `ToolBatchRuntime` | 提供 `readonly_parallel` 执行能力，保持不变；新增 `cancel_token` 透传 |

**架构原则**：所有升级必须在 `TransactionKernel` 事务语义内完成。`ShadowEngine` 只能猜，`TransactionKernel` 必须负责"采用 / 等待 / 放弃 / 取消 / 降级重跑"。
