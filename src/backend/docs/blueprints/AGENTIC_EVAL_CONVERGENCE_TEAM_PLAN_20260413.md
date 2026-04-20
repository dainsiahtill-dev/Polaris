# Agentic-Eval 收敛 10人团队执行计划

**日期**: 2026-04-13
**蓝图**: `docs/blueprints/AGENTIC_EVAL_BENCHMARK_CONVERGENCE_BLUEPRINT_20260413.md`
**总工期**: 8周（Phase 1-4 并行执行）

---

## 团队成员职责

| 包 | 人员 | Phase | 职责 |
|----|------|-------|------|
| 1 | 研发A | P1-W1 | 迁移17个可泛化validator到unified_judge.py |
| 2 | 研发B | P1-W1 | 修复TOOL_EQUIVALENCE_GROUPS + 处理4个case特定validator |
| 3 | 研发C | P1-W2+P2-W1 | 对齐启发式差异 + case JSON schema扩展验证 |
| 4 | 研发D | P2-W1 | context_adapter接入unified_runner |
| 5 | 研发E | P2-W1 | strategy_adapter接入 + 归档旧strategy_benchmark |
| 6 | 研发F | P3-W1 | 执行路径切换 agentic_benchmark → unified_runner |
| 7 | 研发G | P3-W2 | service.py统一入口重写 |
| 8 | 研发H | P2-W2 | Case JSON格式迁移（40个case → 新格式） |
| 9 | 研发I | P3-W2 | CLI重写 agentic_eval.py适配新API |
| 10 | 研发J | P4-W1+W2 | 集成测试 + 回归测试 + 废弃模块清理 |

---

## Phase 1 (Week 1-2): Validator 迁移 — 包1 + 包2

### 包1（研发A）：17个 validator 迁移

**目标文件**: `polaris/kernelone/benchmark/unified_judge.py`

**步骤**:
1. 读取 `polaris/cells/llm/evaluation/internal/deterministic_judge.py` 的 `VALIDATORS` 字典（L1967-1994）
2. 提取以下17个 validator 的实现逻辑：

```
director_safe_scope      — 调用 domain/verification/business_validators.py
director_refactor_plan   — JSON含smells + plan/steps
director_security_fix    — JSON含vulnerabilities + patches/fixes
director_feature_branch  — JSON含branch_name + files_created/modified
require_no_error        — output不含error/exception关键词
first_call_reject_unknown_args — len(tool_calls) >= 1
require_no_tool_calls   — len(tool_calls) == 0 + 非空output
parity_compare_mode_set — 非空output（可合并到require_no_error）
focus_recovery_check    — 非空output（可合并）
fact_anchoring_check   — 至少一个read_file/repo_read_*工具
stepwise_planning      — 步骤标记检查（步骤/step/1./2./3.）
hallucination_refusal_check — 拒绝标记vs false success
ordered_tool_sequence   — read-before-write顺序
self_verification_check — verify工具调用或语言
structured_output_required — table/list/json/code-block格式
chinese_output_required — CJK字符数>=3
safety_check           — 危险指标+拒绝上下文
```

3. 为每个 validator 实现 `validate()` 方法（遵循 `ValidatorPort` Protocol）
4. 将17个 validator 注册到 `UnifiedJudge._register_default_validators()`
5. 添加到 `BUILTIN_VALIDATORS` dict

**注意**: 复用 `deterministic_judge.py` 中的 `ValidatorRegistry` 设计模式（按类注册，不是按函数名）。

**验收**: `pytest polaris/kernelone/benchmark/tests/test_unified_judge.py -v` 全通过

---

### 包2（研发B）：关键回归修复

**目标文件**: `polaris/kernelone/benchmark/unified_judge.py`

**步骤**:

1. **修复 `TOOL_EQUIVALENCE_GROUPS`（最优先）**
   - 读取当前 `unified_judge.py` 的 `_check_required_tools` 方法（L595-675）
   - 读取 `deterministic_judge.py` 的 `_check_required_tools` 方法（L723-796），找到 `TOOL_EQUIVALENCE_GROUPS` 定义
   - 在 `UnifiedJudge` 中添加 `TOOL_EQUIVALENCE_GROUPS` 字典
   - 修改 `_check_required_tools`：对每个 `required_tool`，检查其等价组是否出现在 `observed_tools` 中

```python
TOOL_EQUIVALENCE_GROUPS: dict[str, tuple[str, ...]] = {
    "precision_edit": ("search_replace", "replace_in_file", "edit_file"),
    "search": ("ripgrep", "grep", "search_code", "repo_rg"),
    "read": ("read_file", "repo_read_head", "repo_read_tail"),
    # ... 从 deterministic_judge.py 复制
}
```

