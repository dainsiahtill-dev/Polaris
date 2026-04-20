# Transaction Kernel 架构审计与整改计划

**审计日期**: 2026-04-19  
**审计范围**: `polaris/cells/roles/kernel/internal/transaction/`  
**状态**: 已审计，待整改  
**整改原则**: 先协议归一化，再行为修复，最后架构收口

---

## 执行摘要

Transaction Kernel 当前的主要敌人不是功能缺失，而是**协议语义分叉**。本次审计发现 7 项 CRITICAL、20 项 WARNING、14 项 NOTE，其背后可归纳为 3 个根因。整改顺序有严格依赖关系，不可并行乱做。

| 根因 | 表现 | 破坏的不变量 | 修复包 |
|------|------|-------------|--------|
| 协议常量没有单一真相源 | WRITE_TOOLS 三处定义不一致；READ_TOOLS / _READONLY_TOOLS 范围不匹配；retry / decoder / guard 各自硬编码 | "同一 invocation 在所有阶段分类一致" | #1 协议归一化 |
| 系统从"硬协议"滑向"软行为" | finalization 从 panic 改为吞掉；guard 从 hard fail 改为 warning；prompt 约束被删薄 | "协议违规可被可靠发现并追责" | #3 收口安全模型 |
| 交易内核存在多条并行语义路径 | Controller 自建 hybrid intent；Gateway 也做 classify；Retry/Executor/Guard 各自推断 execution_mode | "意图分类、执行模式、收据格式在所有路径下输出同构" | #2 收据/账本归一化 + #4 意图入口收口 |

---

## 根因 A：协议常量没有单一真相源

### 表现

| 位置 | 写工具集合 | 与 `constants.py` 的差异 |
|------|-----------|------------------------|
| `constants.py:31` | 8 个：precision_edit, edit_blocks, search_replace, edit_file, repo_apply_diff, append_to_file, write_file, create_file | 基准 |
| `write_phases.py:19` | 6 个：write_file, apply_patch, edit_file, create_file, delete_file, rename_file | **缺** precision_edit, edit_blocks, search_replace, append_to_file, repo_apply_diff；**多** delete_file, rename_file, apply_patch |
| `turn_decision_decoder.py:87` | 7 个：write_file, edit_file, delete_file, bash, mkdir, mv, cp | **缺** precision_edit, edit_blocks, search_replace, repo_apply_diff, append_to_file, create_file；**多** bash, mkdir, mv, cp |

`READ_TOOLS` 同理：`constants.py` 只列 4 个 repo 读工具，而 `turn_contracts.py:_READONLY_TOOLS` 列了 13 个（含 list_directory, grep, search_code 等）。`retry_orchestrator.py` 的 `write_candidates` 还做了第 4 处硬编码。

### 后果

- 同一个 tool 在不同阶段被当成不同物种：speculative 层认为是"非写工具"而 adopt，但 authoritative 层执行时合约守卫认为是"写工具"而触发 violation
- speculative / authoritative / guard / decoder 无法形成闭环，修复任何一层都可能导致另一层漂移

### 治理方式

不是"逐个补齐集合"，而是：

1. `transaction/constants.py` 成为**唯一导出层**
2. 其他模块**只 import，不允许再声明局部集合**
3. 增加**一致性单测**，扫描全仓所有引用点，发现新增硬编码即失败

---

## 根因 B：系统从"硬协议"滑向"软行为"

### 表现

| 层级 | 改动前 | 改动后（当前） |
|------|--------|---------------|
| Prompt | "不要调用任何工具" 硬约束 | 简化为正面指令，移除显式禁止 |
| API | `tool_choice=none` | 不变 |
| Decoder | 发现 tool call → `HANDOFF_WORKFLOW` panic | 发现 tool call → 静默丢弃，返回 `FINAL_ANSWER` |
| Guard | `assert_no_finalization_tool_calls` 抛 `KernelGuardError` | 仅记录 warning log |

### 后果

- 从"三层硬拦截（API + Prompt + Guard）"降级为"两层软拦截（API + Decoder）"
- 如果 Decoder 过滤逻辑有漏洞，tool call 可能泄漏到下游，且没有任何硬层能捕获
- 仅 warning log 无法支持可观测、可追责、可回放

