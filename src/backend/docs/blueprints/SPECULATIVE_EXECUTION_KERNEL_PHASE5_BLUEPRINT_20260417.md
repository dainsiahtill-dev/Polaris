# Blueprint: Speculative Execution Kernel Phase 5 — Chain Speculation, Web Prefetch & Write Tool 3-Phase

**Date:** 2026-04-17  
**Scope:** `polaris/cells/roles/kernel/internal/speculation/` and upstream integration points  
**Status:** Draft → Implementation Ready  
**Author:** Principal Architect  
**Related:** ADR-0077, VC-20260417-speculative-execution-kernel-v2

---

## 1. 业务背景与问题陈述

Phase 1~4 已落地 Speculative Execution Kernel 的核心骨架：Registry、Resolver、BudgetGovernor、SalvageGovernor、CandidateDecoder、StabilityScorer。当前系统能够安全地对**单个工具调用**做推测执行，但真实 agent 工作流中存在大量**隐式工具链**：

- `repo_rg` / `search_code` 之后几乎一定会跟 `read_file`（阅读匹配到的文件）
- `web_search` 之后几乎一定会跟 `fetch_url` / `read_webpage`（读取前几个搜索结果）
- 写操作工具（`write_file`、`apply_patch`）在实际写入前，模型通常会先调用 `read_file` 确认上下文

这些链路的端到端延迟占总 turn 时间的 30%~60%，但当前系统只能对链路的**第一个工具**做推测。Phase 5 的目标是：

> **让推测执行从“单点工具”扩展到“工具链”，在第一个 shadow task 完成后自动触发下游推测，进一步隐藏延迟。**

---

## 2. 高层架构（文本图）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         StreamShadowEngine (Facade)                          │
│                              │                                               │
│    ┌─────────────────────────┼─────────────────────────┐                     │
│    │                         ▼                         │                     │
│    │  ┌──────────────────────────────────────────┐    │                     │
│    │  │         ChainSpeculator (NEW)            │    │                     │
│    │  │                                          │    │                     │
│    │  │  ┌─────────────┐    ┌─────────────┐     │    │                     │
│    │  │  │  repo_rg    │───►│ read_file   │     │    │                     │
│    │  │  │  shadow     │    │  prefetch   │     │    │                     │
│    │  │  └─────────────┘    └─────────────┘     │    │                     │
│    │  │                                          │    │                     │
│    │  │  ┌─────────────┐    ┌─────────────┐     │    │                     │
│    │  │  │ web_search  │───►│ fetch_url   │     │    │                     │
│    │  │  │  shadow     │    │  prefetch   │     │    │                     │
│    │  │  └─────────────┘    └─────────────┘     │    │                     │
│    │  │                                          │    │                     │
│    │  │  ┌─────────────┐    ┌─────────────┐     │    │                     │
│    │  │  │ read_file   │───►│  write_file │     │    │                     │
│    │  │  │  (validate) │    │  (prepare)  │     │    │                     │
│    │  │  └─────────────┘    └─────────────┘     │    │                     │
│    │  └──────────────────────────────────────────┘    │                     │
│    │                         │                         │                     │
│    │                         ▼                         │                     │
│    │           ShadowTaskRegistry.start_shadow_task()  │                     │
│    │                         │                         │                     │
│    └─────────────────────────┼─────────────────────────┘                     │
│                              ▼                                               │
│              TurnScopedTaskGroup (structured concurrency)                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 模块职责划分

### 3.1 新增模块

| 模块 | 文件 | 职责 |
|------|------|------|
| `ChainSpeculator` | `speculation/chain_speculator.py` | 监听 Registry 的 shadow 完成事件，自动触发下游推测。支持 retrieval chain 和 web prefetch 两种模式。 |
| `WriteToolPhases` | `speculation/write_phases.py` | 为写工具提供 `prepare` → `validate` → `commit` 三阶段语义。`prepare` 可安全 speculative，`commit` 必须由 authoritative 路径执行。 |

### 3.2 改造模块

| 模块 | 改造点 |
|------|--------|
| `ShadowTaskRegistry` | 在 `adopt()` / `complete` 回调中增加 `chain_completed` 钩子，允许 `ChainSpeculator` 订阅并触发下游推测。 |
| `StreamShadowEngine` | `consume_delta()` 在检测到 `SEMANTICALLY_STABLE` 后，不仅启动当前 tool 的 shadow，还通过 `ChainSpeculator.predict_downstream()` 预注册可能的下游 shadow。 |
| `SpeculationResolver` | 扩展 `resolve_or_execute()` 支持 write tool 的 `prepare` 结果复用（如果 prepare 已完成且未被废弃）。 |
| `TurnTransactionController` | 在 `_execute_tool_batch()` 中对写工具批次启用 `WriteToolPhases` 编排。 |

---

## 4. 核心数据流

### 4.1 Retrieval Chain 推测流

