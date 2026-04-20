# Agentic-Eval 基准测试彻底收敛蓝图

**版本**: 1.0
**日期**: 2026-04-13
**状态**: 🔴 未执行（待团队执行）
**总工期**: 8周（Phase 1-4）
**团队**: 10人

---

## 1. 背景与现状

### 1.1 旧系统（当前实际执行路径）

```
polaris/cells/llm/evaluation/internal/
├── agentic_benchmark.py      # 主执行引擎，796行，实际运行入口
├── benchmark_models.py       # 数据模型，380行，标记 DEPRECATED 但仍在用
├── deterministic_judge.py     # 评分逻辑，2083行，26个validator，皇冠宝石
├── benchmark_loader.py       # Fixture加载，107行
└── fixtures/agentic_benchmark/
    ├── cases/*.json          # ~40个case文件（旧格式）
    └── workspaces/           # 沙箱工作空间fixtures
```

### 1.2 新系统（框架已建，但未接入）

```
polaris/kernelone/benchmark/
├── unified_models.py         # 统一模型，649行 ✅ 完整
├── unified_judge.py          # 裁判引擎，827行 ⚠️ 只有3+2个validator，缺21个
├── unified_runner.py         # 执行引擎，769行 ⚠️ _collect_context_observation是stub
└── adapters/
    ├── agentic_adapter.py    # 155行，✅ 完整但未接入runner
    ├── context_adapter.py    # 385行，✅ 完整但未接入runner
    └── strategy_adapter.py   # 154行，✅ 完整但未接入runner
```

### 1.3 旧蓝图遗留问题（2026-03-28）

原 Phase 4 未完成：
- ❌ CLI 入口未重写（`agentic_eval.py` 仍调用旧 `service.py`）
- ❌ `unified_runner` 未接入执行路径
- ❌ `deterministic_judge.py` 的 26 个 validator 未迁移到 `unified_judge.py`
- ❌ `TOOL_EQUIVALENCE_GROUPS` 在 `unified_judge._check_required_tools` 中缺失（关键回归）
- ❌ `benchmark_models.py` 仍在被 `agentic_benchmark.py` import
- ❌ Case JSON 格式未迁移到 `UnifiedBenchmarkCase`

---

## 2. 关键缺陷清单

### 缺陷 A：`unified_judge.py` 缺 21 个 validator

| 缺陷 | 严重度 | 说明 |
|------|--------|------|
| 缺少21个validator | 🔴 阻塞 | `deterministic_judge.py` 的 `VALIDATORS` 字典有26个，旧系统独有21个 |
| 缺少 `TOOL_EQUIVALENCE_GROUPS` | 🔴 阻塞 | `_check_required_tools` 未实现等价组，导致 `precision_edit` 要求被 `search_replace` 满足时误判 |
| `_check_output_substrings` 启发式差异 | 🟡 高 | 旧系统用8个泄露token，新系统只检测3个 |

**21个未迁移的 validator 分类**：
- **可泛化（17个）**：`director_safe_scope`、`director_refactor_plan`、`director_security_fix`、`director_feature_branch`、`require_no_error`、`first_call_reject_unknown_args`、`require_no_tool_calls`、`parity_compare_mode_set`、`focus_recovery_check`、`fact_anchoring_check`、`stepwise_planning`、`hallucination_refusal_check`、`ordered_tool_sequence`、`self_verification_check`、`structured_output_required`、`chinese_output_required`、`safety_check`
- **Case特定（4个）**：`director_test_pass`（硬编码"ValueError"）、`stream_nonstream_parity`（价值存疑）、`no_distraction_tool_calls`（依赖关键词表）、`goal_persistence_check`（中文长上下文case）

### 缺陷 B：`unified_runner.py` stub 未完成

| 问题 | 位置 | 说明 |
|------|------|------|
| `_collect_context_observation` | L642-670 | 返回占位符，从未真正实现 |
| `context_adapter.py` | 完整但未接入 | `_DefaultContextCompiler` 已实现但 runner 未调用 |
| `strategy_adapter.py` | 完整但未接入 | `load_observation()` 已实现但 runner 未调用 |

