# Fitness Rules Audit Report

**Date**: 2026-04-24
**Source**: `src/backend/docs/governance/ci/fitness-rules.yaml`
**Total Rules**: 61

## 当前状态分布

| 状态 | 数量 | 占比 |
|------|------|------|
| enforced | 9 | 14.8% |
| enforced_non_regressive | 9 | 14.8% |
| seeded | 22 | 36.1% |
| draft | 20 | 32.8% |
| partially_enforced | 1 | 1.6% |

## 优先级规则审计

### 1. `no_cross_cell_internal_import`

- **当前状态**: `enforced_non_regressive` (Blocker)
- **建议**: **保持现状**
- **理由**: 已通过 catalog governance gate 的 `fail-on-new` 模式执行，`run_catalog_governance_gate.py` 已集成此检查。升级为 `enforced` 需先验证零误报历史。

### 2. `explicit_utf8_text_io`

- **当前状态**: `partially_enforced` (Blocker)
- **建议**: **升级为 `enforced`**
- **理由**: AGENTS.md 已将 UTF-8 显式声明为铁律（§4.9）。现有 `test_storage_layout.py` 和 `test_imports.py` 提供部分覆盖。
- **前置条件**:
  - 需实现 `desired_automation` 中的静态搜索工具：检测 `open()` 调用缺少 `encoding="utf-8"` 的情况
  - 建议使用 `ruff` 规则 `unspecified-encoding` (UP015) 或自定义 lint
  - 扫描范围：`polaris/` 全目录
- **风险**: 若当前代码库存在大量未指定 encoding 的遗留调用，直接 enforced 会阻断 CI

### 3. `manifest_schema_valid`

- **当前状态**: `enforced_non_regressive` (Blocker)
- **建议**: **保持现状**
- **理由**: 已通过 catalog governance gate 执行 schema 验证。`fail-on-new` 模式已在 CI 中激活。

### 4. `kernelone_release_suite_green`

- **当前状态**: `seeded` (Blocker)
- **建议**: **暂不升级，待测试套件稳定**
- **理由**: 当前 `pytest --collect-only` 显示 62 errors（2026-04-24 快照）。测试收集阶段尚有导入错误，`--mode tests` 执行会直接失败。
- **前置条件**:
  1. 修复 62 个收集错误
  2. `run_kernelone_release_gate.py --mode collect` 返回 0
  3. `run_kernelone_release_gate.py --mode tests` 全绿
- **风险**: 当前强制执行会阻断所有 CI

### 5. `agent_instruction_snapshot_consistent`

- **当前状态**: `seeded` (High)
- **存在性确认**: ✅ 规则已存在（fitness-rules.yaml 第 367-380 行）
- **建议**: **升级为 `enforced`**
- **理由**: AGENTS.md §16.4 已明确要求三文件（AGENTS.md / CLAUDE.md / GEMINI.md）快照事实一致。测试用例 `test_agent_instruction_snapshot_is_consistent` 已定义。
- **前置条件**: 验证测试实际通过

## 建议激活的规则

| 规则名 | 当前状态 | 建议状态 | 理由 |
|--------|---------|---------|------|
| `explicit_utf8_text_io` | partially_enforced | enforced | 铁律已声明，需补齐自动化 lint |
| `agent_instruction_snapshot_consistent` | seeded | enforced | 三文件同步已为强制要求，测试已定义 |
| `kernelone_release_collect_clean` | seeded | enforced_non_regressive | collect-only 检查低风险，可先于 test 执行 |
| `delivery_cli_import_hygiene` | seeded | enforced_non_regressive | 测试已存在，导入卫生检查低风险 |
| `debt_register_schema_valid` | seeded | enforced_non_regressive | 治理资产完整性检查，schema 已定义 |
| `verify_pack_schema_valid` | seeded | enforced_non_regressive | Cell verify pack 结构检查，测试已定义 |
| `migration_preflight_required` | seeded | enforced_non_regressive | 预检套件已存在，防止无预检直接写入 |

## 建议保持现状的规则