### 治理方式

不是回退到全 panic，而是建立"软落地 + 强观测 + 可追责"模型：

| 层级 | 职责 | 行为 |
|------|------|------|
| Prompt | 显式禁止 | 保留"不要调用工具"约束（恢复） |
| Decoder | 容错丢弃 | 继续丢弃幻觉 tool call |
| Guard | 留下强证据 | 不抛异常，但记录 **metrics + structured event + ledger anomaly flag** |

Guard 层的软化是合理的（不打断用户链路），但必须留下**可被外部系统消费的证据**，而不是只写一条人读的 warning log。

---

## 根因 C：交易内核存在多条并行语义路径

### 表现

```
用户输入
    │
    ├──► TurnTransactionController._requires_mutation_intent_hybrid()
    │       ├── Regex classify
    │       └── Embedding fallback  ← 完全跳过 SLM
    │
    └──► CognitiveGateway.classify_intent()
            ├── Embedding Router
            ├── SLM Coprocessor      ← Controller 路径永远不用
            └── Regex fallback
```

同时：
- `turn_decision_decoder.py` 自己推断 `execution_mode`
- `contract_guards.py` 自己判定 `is_write_invocation`
- `retry_orchestrator.py` 自己决定 `write_candidates`

### 后果

- 系统表面上是统一的 Transaction Kernel，实际上是多个小内核叠在一起
- 任何局部修复都会在另一条路径上产生新的分叉
- 测试覆盖存在断层：Gateway 有 47 个测试全部通过，但 Controller 的核心消费路径**零覆盖**

### 治理方式

- Controller 不再自己判意图，只调用 Gateway
- Gateway 内部决定 regex / embedding / SLM 的级联策略
- 执行模式推断收束到单一模块（`turn_contracts.py:_infer_execution_mode`）

---

## 修复包 1：协议归一化（P0，最先做）

### 涉及问题

- WRITE_TOOLS 三处不一致
- READ_TOOLS / _READONLY_TOOLS 不一致
- retry/write_candidates 硬编码
- execution_mode fallback 偏差

### 目标

把"工具类别判定"收束成一个权威模块，禁止 decoder / retry / guard 各自猜测。

### 改动清单

1. **`transaction/constants.py`**
   - 从 `turn_contracts.py` 导入 `_READONLY_TOOLS` 和 `_ASYNC_TOOLS`，扩展 `READ_TOOLS`
   - 注释中标注："唯一真相源，禁止其他模块重复声明"

2. **`speculation/write_phases.py`**
   - 删除 `_WRITE_TOOLS` 局部定义
   - 改为 `from transaction.constants import WRITE_TOOLS`
   - 保留 `is_write_tool` 方法作为 facade，但内部引用统一常量

3. **`turn_decision_decoder.py`**
   - 删除 `WRITE_TOOLS` 局部定义
   - 改为从 `transaction.constants` 导入
   - `_infer_execution_mode` 的工具列表也从统一常量派生

4. **`retry_orchestrator.py`**
   - `write_candidates` 改为引用 `tuple(WRITE_TOOLS)`

5. **`contract_guards.py`**
   - `is_write_invocation` 的判定逻辑与 `_infer_execution_mode` 对齐

6. **新增一致性测试**
   - `test_write_tools_single_source_of_truth`: 扫描全仓 `frozenset`/`set` 字面量中包含写工具名的定义，确保只有一处
   - `test_read_tools_single_source_of_truth`: 同上，针对读工具
   - `test_unknown_tool_classification_policy`: 验证未知工具默认分类策略在所有模块一致

### 验收条件

```python
# 全仓 grep 只能找到一处 WRITE_TOOLS 定义
assert grep_count("WRITE_TOOLS.*=.*frozenset|set") == 1

# 所有 is_write_tool 类方法的内部实现都引用同一常量
for impl in find_all_is_write_tool():
    assert impl.references == {"transaction.constants.WRITE_TOOLS"}

# _infer_execution_mode 的工具列表与 constants 导出的集合等价
assert set(_infer_execution_mode._tool_lists) == constants.READ_TOOLS | constants.ASYNC_TOOLS | constants.WRITE_TOOLS
```