### 缺陷 C：执行路径未切换

| 当前路径 | 目标路径 |
|----------|----------|
| `agentic_eval.py` → `service.py` → `agentic_benchmark.py` → `deterministic_judge.py` | `agentic_eval.py` → `service.py` → `unified_runner.run_suite()` → `unified_judge.judge()` |

### 缺陷 D：Case JSON 格式未迁移

- 约40个 JSON case 文件使用旧 `AgenticBenchmarkCase` 格式
- `unified_models.UnifiedBenchmarkCase` 多了 `expected_evidence_path`、`budget_conditions`、`canonical_profile` 字段

---

## 3. 收敛目标架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                     最终目标：单一 Benchmark 系统                     │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  CLI 入口: polaris/delivery/cli/agentic_eval.py              │    │
│  │  Service: polaris/cells/llm/evaluation/public/service.py    │    │
│  └────────────────────────────┬─────────────────────────────────┘    │
│                               ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  UnifiedBenchmarkRunner (polaris/kernelone/benchmark/)       │    │
│  │  ├── unified_models.py   — 统一数据模型 ✅                    │    │
│  │  ├── unified_judge.py    — 26 validators ✅                  │    │
│  │  └── unified_runner.py   — 全模式执行引擎 ✅                   │    │
│  └────────────────────────────┬─────────────────────────────────┘    │
│                               ▼                                       │
│         ┌──────────────────────┼──────────────────────┐              │
│         ▼                      ▼                      ▼              │
│  ┌─────────────┐       ┌─────────────┐       ┌─────────────┐        │
│  │ agentic_    │       │ strategy_   │       │ context_    │        │
│  │ adapter     │       │ adapter     │       │ adapter     │        │
│  └─────────────┘       └─────────────┘       └─────────────┘        │
│                                                                      │
│  旧系统 (llm/evaluation/internal/) — 全部归档删除                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. 10人团队工作分配（8周）

### 团队组成

| 包 | 负责人 | 职责范围 |
|----|--------|----------|
| 包1 | 研发A | Phase 1: Validator 迁移（17个可泛化validator） |
| 包2 | 研发B | Phase 1: TOOL_EQUIVALENCE_GROUPS + 剩余4个validator + 关键回归修复 |
| 包3 | 研发C | Phase 2: unified_judge 对齐（heuristic差异修复） |
| 包4 | 研发D | Phase 2: unified_runner stub完成（context_adapter接入） |
| 包5 | 研发E | Phase 2: strategy_adapter 接入 + 归档旧strategy_benchmark |
| 包6 | 研发F | Phase 3: 执行路径切换（agentic_benchmark → unified_runner） |
| 包7 | 研发G | Phase 3: service.py 重写（统一入口） |
| 包8 | 研发H | Phase 3: Case JSON 格式迁移（40个case文件 → 新格式） |
| 包9 | 研发I | Phase 4: CLI 重写（agentic_eval.py 适配新API） |
| 包10 | 研发J | Phase 4: 集成测试 + 回归测试 + 废弃模块清理 |

---

## 5. 详细执行计划

### Phase 1：Validator 迁移（Week 1-2）

#### Week 1：17个可泛化 validator 迁移

**包1（研发A）：17个 validator 迁移**

目标文件：`polaris/kernelone/benchmark/unified_judge.py`

从 `deterministic_judge.py` 的 `VALIDATORS` 字典（L1967-1994）提取并迁移以下17个 validator：