| 规则名 | 当前状态 | 理由 |
|--------|---------|------|
| `no_cross_cell_internal_import` | enforced_non_regressive | 已通过 catalog gate 执行，稳定运行中 |
| `manifest_schema_valid` | enforced_non_regressive | schema 验证已在 CI 中激活 |
| `declared_cell_dependencies_match_imports` | enforced_non_regressive | catalog gate 已集成 |
| `owned_paths_do_not_overlap` | enforced_non_regressive | catalog gate 已集成 |
| `single_state_owner` | enforced_non_regressive | catalog gate 已集成 |
| `undeclared_effects_forbidden` | enforced_non_regressive | catalog gate 已集成 |
| `critical_subgraph_has_verify_targets` | enforced_non_regressive | catalog gate 已集成 |
| `manifest_catalog_consistency` | enforced_non_regressive | mismatch baseline 已冻结历史差异 |
| `kernelone_release_suite_green` | seeded | 测试收集尚有 62 errors，强制执行会阻断 CI |
| `context_pack_is_primary_ai_entry` | draft | 描述符覆盖已达 54/54，但自动化未就绪 |
| `graph_constrained_semantic_retrieval` | draft | 架构原则已定义，但运行时检查未实现 |
| `contract_change_requires_review` | draft | 兼容性审查流程未建立 |
| `migration_units_do_not_conflict` | draft | 迁移 ledger 自动化检查未就绪 |
| `catalog_missing_units_cannot_advance` | draft | 依赖 catalog 状态完整性 |
| `shim_only_units_require_markers` | draft | legacy header 审计脚本未就绪 |
| `legacy_file_coverage_audit` | draft | 覆盖率审计脚本未就绪 |
| `verified_or_retired_units_require_evidence` | draft | 验证数据要求未自动化 |
| `migration_gaps_are_explicit` | seeded | 迁移期合理，待 Cell 映射完成 |
| `semantic_descriptor_schema_valid` | seeded | 依赖 context.catalog 成熟度 |
| `semantic_descriptor_freshness` | seeded | 依赖 index_state 追踪机制 |
| `CELL_KERNELONE_*` (01-08) | draft | Cells→KernelOne 集成蓝图阶段，实现未完成 |
| `events_fact_stream_singleton_writer` | draft | 单一写入者语义需 catalog 验证 |
| `no_direct_role_call` | draft | EDA task_market 模式未完全落地 |
| `task_market_is_single_business_broker` | draft | 依赖 runtime.task_market 成熟度 |
| `outbox_atomic` | seeded | 原子性已实现但需更多回归测试 |
| `director_requires_blueprint` | draft | blueprint 检查逻辑未实现 |
| `EXC-001` | seeded | ruff BLE 规则可直接执行，但需清理现有违规 |
| `EXC-002` | seeded | ruff SIM105 规则可直接执行，但需清理现有违规 |

## 建议降级的规则

无

## 执行路径建议

### Phase 1: 低风险升级（立即可执行）

1. `kernelone_release_collect_clean` → `enforced_non_regressive`
2. `delivery_cli_import_hygiene` → `enforced_non_regressive`
3. `debt_register_schema_valid` → `enforced_non_regressive`
4. `verify_pack_schema_valid` → `enforced_non_regressive`

**验证命令**:
```bash
python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode collect
python -m pytest -q tests/architecture/test_delivery_cli_import_hygiene.py
python -m pytest -q tests/architecture/test_structural_bug_governance_assets.py
```

### Phase 2: 中等风险升级（需验证）

5. `agent_instruction_snapshot_consistent` → `enforced`
6. `migration_preflight_required` → `enforced_non_regressive`

**验证命令**:
```bash
python -m pytest -q tests/architecture/test_kernelone_release_gates.py::test_agent_instruction_snapshot_is_consistent
python docs/migration/scripts/migration_preflight_suite.py
```

### Phase 3: 高风险升级（需实现自动化）

7. `explicit_utf8_text_io` → `enforced`
   - 需实现静态分析工具或集成 ruff UP015
   - 需先清理现有违规

8. `kernelone_release_suite_green` → `enforced`
   - 需先修复 62 个测试收集错误
   - 需确保 test suite 全绿

---

**审计结论**: 61 条规则中，7 条建议立即升级，4 条建议待自动化就绪后升级，其余保持现状。当前 enforced + enforced_non_regressive 比例为 29.5%，建议在 2026-Q2 提升至 50%+。

---

## 性能基准建立状态

**日期**: 2026-04-24
**状态**: Infrastructure Created

### 已创建基础设施

| 组件 | 路径 | 状态 |
|------|------|------|
| 基准测试配置 | `src/backend/tests/benchmark/conftest.py` | Created |
| 延迟基准测试 | `src/backend/tests/benchmark/test_latency_baseline.py` | Created |
| 基准数据目录 | `src/backend/tests/benchmark/baselines/` | Created |
| 基准数据说明 | `src/backend/tests/benchmark/baselines/README.md` | Created |
| 示例基线文件 | `src/backend/tests/benchmark/baselines/sample_baseline.json` | Created |

### 覆盖的基准测试项

1. **TurnTransactionController**
   - 控制器初始化延迟
   - 单次 turn 执行延迟
   - 状态机转换延迟
   - Ledger 记录操作延迟

2. **LLM Provider (Mock 模式)**
   - Mock provider 调用延迟
   - 流式响应延迟

3. **ContextOS**
   - 上下文读取延迟
   - 上下文写入延迟
   - JSON 序列化延迟

### 性能阈值定义

```python
class LatencyThresholds:
    TURN_EXECUTE_P50_MS = 100.0
    TURN_EXECUTE_P95_MS = 500.0
    LLM_PROVIDER_P95_MS = 200.0
    CONTEXT_OS_READ_P95_MS = 50.0
    CONTEXT_OS_WRITE_P95_MS = 100.0
```

### 执行命令

```bash
# 运行基准测试
python -m pytest src/backend/tests/benchmark/ -v

# 运行完整套件并保存基线
python -m pytest src/backend/tests/benchmark/test_latency_baseline.py::test_full_benchmark_suite -v
```

### 后续计划

- [ ] 集成到 CI pipeline（可选 gate）
- [ ] 添加历史趋势追踪
- [ ] 扩展覆盖至 ExplorationWorkflowRuntime
- [ ] 添加内存使用基准