### 依赖关系

无前置依赖，可独立做。但必须先完成，否则修复包 2/3/4 都会在各自路径上产生新的漂移。

---

## 修复包 2：收据/账本语义归一化（P0，第二个做）

### 涉及问题

- speculative receipt 缺 `raw_results`
- `ledger.tool_batch_count` 双点维护
- stale edit failure 漏检风险

### 目标

建立统一 receipt schema，所有 adopt/join/authoritative 输出同构 receipt，`tool_batch_count` 只允许一个 owner 写入。

### 改动清单

1. **统一 receipt schema**
   - 在 `transaction/` 下新建 `receipt_schemas.py`（或复用 `public/turn_contracts.py`）
   - 定义 `ToolBatchReceipt` dataclass / Pydantic model，字段包含：
     - `batch_id`, `turn_id`, `results: list[ToolResult]`
     - `raw_results: list[dict]`（必填）
     - `success_count`, `failure_count`
     - `execution_mode`, `effect_type`
   - `tool_batch_executor.py` ADOPT/JOIN 路径：补充 `raw_results` 字段
   - `tool_batch_executor.py` authoritative 路径：确保输出符合同一 schema

2. **统一 ledger 计数**
   - `ToolBatchExecutor.execute_tool_batch()` 是 `tool_batch_count` 的唯一 owner
   - `RetryOrchestrator` 不再手动 `ledger.tool_batch_count += 1`
   - `RetryOrchestrator` 调用 `execute_tool_batch(..., count_towards_batch_limit=True)`，由 Executor 统一递增
   - 删除 `count_towards_batch_limit` 参数（简化接口），改为 Executor 内部判断：如果是 retry 调用，不递增（由 RetryOrchestrator 在更高层跟踪 retry 次数）
   - 或者更干净的做法：`RetryOrchestrator` 记录自己的 `retry_attempt_count`，`tool_batch_count` 只记录实际执行了多少个 batch（含 retry 产生的 batch）

3. **stale edit failure 统一检测**
   - `receipts_have_stale_edit_failure` 只检查统一 schema 中的 `raw_results`
   - 删除对旧格式 `result.get("result")` 的兼容路径

### 验收条件

```python
# 任意 speculative/adopt/join receipt 都含统一字段集
for receipt in all_receipts():
    assert "raw_results" in receipt
    assert "results" in receipt
    assert "batch_id" in receipt

# tool_batch_count 在单测中可证明不会重复递增
def test_batch_count_single_owner():
    ledger = TurnLedger()
    # 模拟 retry 路径
    executor.execute_tool_batch(..., ledger)  # count += 1
    # RetryOrchestrator 不再手动递增
    assert ledger.tool_batch_count == 1
```

### 依赖关系

依赖修复包 1（协议归一化），因为 receipt schema 中的 `execution_mode` / `effect_type` 判定需要统一常量。

---

## 修复包 3：Finalization 安全模型（P1）

### 涉及问题

- protocol panic 死代码
- `assert_no_finalization_tool_calls` 软化
- finalization prompt 约束削弱

### 目标

明确定义 finalize 阶段为："协议上禁止，实现上容错，观测上显式告警"。

### 改动清单

1. **清理死代码**
   - `finalization.py:91-129`：删除 protocol panic 分支，或改为防御性断言（`assert finalize_decision.kind != HANDOFF_WORKFLOW, "decoder must filter this"`）

2. **恢复 Prompt 约束**
   - `finalization.py` 收口提示词恢复 "不要调用任何工具" 显式约束

3. **Guard 层强化观测**
   - `kernel_guard.py:assert_no_finalization_tool_calls` 不抛异常，但：
     - 写入 `ledger.anomaly_flags.append("finalize_tool_call_hallucination")`
     - 发射 structured event：`{"event": "guard.anomaly", "type": "finalize_tool_call", "turn_id": ...}`
     - 上报 metrics counter：`kernel_guard.finalization_hallucination_total`
   - 这些观测点可以被外部监控消费，支持"可追责"