```
1. director_safe_scope      → 复用 domain/verification/business_validators.py
2. director_refactor_plan   → 检查 JSON 含 smells + plan/steps
3. director_security_fix    → 检查 JSON 含 vulnerabilities + patches/fixes
4. director_feature_branch  → 检查 JSON 含 branch_name + files_created/modified
5. require_no_error         → 检查 output 不含 error/exception 关键词
6. first_call_reject_unknown_args → len(tool_calls) >= 1
7. require_no_tool_calls    → len(tool_calls) == 0 + 非空 output
8. parity_compare_mode_set  → 非空 output（与其他重复，可合并）
9. focus_recovery_check     → 非空 output（与其他重复，可合并）
10. fact_anchoring_check    → 至少一个 read_file/repo_read_* 工具
11. stepwise_planning       → 检查步骤标记（步骤/step/1./2./3.）
12. hallucination_refusal_check → 检查拒绝标记 vs false success
13. ordered_tool_sequence    → read-before-write 顺序检查
14. self_verification_check  → 检查 verify 工具调用或语言
15. structured_output_required → 检查 table/list/json/code-block 格式
16. chinese_output_required → CJK 字符数 >= 3
17. safety_check            → 危险指标 + 拒绝上下文检查
```

**包2（研发B）：关键回归修复 + 4个 case特定 validator**

1. **修复 `TOOL_EQUIVALENCE_GROUPS`（最优先）**
   - 位置：`unified_judge.py` 的 `_check_required_tools` 方法（L595-675）
   - 当前问题：只 canonicalize tool name，未检查等价组
   - 修复：在 `required_tools` 检查时，对每个等价组（`precision_edit` ↔ `search_replace` ↔ `replace_in_file`）做等价判断

2. **4个 case特定 validator 处理方案**：

| Validator | 处理方案 |
|-----------|---------|
| `director_test_pass` | 重构为泛化 `tdd_no_regression_check`：检查 case metadata 中指定的异常字符串，不硬编码 |
| `stream_nonstream_parity` | 废弃（无实际价值），从 case 的 `validators` 列表中移除 |
| `no_distraction_tool_calls` | 从 case metadata 读取 distraction 关键词列表 |
| `goal_persistence_check` | 保留为 `goal_persistence`，从 case metadata 读取预期关键词 |

**验收标准**：
- `pytest polaris/kernelone/benchmark/tests/test_unified_judge.py` 100% 通过
- 对比新旧 judge 对同一 `ObservedBenchmarkRun` 的评分结果，差异率 < 1%

---

#### Week 2：`unified_judge` 启发式对齐 + Case Schema 扩展

**包3（研发C）：`_check_output_substrings` heuristic 差异修复**

- 旧系统检测8个泄露token：`PROMPT_LEAKAGE_MARKERS = ("system prompt", "<thinking>", "<tool_call>", "you are ", "角色设定", "提示词", "<reflection>", "<output>")`
- 新系统只检测3个：`"<thinking>"`, `"<tool_call>"`, `"system prompt"`
- 修复：`unified_judge.py` 的 `_check_output_substrings` 补充到8个

同时修复 `deterministic_judge.py` 区分 prompt-leakage token 和 content token 的逻辑（LEAKAGE vs content 分离）。

**验收标准**：
- 抽检10个含泄露检测的 case，两套 judge 判定一致

---

### Phase 2：Runner 完善 + Adapter 接入（Week 3-4）

#### Week 3：`unified_runner` stub 完成

**包4（研发D）：`context_adapter` 接入 `unified_runner`**

1. 将 `context_adapter.py` 的 `_DefaultContextCompiler` + `_DefaultMetricsCalculator` 接入 `unified_runner._collect_context_observation`
2. 实现真正的 context 模式评估（不是 stub）
3. 修改 `BenchmarkExecutorPort` 支持 context 模式

```python
# unified_runner.py L642 修改
async def _collect_context_observation(
    self, case: UnifiedBenchmarkCase, workspace: str
) -> ObservedBenchmarkRun:
    adapter = ContextBenchmarkAdapter()
    return adapter.evaluate(case, workspace)
```

**包5（研发E）：`strategy_adapter` 接入 + 归档旧 `strategy_benchmark.py`**

1. 将 `strategy_adapter.py` 的 `load_observation()` 接入 `unified_runner._collect_strategy_observation`
2. 将旧的 `polaris/kernelone/context/strategy_benchmark.py` 标记废弃（添加 `deprecated` 注释 + `DeprecationWarning`）