1. `StreamShadowEngine` 流式解析到 `repo_rg(query="auth middleware")`， stability 达标。
2. 启动 `repo_rg` 的 shadow task（Tier S2，经 BudgetGovernor 准入）。
3. `repo_rg` shadow 完成，结果包含匹配文件列表 `["src/auth.ts", "src/middleware.ts"]`。
4. `ChainSpeculator` 监听到 `repo_rg` 完成事件，提取 top-k（默认 k=3）文件路径。
5. 自动为每个路径启动 `read_file(path=...)` 的 shadow task（Tier S1）。
6. 当 authoritative batch 到达 `read_file(src/auth.ts)` 时，该 shadow 可能已被 ADOPT 或 JOIN。

### 4.2 Web Research Prefetch 流

1. `web_search(query="FastAPI auth middleware")` shadow 完成。
2. 结果摘要中提取 URL 列表（按搜索引擎排名或摘要相关性排序）。
3. `ChainSpeculator` 为前 N 个 URL（默认 N=2）启动 `fetch_url(url=...)` shadow。
4. authoritative batch 中若出现 `fetch_url` 或 `read_webpage`，直接 ADOPT/JOIN。

### 4.3 Write Tool 3-Phase 流

```
Prepare (speculative allowed)
    │
    ▼
Validate (speculative allowed, idempotent check)
    │
    ▼
Commit (authoritative only, effect_receipt required)
```

1. 模型输出 `write_file(path="src/auth.ts", content="...")`。
2. `StreamShadowEngine` 识别到这是写工具，在 `consume_delta()` 阶段只触发 **Prepare** shadow：
   - 调用一个只读的 `validate_path_exists` + `validate_content_schema` 工具组合；
   - 或调用写工具的 `dry_run=True` 模式（如果工具支持）。
3. authoritative batch 到达时，`SpeculationResolver` 可 ADOPT **Prepare** 结果。
4. `TurnTransactionController._execute_tool_batch()` 将写批次拆分为：
   - `prepare/validate` 复用 shadow 结果（若命中）；
   - `commit` 必须走 serial_writes 的 authoritative 路径。

---

## 5. 关键设计决策

### 5.1 ChainSpeculator 触发规则

`ChainSpeculator` 不是无条件触发所有下游工具，而是遵循策略表：

| 上游工具 | 下游推测工具 | 触发条件 | 默认 top-k | 预算 Tier |
|----------|-------------|----------|-----------|----------|
| `repo_rg` | `read_file` | shadow 成功完成且结果包含文件路径 | 3 | S1 |
| `search_code` | `read_file` | shadow 成功完成且结果包含文件路径 | 3 | S1 |
| `web_search` | `fetch_url` | shadow 成功完成且结果包含 URL | 2 | S2 |
| `read_file` | `write_file` / `apply_patch` | 仅在 delta 中检测到后续 write 意图时（高置信度） | 1 | S3 |

**约束：**
- 下游推测工具必须是 `READONLY_TOOLS` 或 `ASYNC_TOOLS`（预算上可 speculative）。
- 下游推测必须通过 `BudgetGovernor` 准入检查。
- 下游推测共享同一个 `turn_id` 和 `task_group`。
- 如果上游 shadow 在 authoritative 阶段被 CANCELLED 或 ABANDONED，其自动触发的所有下游 shadow 必须级联取消/废弃。

### 5.2 结果解析与 top-k 提取

由于工具返回格式不统一，`ChainSpeculator` 使用**启发式提取器**：

```python
class ResultExtractor:
    def extract_file_paths(self, tool_result: Any) -> list[str]: ...
    def extract_urls(self, tool_result: Any) -> list[str]: ...
```

实现策略：
1. 优先查找标准字段：`files`、`matches[].path`、`results[].url`、`urls`。
2. 如果标准字段缺失，在字符串结果中使用正则提取文件路径和 URL。
3. 对文件路径做 workspace 相对路径归一化；对 URL 做去重和域名白名单过滤（禁止内部/admin 等敏感 URL）。

### 5.3 Write Tool 3-Phase 的安全边界

**Prepare 阶段：**
- 不产生持久化副作用。
- 如果工具原生支持 `dry_run=True`，优先使用 dry run。
- 否则使用只读校验组合（`file_exists` + `lint_check`）。

**Validate 阶段：**
- 可选。如果 Prepare 输出包含语法/schema 错误，Validate 会捕获并返回 error receipt。
- 可 speculative（因为也是只读校验）。

**Commit 阶段：**
- 必须由 authoritative batch 执行。
- `ToolBatchRuntime` 中的 `serial_writes` 分组确保写操作串行化。
- 必须生成 `effect_receipt`（用于回滚/审计）。

### 5.4 级联取消（Cascade Cancel）

当上游 shadow 被废弃或取消时，Registry 通过 `_turn_index` 查找同 `turn_id` 的所有任务。Phase 5 增加**级联取消**逻辑：

```python
# In ShadowTaskRegistry.cancel() or abandon_turn()
cascade_ids = self._chain_index.get(upstream_task_id, set())
for downstream_id in cascade_ids:
    await self.cancel(downstream_id, reason="upstream_cancelled")
```

