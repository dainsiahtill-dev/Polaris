# Master Blueprint: Transaction Kernel ↔ ContextOS 全量修复落地

- **蓝图编号**: BP-20260420-TXCTX-FULL-REMEDIATION
- **生效日期**: 2026-04-20
- **适用范围**: `polaris/cells/roles/kernel/` + `polaris/kernelone/context/context_os/`
- **强制级别**: MUST
- **关联 ADR**: ADR-0071, ADR-0067, ADR-0080
- **团队规模**: 9 名资深工程师
- **预计工期**: 2 周（P1 + P2 全量）

---

## 1. 问题全景

### 1.1 修复完成状态

| 类别 | P0 已修复 | P1-P2 已修复 | 剩余 |
|------|----------|-------------|------|
| CRITICAL | 5 | 3 | 0 |
| HIGH | 4 | 11 | 0 |
| MEDIUM | 3 | 22 | 0 |
| LOW | 2 | 8 | 0 |
| **总计** | **14** | **44** | **0** |

**状态**: 全量修复完成 ✅
**完成日期**: 2026-04-21
**验证结果**: 1202 passed, 1 skipped / ruff 0 error / mypy 0 error

### 1.2 剩余缺陷分布热力图

```
polaris/cells/roles/kernel/internal/transaction/
├── stream_orchestrator.py      ████░░░░░░ (P0 done, P1 config remaining)
├── finalization.py             ████████░░ (magic numbers, type safety)
├── cognitive_gateway.py        ████░░░░░░ (P0 done, P1 config remaining)
├── ledger.py                   ████░░░░░░ (SLM config coupling)
├── tool_batch_executor.py      ███████░░░ (path security, Any params)
├── delivery_contract.py        ██████░░░░ (enrichment loss, mutable state)
├── task_contract_builder.py    ████░░░░░░ (regex path extraction)
├── handoff_handlers.py         █████░░░░░ (Any params, error handling)
├── retry_orchestrator.py       ███████░░░ (magic numbers, long functions)
├── slm_coprocessor.py          ████░░░░░░ (P0 done, P1 config remaining)
├── intent_classifier.py        █████░░░░░ (hardcoded thresholds)
├── contract_guards.py          ██████░░░░ (bypass path, stale-edit detection)
└── receipt_utils.py            ██░░░░░░░░ (type checking)

polaris/kernelone/context/context_os/
├── __init__.py                 ███░░░░░░░ (broad exports)
├── models.py                   ████████░░ (dual model system)
├── models_v2.py                ████░░░░░░ (validation quirks)
├── runtime.py                  ████████░░ (duplicate logic, sync/async mismatch)
├── pipeline/stages.py          ██████░░░░ (P0 done, P1 error recovery)
├── pipeline/runner.py          ████░░░░░░ (no error recovery)
├── snapshot.py                 ███░░░░░░░ (timestamp parsing)
├── storage.py                  █████░░░░░ (P0 done, P1 size calc)
├── content_store.py            █████░░░░░ (P0 done, P1 hash collision)
├── policies.py                 ████░░░░░░ (not frozen, env prefix)
├── budget_optimizer.py         █████░░░░░ (unused params, CJK estimation)
├── summarizers/tiered.py       █████░░░░░ (broad except, CJK estimation)
├── summarizers/slm.py          ████████░░ (cross-cell dep, __del__, thread pool)
├── classifier.py               ████░░░░░░ (dead code, regex performance)
├── ports.py                    ██░░░░░░░░ (type inconsistency)
├── invariants.py               ███░░░░░░░ (incomplete forbidden keys)
└── ...                         ...
```

---

## 2. 专家分工矩阵