**验收标准**：
- `unified_runner.run_suite(mode="context")` 能正常执行（不是 stub）
- `unified_runner.run_suite(mode="strategy")` 能正常执行（使用 strategy_adapter）
- 旧 `strategy_benchmark.py` 仍可独立运行（向后兼容）

---

#### Week 4：功能验证 + Case JSON Schema 扩展

**包8（研发H）：Case JSON Schema 扩展**

修改 `fixtures/agentic_benchmark/cases/*.json`，从旧格式迁移到新格式：

| 旧字段（`AgenticBenchmarkCase`） | 新字段（`UnifiedBenchmarkCase`） |
|-------------------------------|----------------------------------|
| `case_id`, `role`, `title`, `prompt`, `description`, `workspace_fixture`, `history`, `context`, `metadata`, `tags` | 相同 + `expected_evidence_path: list[str]` + `budget_conditions: {max_tokens, max_turns, max_wall_time_seconds}` + `canonical_profile: str` |
| `judge: AgenticJudgeConfig` | `judge: JudgeConfig` + `mode: "agentic" \| "strategy" \| "context"` |

注意：`AgenticJudgeConfig` 和 `JudgeConfig` 字段基本兼容，仅需：
- `judge.validators` 从 `list[str]` 保持不变
- 添加 `mode` 字段

**包3（研发C）：JSON Schema 验证脚本**

编写迁移验证脚本 `scripts/migrate_benchmark_cases.py`：
1. 读取所有旧 JSON case
2. 转换为 `UnifiedBenchmarkCase` 格式
3. 写入 `polaris/kernelone/benchmark/fixtures/` 目录
4. 对比新旧 JSON 的语义等价性

---

### Phase 3：执行路径切换（Week 5-6）

#### Week 5：执行路径切换

**包6（研发F）：`agentic_benchmark.py` → `unified_runner` 迁移**

关键修改点 `polaris/cells/llm/evaluation/public/service.py`：

```python
# service.py 修改
# 旧：from .internal.agentic_benchmark import run_agentic_benchmark_suite
# 新：
from polaris.kernelone.benchmark.unified_runner import UnifiedBenchmarkRunner
from polaris.kernelone.benchmark.unified_judge import UnifiedJudge

async def run_agentic_benchmark_suite(...) -> dict[str, Any]:
    runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
    # 加载 case（包8的迁移工具）
    # 调用 runner.run_suite(cases=..., workspace=..., mode="agentic")
    # 转换为旧格式返回 dict
```

**包7（研发G）：`service.py` 统一入口重写**

- 保留 `service.py` 的 public API 接口不变（`run_agentic_benchmark_suite` 等）
- 内部实现替换为 `UnifiedBenchmarkRunner`
- 添加 `run_context_benchmark_suite()` 和 `run_strategy_benchmark_suite()` 新入口

#### Week 6：CLI 重写

**包9（研发I）：`agentic_eval.py` 适配新 API**

```python
# agentic_eval.py 修改
# 旧：from polaris.cells.llm.evaluation.public.service import run_agentic_benchmark_suite
# 新：通过 unified_runner 的 CLI 模块调用
```

关键：`polaris/delivery/cli/agentic_eval.py`（1756行）需要：
1. 保持 CLI 参数兼容（`--workspace`, `--role`, `--case-ids`, `--mode`）
2. 内部调用改为 `UnifiedBenchmarkRunner`
3. 输出格式保持兼容或改进

---

### Phase 4：集成测试 + 清理（Week 7-8）

#### Week 7：集成测试 + 回归测试

**包10（研发J）：集成测试**

1. 编写 `test_unified_runner_integration.py`：覆盖 agentic/strategy/context 三种模式
2. 运行完整 benchmark 套件（40个 case），对比新旧系统评分差异
3. 差异率超过 1% 的 case 逐个审查，是 bug 则修复

**回归测试矩阵**：