4. **增加 regression 测试**
   - 测试：Decoder 丢弃 tool call 后，Guard 能正确识别 anomaly 并记录
   - 测试：Guard 不抛异常，不中断用户链路

### 验收条件

```python
# Guard 软化但不沉默
async def test_finalize_guard_records_anomaly():
    ledger = TurnLedger()
    guard.assert_no_finalization_tool_calls(decision, ledger)
    assert "finalize_tool_call_hallucination" in ledger.anomaly_flags
    assert metrics.get("kernel_guard.finalization_hallucination_total") == 1
```

### 依赖关系

依赖修复包 1（常量统一）。可和修复包 2 并行，但建议顺序做（先收据归一化，再收口模型，因为 anomaly flag 需要写入 ledger）。

---

## 修复包 4：意图入口收口（P1，架构回正）

### 涉及问题

- Controller vs Gateway 路径分叉
- SLM 健康探测虚假阳性
- embedding warmup 不等待
- hybrid 路径无测试

### 目标

让 Controller 不再自己判意图，统一委托 Gateway。

### 改动清单

1. **Controller 委托 Gateway**
   - `TurnTransactionController._requires_mutation_intent_hybrid()` 改为调用 `CognitiveGateway.classify_intent(latest_user_request)`
   - 删除 Controller 内嵌的 `classify_with_embedding_fallback` 函数
   - Gateway 内部保持三级瀑布（Embedding → SLM → Regex）

2. **修复 SLM 健康探测**
   - `cognitive_gateway.py:_probe_slm_health` 要求 `_invoke_slm` 返回**非空字符串**才判定健康

3. **修复 warmup 生命周期**
   - `CognitiveGateway.__del__` 或 `close()` 方法中 cancel 并 await `_warmup_task`

4. **补充缺失测试**
   - `test_controller_intent_routing`：验证 Controller 委托 Gateway
   - `test_slm_health_false_positive`：模拟 SLM 返回空字符串，验证探测失败
   - `test_embedding_warmup_wait`：验证 warmup 完成后 classify 才走 embedding 层

### 验收条件

```python
# Controller 不再自己判意图
async def test_controller_delegates_to_gateway():
    with patch("CognitiveGateway.classify_intent") as mock:
        await controller._execute_turn(...)
        assert mock.called

# SLM 空字符串返回判定为不健康
async def test_slm_empty_response_unhealthy():
    with patch("_invoke_slm", return_value=""):
        assert not gateway.is_slm_healthy()
```

### 依赖关系

依赖修复包 1（常量统一，因为 Gateway 的意图分类需要准确的工具类别信息）。可和修复包 2/3 并行。

---

## PR 执行序列

```
修复包 1: 协议归一化 PR
    │
    ├──► 修复包 2: 收据/账本归一化 PR
    │       │
    │       └──► 修复包 3: Finalization 安全模型 PR
    │
    └──► 修复包 4: 意图入口收口 PR
```

### 为什么不能并行乱做

1. **修复包 2/3/4 都依赖修复包 1**：如果常量还没统一，receipt schema 中的 `execution_mode`、finalization 的 tool 分类、Gateway 的 intent 路由都会在各自路径上产生新的漂移。
2. **修复包 3 建议依赖修复包 2**：anomaly flag 需要写入 ledger，如果 ledger 的字段还没统一，会产生新的不一致。
3. **修复包 4 可以和 2/3 并行**：但建议顺序做，因为 team bandwidth 有限，且每个 PR 都需要仔细 review。

---

## 附录：问题完整清单

### CRITICAL（7 项）

| # | 问题 | 破坏的不变量 | 所属修复包 |
|---|------|-------------|-----------|
| 1 | WRITE_TOOLS 三处定义互不一致 | "同一 invocation 在所有阶段分类一致" | #1 |
| 2 | Controller 与 Gateway 意图分类路径分叉 | "意图分类在所有路径下输出同构" | #4 |
| 3 | FinalizationHandler protocol panic 死代码 | "收口阶段决策可被可靠解析" | #3 |
| 4 | assert_no_finalization_tool_calls 已完全软化 | "协议违规可被可靠发现并追责" | #3 |
| 5 | ledger.tool_batch_count 双点维护 | "batch_count 可被唯一确定" | #2 |
| 6 | speculative receipt 格式不一致 | "所有执行结果可统一回放与验证" | #2 |
| 7 | SLM 健康探测存在虚假阳性 | "SLM 不可用时不被错误依赖" | #4 |