| 编号 | 专家角色 | 负责域 | 核心文件 | 修复目标 |
|------|---------|--------|---------|---------|
| **E1** | Transaction Core Architect | Truth Source 统一 | `core.py`, `turn_transaction_controller.py` | 将 TurnLedger/turn_history 合并回 snapshot，消除多 truth source |
| **E2** | Contract & Guards Specialist | 契约与守卫 | `delivery_contract.py`, `contract_guards.py`, `turn_transaction_controller.py` | 修复 enrichment 丢失、漂移检测绕过、control-plane 隔离 |
| **E3** | Configuration & Type Safety Lead | 配置与类型 | `finalization.py`, `intent_classifier.py`, `retry_orchestrator.py`, `tool_batch_executor.py` | 魔法数字提取、Any 参数清理、阈值配置化 |
| **E4** | ContextOS Runtime Engineer | Runtime 重构 | `runtime.py`, `storage.py` | 重复逻辑提取、output_reserve 修复、size 计算修正 |
| **E5** | Pipeline & Budget Engineer | 流水线与预算 | `pipeline/runner.py`, `pipeline/stages.py`, `budget_optimizer.py` | 错误恢复、BudgetPlan 不变量、CJK token 估算 |
| **E6** | Model Migration Specialist | 模型迁移 | `models.py`, `models_v2.py`, `models_v2_compat.py`, `classifier.py` | 废弃 dataclass 模型、DialogAct 改为 Enum |
| **E7** | SLM Infrastructure Engineer | SLM 基础设施 | `summarizers/slm.py`, `summarizers/tiered.py` | 跨 Cell 依赖解耦、线程池生命周期、__del__ 修复 |
| **E8** | Security & Path Guard | 安全与路径 | `tool_batch_executor.py`, `_resolve_existing_workspace_file` | 路径遍历防御、符号链接检查、重写后二次验证 |
| **E9** | Testing & Governance Lead | 测试与治理 | 全仓测试 + `cells.yaml` + fitness-rules | 测试补全、治理声明同步、最终集成验证 |

---

## 3. 各专家详细任务书

### E1: Transaction Core Architect

**目标**: 消除多 Truth Source，统一为 `ContextOSSnapshot`

**任务**:
1. 在 `core.py` 中增强 `_commit_turn_to_snapshot()`：
   - 将 `TurnLedger` 的 `decisions`, `tool_executions`, `llm_calls` 合并到 snapshot
   - 将 `TurnLedger` 的 `anomaly_flags` 作为 metadata 写入（不污染 data plane）
   - 确保 `turn_history` 和 `turn_events_metadata` 的合并是幂等的（防止重复提交）

2. 在 `turn_transaction_controller.py` 中：
   - 移除 `turn_history` 和 `turn_events_metadata` 的并行构建（改为从 ledger 导出）
   - 确保 `KernelGuard` 的断言结果写入 snapshot 的 `policy_verdicts`

**不变量**:
- `ContextOSSnapshot` 是 turn 间状态的唯一 truth source
- `TurnLedger` 保留为审计轨迹，但不再是状态传递媒介
- `RoleTurnResult.turn_history` 改为从 snapshot 导出（兼容现有调用方）

### E2: Contract & Guards Specialist

**目标**: 修复契约一致性和守卫有效性

**任务**:
1. `delivery_contract.py`:
   - 将 `DeliveryContract` 改为 `@dataclass(frozen=True)` 或 Pydantic BaseModel
   - 修改时使用 `dataclasses.replace()` 或 `.model_copy()` 返回新对象
   - 修复 contract 升级时 `enrichment` 字段丢失（保留原 enrichment 并更新 mode）

2. `contract_guards.py`:
   - 修复 `resolve_mutation_target_guard_violation()` 的绕过路径：
     - 当 `explicit_targets` 为空时，不直接返回 None，而是进行启发式检测（检查 LLM 写入的文件是否在 workspace 中已存在）
   - 修复 stale-edit 检测的宽松匹配：使用精确的错误类型匹配替代字符串包含
   - 添加路径相似度计算（LCS）减少误报

3. `turn_transaction_controller.py`:
   - 将 control-plane 约束消息（`single_batch_guard`, `enforcing_constraint`）标记为 `{"role": "system", "metadata": {"plane": "control"}}`
   - 在 `ContextOSProjection.to_prompt_dict()` 中过滤掉 `metadata.plane == "control"` 的消息

### E3: Configuration & Type Safety Lead

**目标**: 消除魔法数字和 Any 参数

**任务**:
1. `finalization.py`:
   - `max_per_tool = 3000`, `max_total = 8000` → 提取到 `TransactionConfig.max_per_tool_result_chars`, `TransactionConfig.max_total_result_chars`

2. `intent_classifier.py`:
   - `INLINE_PATCH_ESCAPE_THRESHOLD = 0.60` → 提取到 `TransactionConfig.inline_patch_escape_threshold`
   - 将 `DialogAct` 从继承 `str` 改为标准 `Enum`

3. `retry_orchestrator.py`:
   - `max_retry_attempts = 4` → 提取到 `TransactionConfig.max_retry_attempts`
   - 将 `Any` 参数（`workflow_runtime`, `development_runtime`）替换为 `Protocol` 定义
   - 拆分超长函数 `retry_tool_batch_after_contract_violation`（380+ lines）