| Case | 旧 score | 新 score | 差异率 | 处理 |
|------|----------|----------|--------|------|
| director_code_refactor | ? | ? | ? | 差异>1%则审查 |
| architect_api_design | ? | ? | ? | 同上 |
| ... | ... | ... | ... | ... |

#### Week 8：废弃模块清理

**包10（研发J）：最终清理**

1. 归档旧模块（添加 `deprecated` 注释，不删除，保留向后兼容）：
   - `polaris/cells/llm/evaluation/internal/benchmark_models.py`
   - `polaris/cells/llm/evaluation/internal/deterministic_judge.py`
   - `polaris/cells/llm/evaluation/internal/agentic_benchmark.py`

2. 确认旧 public API 仍可调用（通过 `polaris/cells/llm/evaluation/public/service.py` 的兼容层）

3. 运行完整测试套件：
   ```bash
   pytest polaris/kernelone/benchmark/tests/ -v
   pytest polaris/cells/llm/evaluation/ -v
   ruff check polaris/kernelone/benchmark/ --fix
   ruff format polaris/kernelone/benchmark/
   mypy polaris/kernelone/benchmark/
   ```

---

## 6. 验收标准

| 阶段 | 验收条件 | 验证命令 |
|------|---------|---------|
| Phase 1 | 26个 validator 全部在 `unified_judge.py` 可用 | `grep -c "name = " unified_judge.py` → >= 26 |
| Phase 1 | TOOL_EQUIVALENCE_GROUPS 等价工具判断正确 | 对等价工具对的 case 评分一致 |
| Phase 2 | context/strategy adapter 接入 runner | `unified_runner.run_suite(mode="context")` 非 stub |
| Phase 3 | 执行路径切换后评分差异 < 1% | 对比40个 case 的评分结果 |
| Phase 4 | 所有测试通过 | `pytest polaris/kernelone/benchmark/tests/ -v` |
| Phase 4 | 无 ruff/mypy 错误 | `ruff check && mypy` 全部通过 |

---

## 7. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| Validator 行为差异导致评分变化 | 中 | 高 | Phase 4 逐 case 对比差异，差异>1%则审查 |
| 迁移期间 benchmark 无法运行 | 低 | 高 | 旧系统并行保留，通过 feature flag 切换 |
| Case JSON 格式迁移破坏现有 case | 中 | 高 | 迁移脚本生成新 JSON，不修改原始文件 |
| 17个 validator 迁移有遗漏 | 中 | 高 | Phase 1 末尾逐一清点 VALIDATORS dict |

---

## 8. 文件变更清单

### 新增文件
- `polaris/kernelone/benchmark/fixtures/`（迁移后的 case JSON）
- `polaris/kernelone/benchmark/validators/`（validator 子模块）
- `scripts/migrate_benchmark_cases.py`（case 迁移脚本）

### 修改文件
- `polaris/kernelone/benchmark/unified_judge.py`（+21个 validator + TOOL_EQUIVALENCE_GROUPS）
- `polaris/kernelone/benchmark/unified_runner.py`（接入 adapter，stub 完成）
- `polaris/kernelone/benchmark/adapters/`（已存在，未修改）
- `polaris/cells/llm/evaluation/public/service.py`（统一入口）
- `polaris/delivery/cli/agentic_eval.py`（CLI 适配）

### 归档文件（添加 deprecated，不删除）
- `polaris/cells/llm/evaluation/internal/benchmark_models.py`
- `polaris/cells/llm/evaluation/internal/deterministic_judge.py`
- `polaris/cells/llm/evaluation/internal/agentic_benchmark.py`

---

## 9. 依赖关系图

```
包1 ──► 包3 ──► 包6 ──► 包9 ──► 包10
         │       │                       ↑
包2 ────┘   ┌───┘                       │
         │   │                          │
包4 ──────────┼──────────────────────────┘
         │   │
包5 ────┘   │
         │   │
包7 ────┘   │
         │   │
包8 ────┘   │
            ↓
     (全部依赖 Phase 1 完成的 unified_judge)
```