新增 `_chain_index: dict[str, set[str]]` 记录上游 → 下游的依赖关系。

---

## 6. 接口变更

### 6.1 新增接口

```python
# speculation/chain_speculator.py
class ChainSpeculator:
    def __init__(self, registry: ShadowTaskRegistry, budget_governor: BudgetGovernor | None = None) -> None: ...
    async def on_shadow_completed(self, record: ShadowTaskRecord) -> list[ShadowTaskRecord]: ...
    def predict_downstream(self, tool_name: str, tool_result: Any) -> list[PredictedInvocation]: ...

# speculation/write_phases.py
class WriteToolPhases:
    @staticmethod
    def is_write_tool(tool_name: str) -> bool: ...
    @staticmethod
    def build_prepare_invocation(invocation: ToolInvocation) -> ToolInvocation: ...
    @staticmethod
    def build_commit_invocation(invocation: ToolInvocation) -> ToolInvocation: ...
```

### 6.2 改造接口

```python
# ShadowTaskRegistry
class ShadowTaskRegistry:
    def __init__(...):
        self._chain_index: dict[str, set[str]] = {}  # NEW

    async def start_shadow_task(..., parent_task_id: str | None = None) -> ShadowTaskRecord:
        # 如果 parent_task_id 存在，注册级联关系
        ...
```

---

## 7. 测试策略

### 7.1 新增测试文件

| 文件 | 测试点 |
|------|--------|
| `test_speculation_chain.py` | `ChainSpeculator` 从 `repo_rg` 结果提取文件路径并触发 `read_file` shadow；级联取消生效。 |
| `test_speculation_web_prefetch.py` | `web_search` 完成后自动触发 `fetch_url`；URL 白名单过滤恶意/内部地址。 |
| `test_speculation_write_phases.py` | write tool 的 prepare shadow 可被 adopt；commit 必须走 authoritative 路径。 |

### 7.2 集成测试扩展

在 `test_speculation_integration.py` 中新增：

1. `test_retrieval_chain_adopts_prefetch`：`repo_rg` shadow 完成后，下游 `read_file` shadows 被正确 ADOPT。
2. `test_cascade_cancel_abandons_downstream`：上游 `repo_rg` 被 refusal abort 后，所有自动触发的 `read_file` 也被标记为 ABANDONED。
3. `test_write_tool_commit_never_speculative`：`write_file` 的 commit 阶段不会被 shadow 执行。

### 7.3 回归测试

- 全目录 `polaris/cells/roles/kernel/tests/` 必须保持 **1016 passed, 1 skipped**。

---

## 8. 风险与边界

1. **链式爆炸**：`repo_rg` 可能返回几十个匹配文件，无限制 prefetch 会导致并发和 token 压力。**缓解**：top-k=3 + BudgetGovernor 并发限制。
2. **URL 安全风险**：`web_search` 结果可能包含恶意链接。**缓解**：域名白名单 + URL 长度/格式校验 + 禁止 fetch 本地/internal 地址。
3. **Write tool prepare 语义不一致**：不同工具对 `dry_run` 的支持程度不同。**缓解**：默认 fallback 到只读校验组合，不依赖工具的 dry_run 实现。
4. **级联取消时序**：上游 task 完成并触发下游后，上游才被 authoritative 取消，此时下游可能已在运行。**缓解**：级联取消在 `abandon_turn()` 和 `cancel(task_id)` 两个入口都做，下游任务收到 `upstream_cancelled` 原因。

---

## 9. 实施顺序

1. **Step 1**: 实现 `ChainSpeculator` + `ResultExtractor`，支持 `repo_rg` → `read_file` 和 `web_search` → `fetch_url`。
2. **Step 2**: 扩展 `ShadowTaskRegistry` 的 `_chain_index` 和级联取消逻辑。
3. **Step 3**: 在 `StreamShadowEngine` 和 `Registry` 中接入 `on_shadow_completed` 回调。
4. **Step 4**: 实现 `WriteToolPhases`，改造 `SpeculationResolver` 支持 prepare 结果复用。
5. **Step 5**: 改造 `TurnTransactionController._execute_tool_batch()` 对写批次启用 3-phase 拆分。
6. **Step 6**: 编写 `test_speculation_chain.py`、`test_speculation_web_prefetch.py`、`test_speculation_write_phases.py` 及集成测试扩展。
7. **Step 7**: 全量回归测试。

---

## 10. 自检清单（Self-Check）

- [x] 安全优先：写工具的 commit 阶段禁止 speculative 执行。
- [x] 向后兼容：`StreamShadowEngine` 和 `SpeculationResolver` 的 public 签名不变。
- [x] 类型安全：所有新增模块使用 `from __future__ import annotations` 与现代类型注解。
- [x] 防御性编程：`ResultExtractor` 对缺失字段有 fallback；URL 有白名单过滤。
- [x] 禁止炫技：不使用元编程、动态导入或复杂的 descriptor 模式。