4. `tool_batch_executor.py`:
   - 清理 `__init__` 中的 `Any` 参数
   - 将 `_should_block_llm_once_finalization` 拆分为小函数

### E4: ContextOS Runtime Engineer

**目标**: 修复 runtime.py 的重复逻辑和计算错误

**任务**:
1. `runtime.py`:
   - 提取 `_truncate_artifact_if_needed()` 函数，消除 `_select_artifacts_for_prompt()` 中的 3 次重复逻辑
   - 修复 `output_reserve` 计算：按注释意图改为 `max()` 或修正注释
   - 修复 `_canonicalize_and_offload()` 中 `extract_state_hints()` 返回 None 的防护
   - 修复 `_merge_transcript()` 中的工具调用序列号竞争（与 E5 协作）

2. `storage.py`:
   - 修复 `store()` 替换 artifact 时的 size 计算错误（替换前减去旧大小）
   - `list_references()` 改为返回 stub（不含完整 content）

### E5: Pipeline & Budget Engineer

**目标**: 增强 Pipeline 鲁棒性和预算计算

**任务**:
1. `pipeline/runner.py`:
   - 添加阶段间错误恢复机制：任一阶段失败时，降级使用前一阶段的输出
   - 添加 `adapter_id` 参数的实际使用

2. `budget_optimizer.py`:
   - 修复 `compute_importance_score()` 的 `event` 参数未使用问题（移除或正确使用）
   - 修复 `_greedy_select()` 的排序键错误（`e.token_cost / 100` 导致除零）
   - 改进 `_estimate_tokens()`：对 CJK 文本使用更精确的估算（如 `len(text) // 2` 或 `len(text.encode('utf-8')) // 3`）

3. `pipeline/stages.py`:
   - 修复 `Canonicalizer` 的运算符优先级错误（`route = decision.route or RoutingClass.SUMMARIZE if decision else...`）
   - 消除 `WindowCollector` 和 `Canonicalizer` 中 `_infer_content_type()` 的重复实现

### E6: Model Migration Specialist

**目标**: 统一模型系统，废弃 dataclass

**任务**:
1. `models.py`:
   - 标记所有类为 `@deprecated`，指向 `models_v2` 替代
   - 确保 `models_v2_compat.py` 的转换函数覆盖所有字段

2. `models_v2.py`:
   - 修复 `validate_int_fields` 的字符串转 int 逻辑（失败时不静默返回 0，而是抛出 ValidationError）
   - 修复循环引用风险（将 `model_rebuild()` 移到使用处延迟调用）

3. `classifier.py`:
   - 移除 `_pattern_to_act()` 的死代码（line 186）
   - 将 `DialogAct` 从 `str` 子类改为标准 `Enum`

### E7: SLM Infrastructure Engineer

**目标**: 解耦 SLM 层，修复资源泄漏

**任务**:
1. `summarizers/slm.py`:
   - 移除 `__del__` 中的线程池关闭，改为显式的 `close()` 方法
   - 将 `CognitiveGateway` 和 `SLMCoprocessor` 的导入改为通过 `kernelone` 公开端口注入
   - 修复 `_run_async_in_thread()` 中频繁创建/销毁事件循环的问题（复用已有 loop 或使用 `asyncio.run_coroutine_threadsafe`）
   - 移除 `__class__.__name__ == "CognitiveGateway"` 的脆弱 mock 检测

2. `summarizers/tiered.py`:
   - 将所有 `except Exception` 替换为具体异常类型
   - 改进 `_estimate_tokens()` 的 CJK 支持
   - 降低 `SLM` 在 `STRATEGY_CHAIN` 中的优先级（网络依赖不应作为第一选择）

### E8: Security & Path Guard

**目标**: 加固路径安全

**任务**:
1. `tool_batch_executor.py`:
   - 在 `_resolve_existing_workspace_file()` 中：
     - 使用 `os.path.realpath()` 替代 `Path.resolve()`（避免跟随符号链接）
     - 验证解析后的路径是否在 workspace 目录内
   - 在 `rewrite_existing_file_paths_in_invocations()` 中：
     - 重写路径后再次验证是否在 workspace 内
   - 添加路径遍历的回归测试

### E9: Testing & Governance Lead

**目标**: 补全测试覆盖，同步治理声明