### WARNING（20 项）

| # | 问题 | 所属修复包 |
|---|------|-----------|
| 8 | READ_TOOLS 与 _READONLY_TOOLS 范围不一致 | #1 |
| 9 | retry_orchestrator write_candidates 硬编码 | #1 |
| 10 | task_contract_builder between_match 只取下限 | #2 |
| 11 | 意图分类优先级未覆盖组合场景 | #4 |
| 12 | classify_with_embedding_fallback 未等待 warmup | #4 |
| 13 | Controller hybrid 路径无测试覆盖 | #4 |
| 14 | SLM 输出清理过于宽松 | #4 |
| 15 | compress_text 降级返回空字符串 | #4 |
| 16 | max_retry_attempts / max_followup_attempts 硬编码 | #2 |
| 17 | summary 截断可能截断 JSON | #3 |
| 18 | error 提取逻辑多层 fallback 不明确 | #2 |
| 19 | _infer_execution_mode fallback 默认 WRITE_SERIAL | #1 |
| 20 | stream_orchestrator model/usage 信息丢失 | #3 |
| 21 | handoff context 膨胀 | #2 |
| 22 | tool_batch_runtime 不区分可重试错误类型 | #2 |
| 23 | retry bootstrap 未检查 requires_mutation | #4 |
| 24 | warmup 任务无取消/等待机制 | #4 |
| 25 | embedding router 未配置模型参数 | #4 |
| 26 | ledger.record_decision Pydantic 兼容处理脆弱 | #2 |
| 27 | 意图标签无统一枚举 | #4 |

### NOTE（14 项）

| # | 问题 | 建议 |
|---|------|------|
| 28 | guard 调用在状态转换之后 | 评估是否需要在状态转换前做预检查 |
| 29 | 非 Mapping invocation 重建丢失字段 | 在修复包 2 中统一 schema 时一并处理 |
| 30 | stream 路径不调用 guard_assert_single_decision | 在修复包 3 中补充 stream 路径 guard |
| 31 | _average_vectors 无维度校验 | 在修复包 4 中增加 embedding 健壮性 |
| 32 | receipt_utils.py 极简，合并/验证逻辑分散 | 在修复包 2 中收拢 |
| 33 | is_write_invocation 双重判定可能过于宽泛 | 在修复包 1 中统一后评估 |
| 34 | retry 模型覆盖机制无可用性验证 | 低优先级，增加配置入口时一并处理 |
| 35 | drain_speculative_tasks timeout 仅 0.2s | 评估是否需场景自适应 |
| 36 | extract_target_files_from_message 正则可能误匹配 | 增加单元测试覆盖边界 case |
| 37 | retry_llm_call_ordinal 越界后 clamp 到最后模型 | 低优先级，增加显式边界检查 |
| 38 | 测试缺少真实 embedding 集成 | 增加集成测试（可选） |
| 39 | requires_mutation_intent 与 requires_verification_intent 逻辑不对称 | 在修复包 4 中统一入口时评估 |
| 40 | hidden_continuation 实现有歧义 | 评估是否需要重构为直接计数 |
| 41 | guard 检查 ledger.decisions 而非当前 turn 实际决策数 | 在修复包 2 中统一 ledger 语义时评估 |

---

## 关联文档

- 审计执行报告：`memory/transaction_kernel_audit_20260419.md`（如已落盘）
- 目标架构标准：`docs/AGENT_ARCHITECTURE_STANDARD.md`
- 目标规范：`docs/FINAL_SPEC.md` §9.2（`len(TurnDecisions) == 1`, `len(ToolBatches) <= 1`, `hidden_continuation == 0`）
- 认知生命体对齐：`CLAUDE.md` §10