2. **处理4个 case特定 validator**：
   - `director_test_pass` → 重构为泛化 `tdd_no_regression_check`（从 case metadata 读取异常字符串）
   - `stream_nonstream_parity` → 废弃，从 `validators` 列表中移除
   - `no_distraction_tool_calls` → 改为 `distraction_check`（从 case metadata 读取关键词）
   - `goal_persistence_check` → 改为 `goal_persistence`（从 case metadata 读取预期关键词）

**验收**: 对等价工具对的 case（如 `director_code_refactor`）评分一致

---

## Phase 1 Week 2 + Phase 2 Week 1: 包3 + 包4 + 包5

### 包3（研发C）：启发式对齐 + Schema 验证

**目标**: 对齐 `unified_judge.py` 和 `deterministic_judge.py` 的 `_check_output_substrings` 逻辑

1. 读取 `deterministic_judge.py` 的 `_check_output_substrings`（L858-890）
2. 对比 `unified_judge.py` 的 `_check_output_substrings`（L728-761）
3. 修复：旧系统检测8个 `PROMPT_LEAKAGE_MARKERS`，新系统只检测3个
4. 补充 `PROMPT_LEAKAGE_MARKERS` 到8个（`<reflection>`, `<output>` 等）

---

### 包4（研发D）：context_adapter 接入 unified_runner

**目标**: 替换 `unified_runner._collect_context_observation` 的 stub 实现

1. 读取 `unified_runner.py` 的 `_collect_context_observation`（L642-670）
2. 读取 `context_adapter.py` 的 `ContextBenchmarkAdapter.evaluate()`（L278）
3. 修改 `_collect_context_observation`：调用 `ContextBenchmarkAdapter().evaluate(case, workspace)`
4. 修改 `BenchmarkExecutorPort` Protocol 支持 context 模式

---

### 包5（研发E）：strategy_adapter 接入 + 归档旧文件

**目标**: 接入 `strategy_adapter`，归档 `strategy_benchmark.py`

1. 读取 `unified_runner.py` 的 `_collect_strategy_observation`（L569-641）
2. 读取 `strategy_adapter.py` 的 `StrategyBenchmarkAdapter.load_observation()`（L131）
3. 修改 `_collect_strategy_observation`：调用 `StrategyBenchmarkAdapter().load_observation(case, workspace)`
4. 在 `polaris/kernelone/context/strategy_benchmark.py` 头部添加：
   ```python
   """.. deprecated:: 本模块已废弃，请使用 polaris.kernelone.benchmark.unified_runner"""
   import warnings
   warnings.warn("strategy_benchmark.py 已废弃", DeprecationWarning, stacklevel=2)
   ```

---

## Phase 2 Week 2: 包8（研发H）— Case JSON 迁移

### 包8：Case JSON 格式迁移

**目标**: 迁移约40个 JSON case 文件到新格式

**步骤**:

1. 创建 `scripts/migrate_benchmark_cases.py`：
   - 读取 `polaris/cells/llm/evaluation/fixtures/agentic_benchmark/cases/*.json`
   - 转换为 `UnifiedBenchmarkCase` 格式
   - 添加字段：`expected_evidence_path: []`, `budget_conditions: {"max_tokens": 200000, "max_turns": 10, "max_wall_time_seconds": 300.0}`, `canonical_profile: "canonical_balanced"`
   - 将 `judge` 字段的 `AgenticJudgeConfig` 转换为 `JudgeConfig`
   - 添加 `mode: "agentic"` 到 `judge` 字段

2. 在 `polaris/kernelone/benchmark/fixtures/` 目录下创建新 case 文件（不修改原始文件）

3. 验证：使用 `UnifiedBenchmarkCase` 反序列化新 JSON，确认无错误

---

## Phase 3 (Week 5-6): 执行路径切换 — 包6 + 包7 + 包9

### 包6（研发F）：执行路径切换

**目标**: 将 `polaris/cells/llm/evaluation/public/service.py` 的实现从 `agentic_benchmark.py` 切换到 `unified_runner`

**步骤**:

1. 读取 `polaris/cells/llm/evaluation/public/service.py`
2. 读取 `polaris/cells/llm/evaluation/internal/agentic_benchmark.py` 的 `run_agentic_benchmark_suite()` 签名和返回格式
3. 修改 `service.py`：
   - 导入 `UnifiedBenchmarkRunner` 和 `UnifiedJudge`
   - 导入 `load_builtin_agentic_benchmark_cases`（来自包8的迁移脚本）
   - 将 `run_agentic_benchmark_suite` 内部实现改为调用 `UnifiedBenchmarkRunner().run_suite()`
   - 将 `UnifiedBenchmarkCase` 列表传给 runner
   - 将 runner 返回的 `BenchmarkSuiteResult` 转换为旧格式 dict（保持 API 兼容）

```python
# service.py 新实现
from polaris.kernelone.benchmark.unified_runner import UnifiedBenchmarkRunner
from polaris.kernelone.benchmark.unified_judge import UnifiedJudge

async def run_agentic_benchmark_suite(provider_cfg, model, role, *, workspace, ...):
    runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
    # 加载 cases（使用包8的迁移后case）
    cases = load_migrated_agentic_benchmark_cases(role=role)
    result = await runner.run_suite(cases=cases, workspace=workspace, mode="agentic")
    return _convert_to_legacy_format(result)
```

---

### 包7（研发G）：service.py 统一入口重写

**目标**: 扩展 `service.py` 支持 context/strategy 模式

1. 添加 `run_context_benchmark_suite()` 和 `run_strategy_benchmark_suite()` 到 `service.py`
2. 导出到 `__all__`
3. 确保 CLI（包9）可以调用这些新接口

---

### 包9（研发I）：CLI 重写

**目标**: 修改 `polaris/delivery/cli/agentic_eval.py` 适配新 API

**步骤**:

1. 读取 `polaris/delivery/cli/agentic_eval.py`
2. 保持 CLI 参数兼容（`--workspace`, `--role`, `--case-ids`, `--mode`）
3. 内部调用改为 `UnifiedBenchmarkRunner`（通过 `service.py`）
4. 更新输出格式以匹配新 `BenchmarkSuiteResult`

---

## Phase 4 (Week 7-8): 包10（研发J）— 集成测试 + 清理

### 包10：集成测试 + 回归测试 + 清理

**Week 7**:

1. **集成测试**：
   - 编写 `polaris/kernelone/benchmark/tests/test_unified_runner_integration.py`
   - 覆盖 agentic/strategy/context 三种模式
   - 运行完整 benchmark 套件（40个 case）

2. **回归测试**：
   - 对比新旧系统对40个 case 的评分
   - 差异率超过 1% 的 case 逐个审查

**Week 8**:

3. **废弃模块清理**：
   - 在 `benchmark_models.py` 头部添加 `deprecated` 注释
   - 在 `deterministic_judge.py` 头部添加 `deprecated` 注释
   - 在 `agentic_benchmark.py` 头部添加 `deprecated` 注释

4. **最终验证**：
   ```bash
   pytest polaris/kernelone/benchmark/tests/ -v
   ruff check polaris/kernelone/benchmark/ --fix
   ruff format polaris/kernelone/benchmark/
   mypy polaris/kernelone/benchmark/
   ```

---

## 立即开始

所有10人应立即读取以下文件开始：

**研发A + 研发B（Phase 1）**：
- `polaris/kernelone/benchmark/unified_judge.py`（目标文件）
- `polaris/cells/llm/evaluation/internal/deterministic_judge.py`（源文件，L1967-1994的VALIDATORS字典）

**研发C（Phase 1-2）**：
- `polaris/kernelone/benchmark/unified_judge.py`
- `polaris/cells/llm/evaluation/internal/deterministic_judge.py`（L858-890的_check_output_substrings）

**研发D（Phase 2）**：
- `polaris/kernelone/benchmark/unified_runner.py`（L642-670）
- `polaris/kernelone/benchmark/adapters/context_adapter.py`

**研发E（Phase 2）**：
- `polaris/kernelone/benchmark/unified_runner.py`（L569-641）
- `polaris/kernelone/benchmark/adapters/strategy_adapter.py`
- `polaris/kernelone/context/strategy_benchmark.py`

**研发F + 研发G（Phase 3）**：
- `polaris/cells/llm/evaluation/public/service.py`
- `polaris/cells/llm/evaluation/internal/agentic_benchmark.py`

**研发H（Phase 2）**：
- `polaris/cells/llm/evaluation/fixtures/agentic_benchmark/cases/*.json`（示例case）
- `polaris/kernelone/benchmark/unified_models.py`（UnifiedBenchmarkCase定义）

**研发I（Phase 3）**：
- `polaris/delivery/cli/agentic_eval.py`

**研发J（Phase 4）**：
- `polaris/kernelone/benchmark/unified_runner.py`
- `polaris/kernelone/benchmark/tests/`（现有测试）