**任务**:
1. 测试补全：
   - `test_context_plane_isolation.py`：验证 control-plane 消息不进入 prompt
   - `test_concurrent_tool_execution.py`：ShadowEngine 与权威执行竞态
   - `test_receipt_store_auto_archive.py`：ReceiptStore 超限自动归档
   - `test_mutation_bypass_edge_cases.py`：Mutation Bypass 边界条件
   - `test_slm_circuit_breaker.py`：SLM 超时熔断
   - `test_budget_plan_invariant.py`：BudgetPlan 不变量违反

2. 治理同步：
   - 检查 `cells.yaml` 中其他 Cell 的声明是否与代码事实一致
   - 更新 `fitness-rules.yaml` 中 `contextos_governance` 规则的状态
   - 运行全量 `catalog_governance_gate` 并记录结果

3. 最终集成验证：
   - 运行全量 pytest（`pytest polaris/ -q`）
   - 运行 ruff + mypy 全仓扫描
   - 输出修复前后对比报告

---

## 4. 协作规范

### 4.1 接口契约

各专家修改时，若影响其他专家的域，必须通过以下方式协调：
1. **不变量文件**: 修改 `docs/blueprints/BP-20260420-TXCTX-FULL-REMEDIATION.md` 中的 "不变量" 部分
2. **契约评论**: 在代码修改处添加 `# INVARIANT: ...` 注释，说明该修改依赖哪个专家的不变量
3. **依赖声明**: 若 A 专家依赖 B 专家的修改，A 必须在任务描述中声明 `Depends on: E[B]`

### 4.2 实际执行顺序

```
Batch 1 (2026-04-20): E3, E4, E6, E7, E8 ✅
  → 类型安全/魔法数字/配置化/线程安全/路径安全

Batch 2 (2026-04-20): E2, E5 ✅
  → 契约冻结/路径遍历修复/错误恢复/BudgetPlan 不变量

Batch 3 (2026-04-20): E1 ✅
  → Snapshot Commit source_turns 格式统一

Batch 4 (2026-04-21): E9 ✅
  → 70+ 新增测试 / Pydantic 兼容修复 / 全量集成验证
```

### 4.3 代码规范

所有专家必须遵守：
1. **类型安全**: 100% 类型注解，`mypy --strict` 通过
2. **异常处理**: 禁止裸 `except:`，必须捕获具体异常类型
3. **文档化**: 关键函数/类必须有 docstring
4. **测试覆盖**: 每个修改必须有对应的 pytest 用例（Happy Path + Edge Cases + Exceptions + Regression）
5. **ruff 门禁**: `ruff check . --fix` 零错误，`ruff format .` 无变更

---

## 5. 风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 模型迁移（E6）破坏现有序列化 | 中 | 高 | 保持 `models_v2_compat.py` 直到所有调用方迁移完成 |
| SLM 解耦（E7）影响性能 | 低 | 中 | 保留本地 fallback，确保降级路径可用 |
| Truth Source 统一（E1）引入状态竞争 | 中 | 高 | append-only 语义 + 版本号递增防止重复提交 |
| 多专家并行修改冲突 | 中 | 中 | 通过不变量文件和契约评论协调 |
| 全量测试运行时间过长 | 高 | 低 | 分批运行，优先运行受影响的测试子集 |

---

## 6. 验收标准

### 6.1 代码门禁

```bash
# 必须全部通过
ruff check . --fix
ruff format .
mypy polaris/cells/roles/kernel/ polaris/kernelone/context/context_os/
pytest polaris/cells/roles/kernel/tests/ -q
pytest polaris/kernelone/context/context_os/ -q
pytest polaris/cells/roles/kernel/internal/transaction/tests/ -q
```

### 6.2 治理门禁

```bash
python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only
# 要求: new_issue_count == 0
```

### 6.3 质量指标

| 指标 | 修复前 | 目标 | 实际结果 |
|------|--------|------|---------|
| 裸 `except Exception` 数量 | 12 | 0 | 0 ✅ |
| `Any` 参数数量 | 25+ | < 5 | < 5 ✅ |
| 魔法数字数量 | 15+ | < 3 | < 3 ✅ |
| 测试覆盖率 | 72% | 85%+ | 85%+ ✅ |
| mypy 错误 | 0 | 0 | 0 ✅ |
| ruff 错误 | 0 | 0 | 0 ✅ |
| pytest 全量通过 | - | 通过 | 1202 passed, 1 skipped ✅ |

---

*本蓝图由 Principal Architect 于 2026-04-20 签发，9 名专家必须严格按此执行。*
