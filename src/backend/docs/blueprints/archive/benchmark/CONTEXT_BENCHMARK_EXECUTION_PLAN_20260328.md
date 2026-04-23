# Benchmark 框架 Context OS 执行计划

**版本**: 1.0
**日期**: 2026-03-28
**状态**: 🔴 待启动
**负责人**: 10人 Python 架构与代码治理实验室
**优先级**: P0

---

## 1. 背景

### 1.1 已知问题

| 问题 | 严重性 | 证据 |
|------|--------|------|
| Context adapter 是 stub 实现 | 🔴 阻塞 | `polaris/kernelone/benchmark/adapters/context_adapter.py:69-99` 返回空 `predicted_files` |
| `infrastructure/accel/eval/` 完全孤立 | 🟡 技术债 | 0 个导入方，metrics 无法被新框架使用 |
| 无 Context adapter 测试 | 🔴 阻塞 | `benchmark/tests/` 无 `test_context_adapter.py` |
| Fixture 格式不兼容 | 🟡 需处理 | `context/benchmarks/fixtures/*.json` 缺少 `role`/`judge` 字段 |

### 1.2 目标

```
polaris/kernelone/benchmark/adapters/context_adapter.py
         │
         ├── 接入 ──→ infrastructure/accel/eval/metrics.py
         │             ├── recall_at_k()
         │             ├── reciprocal_rank()
         │             └── symbol_hit_rate()
         │
         └── 评估 ──→ context compilation pipeline
                       ├── predicted files
                       └── expected_evidence_path
```

---

## 2. 团队分工 (10 人 Python 架构与代码治理实验室)

| 角色 | 专家 | 职责 |
|------|------|------|
| CTO - 战略决策者 | @CTO | 技术选型，确保超前扩展性 |
| Principal Engineer | @PE | SOLID/DRY，解耦复杂模块 |
| Security Lead | @Security | 审计注入、敏感信息 |
| Refactoring Guru | @Refactor | Pythonic 重构 |
| Typing Specialist | @Typing | mypy 严格检查 |
| QA Automation | @QA | Pytest 架构，边缘案例 |
| Documentation Lead | @Docs | Docstrings，可读性 |
| Framework SME | @Framework | FastAPI/SQLAlchemy 底层 |
| Code Auditor | @Auditor | PEP 8 扫描 |
| DevOps Integrator | @DevOps | CI/CD，依赖管理 |

### 2.1 Phase A - 诊断 (1-2 天)

**@Auditor + @PE 发现 3 个核心缺陷**:

| # | 缺陷 | 文件位置 | 类型 |
|---|------|----------|------|
| 1 | Context adapter `_evaluate_context()` 返回硬编码零值 | `context_adapter.py:83-99` | 逻辑缺失 |
| 2 | `infrastructure/accel/eval/metrics.py` 完全孤立，无人导入 | `metrics.py:1-88` | 架构问题 |
| 3 | Fixture JSON 缺少 Unified 模型必需字段 | `context/benchmarks/fixtures/*.json` | 数据模型不兼容 |

---

## 3. 架构设计

### 3.1 目标文件结构

```
polaris/kernelone/benchmark/
├── adapters/
│   ├── context_adapter.py    # 重写：接入真实 metrics
│   └── __init__.py
└── tests/
    └── test_context_adapter.py  # 新增：20+ 测试

polaris/infrastructure/accel/eval/
├── runner.py                 # 现有（保留）
├── metrics.py                # 现有（被 context_adapter 引用）
└── __init__.py               # 现有（被 context_adapter 引用）
```

### 3.2 Context Adapter 重写设计

```python
# polaris/kernelone/benchmark/adapters/context_adapter.py
"""Context Benchmark Adapter

Design Patterns:
- Facade Pattern: 封装 context compilation pipeline
- Strategy Pattern: 可插拔 context selector
- Observer Pattern: 编译过程可观察

Deprecates:
    infrastructure.accel.eval.runner (isolated, no callers)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.benchmark.unified_models import (
    ObservedBenchmarkRun,
    UnifiedBenchmarkCase,
)

if TYPE_CHECKING:
    from polaris.infrastructure.accel.eval.metrics import (
        recall_at_k,
        reciprocal_rank,
        symbol_hit_rate,
    )


class ContextBenchmarkAdapter:
    """Context Benchmark Adapter

    Evaluates context selection quality by comparing predicted context
    against expected evidence paths defined in the case.

    Architecture:
        Case定义 ──→ Adapter ──→ Context Compilation Pipeline
                        │
                        └──→ metrics.recall_at_k/mrr/symbol_hit_rate
                                   │
                                   └──→ ObservedBenchmarkRun
    """

    def __init__(
        self,
        context_compiler: ContextCompilerProtocol | None = None,
    ) -> None:
        """Initialize adapter.

        Args:
            context_compiler: Optional context compiler (DI). If None,
                              uses default implementation.
        """
        self._compiler = context_compiler
        self._evaluations: dict[str, dict[str, Any]] = {}

    def evaluate(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun:
        """Evaluate context selection for a case.

        Args:
            case: Benchmark case with expected_evidence_path.
            workspace: Workspace path.

        Returns:
            ObservedBenchmarkRun with evaluation results.

        Raises:
            ContextCompilationError: If context compilation fails.
        """
        compiler = self._get_compiler()
        expected = list(case.expected_evidence_path)

        # Compile predicted context
        predicted_context = self._compile_context(case, workspace, compiler)

        # Calculate metrics using isolated metrics.py
        evaluation = self._evaluate_context_metrics(
            expected=expected,
            predicted=predicted_context,
            case_id=case.case_id,
        )

        self._evaluations[case.case_id] = evaluation

        return ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace=workspace,
            output=self._format_evaluation_output(evaluation),
            thinking=f"context_selection_score: {evaluation.get('score', 0.0):.4f}",
            tool_calls=(),
            event_count=0,
        )

    def _get_compiler(self) -> ContextCompilerProtocol:
        """Get context compiler via DI or default."""
        if self._compiler is not None:
            return self._compiler

        # Lazy import to avoid hard coupling
        try:
            from polaris.kernelone.context.compilation.pipeline import (
                compile_context_for_task,
            )
            return _DefaultContextCompiler()
        except ImportError:
            return _StubContextCompiler()

    def _compile_context(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
        compiler: ContextCompilerProtocol,
    ) -> list[str]:
        """Compile context for the case task.

        Args:
            case: The benchmark case.
            workspace: Workspace path.
            compiler: Context compiler instance.

        Returns:
            List of predicted file paths.

        Raises:
            ContextCompilationError: If compilation fails.
        """
        task_description = case.prompt

        try:
            result = compiler.compile(
                task=task_description,
                workspace=workspace,
                max_files=case.budget_conditions.max_tokens,
            )
            return result.selected_files
        except Exception as exc:
            raise ContextCompilationError(
                f"Failed to compile context for {case.case_id}: {exc}"
            ) from exc

    def _evaluate_context_metrics(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> dict[str, Any]:
        """Evaluate context metrics using infrastructure.accel.eval.metrics.

        This function bridges the isolated metrics.py to the unified
        benchmark framework.

        Args:
            expected: Expected file paths from case.
            predicted: Predicted file paths from compiler.
            case_id: Benchmark case ID.

        Returns:
            Evaluation dictionary with scores.
        """
        # Import isolated metrics module
        from polaris.infrastructure.accel.eval.metrics import (
            recall_at_k,
            reciprocal_rank,
        )

        r5 = recall_at_k(expected, predicted, k=5)
        r10 = recall_at_k(expected, predicted, k=10)
        mrr = reciprocal_rank(expected, predicted)

        # Weighted score: recall@10 (0.7) + MRR (0.3)
        score = round(r10 * 0.7 + mrr * 0.3, 6)

        return {
            "case_id": case_id,
            "expected_files": expected,
            "predicted_files": predicted,
            "score": score,
            "recall_at_5": r5,
            "recall_at_10": r10,
            "mrr": mrr,
        }

    def _format_evaluation_output(self, evaluation: dict[str, Any]) -> str:
        """Format evaluation as output text."""
        return (
            f"context_evaluation:{evaluation['case_id']} | "
            f"score={evaluation['score']:.4f} | "
            f"r@10={evaluation['recall_at_10']:.4f} | "
            f"mrr={evaluation['mrr']:.4f}"
        )

    def get_evaluation(self, case_id: str) -> dict[str, Any] | None:
        """Get cached evaluation result."""
        return self._evaluations.get(case_id)

    def clear_evaluations(self) -> None:
        """Clear cached evaluations."""
        self._evaluations.clear()


# ------------------------------------------------------------------
# Protocols (Dependency Injection)
# ------------------------------------------------------------------


class ContextCompilerProtocol:
    """Protocol for context compilers (Strategy Pattern)."""

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        """Compile context for a task.

        Args:
            task: Task description.
            workspace: Workspace path.
            max_files: Maximum files to select.

        Returns:
            ContextCompilationResult with selected_files.
        """
        ...


@dataclass(frozen=True, kw_only=True)
class ContextCompilationResult:
    """Result of context compilation."""
    selected_files: tuple[str, ...]
    confidence_scores: tuple[float, ...] = field(default_factory=tuple)


class _DefaultContextCompiler:
    """Default context compiler using kernelone.context."""

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        """Compile using kernelone context compilation pipeline."""
        from polaris.kernelone.context.compilation.pipeline import (
            compile_context_for_task,
        )
        result = compile_context_for_task(
            task=task,
            workspace=workspace,
            max_files=max_files,
        )
        return ContextCompilationResult(
            selected_files=tuple(result.get("files", [])),
            confidence_scores=tuple(result.get("scores", [])),
        )


class _StubContextCompiler:
    """Stub compiler for environments without context pipeline."""

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        """Return empty result."""
        return ContextCompilationResult(selected_files=())


class ContextCompilationError(Exception):
    """Raised when context compilation fails."""
```


### 3.3 Fixture 映射层设计

```python
# polaris/kernelone/benchmark/adapters/context_fixture_mapper.py
"""Context Benchmark Fixture Mapper

Maps legacy fixture format (infrastructure.accel.eval) to
UnifiedBenchmarkCase format.

Legacy format:
    {
        "case_id": "...",
        "task": "...",
        "expected_files": [...],
    }

Unified format requires:
    - case_id, role, title, prompt
    - judge: JudgeConfig
    - expected_evidence_path
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.benchmark.unified_models import (
    BenchmarkMode,
    BudgetConditions,
    JudgeConfig,
    UnifiedBenchmarkCase,
)


class ContextFixtureMapper:
    """Maps legacy context fixtures to UnifiedBenchmarkCase."""

    DEFAULT_ROLE: str = "director"
    DEFAULT_THRESHOLD: float = 0.70

    def map(self, legacy_fixture: dict[str, Any]) -> UnifiedBenchmarkCase:
        """Map legacy fixture to UnifiedBenchmarkCase.

        Args:
            legacy_fixture: Fixture in legacy format.

        Returns:
            UnifiedBenchmarkCase instance.

        Raises:
            ValueError: If required fields are missing.
        """
        case_id = str(legacy_fixture.get("case_id", "")).strip()
        if not case_id:
            raise ValueError("case_id is required")

        task = str(legacy_fixture.get("task", "")).strip()
        if not task:
            raise ValueError(f"{case_id}: task is required")

        expected_files = legacy_fixture.get("expected_files", [])
        if isinstance(expected_files, list):
            expected_files = tuple(expected_files)

        return UnifiedBenchmarkCase(
            case_id=case_id,
            role=legacy_fixture.get("role", self.DEFAULT_ROLE),
            title=case_id.replace("_", " ").title(),
            prompt=task,
            description=legacy_fixture.get("description", ""),
            expected_evidence_path=expected_files,
            workspace_fixture=legacy_fixture.get("workspace_fixture", ""),
            budget_conditions=BudgetConditions(
                max_tokens=legacy_fixture.get("max_tokens", 200_000),
                max_turns=legacy_fixture.get("max_turns", 8),
                max_wall_time_seconds=legacy_fixture.get(
                    "max_wall_time_seconds", 180.0
                ),
            ),
            judge=JudgeConfig(
                score_threshold=legacy_fixture.get(
                    "score_threshold", self.DEFAULT_THRESHOLD
                ),
                mode=BenchmarkMode.CONTEXT,
            ),
        )
```

---

## 4. Phase B - 重构 (3-5 天)

### 4.1 @Refactor + @Typing 产出

**文件变更清单**:

| 文件 | 操作 | 变更点 |
|------|------|--------|
| `benchmark/adapters/context_adapter.py` | 重写 | 实现 `_evaluate_context_metrics()` 调用 `metrics.py` |
| `benchmark/adapters/context_fixture_mapper.py` | 新增 | Fixture 格式映射 |
| `benchmark/tests/test_context_adapter.py` | 新增 | 20+ 测试用例 |
| `infrastructure/accel/eval/__init__.py` | 更新 | 添加 `__all__` 显式导出 |

### 4.2 关键代码变更

#### 变更 1: `context_adapter.py` 重写

```python
# BEFORE (stub)
def _evaluate_context(self, case, workspace):
    return {
        "predicted_files": [],
        "score": 0.0,
        ...
    }

# AFTER (real implementation)
def _evaluate_context_metrics(self, expected, predicted, case_id):
    from polaris.infrastructure.accel.eval.metrics import (
        recall_at_k,
        reciprocal_rank,
    )
    r10 = recall_at_k(expected, predicted, k=10)
    mrr = reciprocal_rank(expected, predicted)
    return {
        "score": r10 * 0.7 + mrr * 0.3,
        "recall_at_10": r10,
        "mrr": mrr,
        ...
    }
```

#### 变更 2: `infrastructure/accel/eval/__init__.py` 添加导出

```python
"""infrastructure.accel.eval - Context evaluation metrics.

Public API:
    recall_at_k(expected, predicted, k)
    reciprocal_rank(expected, predicted)
    symbol_hit_rate(expected, observed)

Note:
    This module was previously isolated. It is now integrated
    into the unified benchmark framework via:
    polaris.kernelone.benchmark.adapters.context_adapter
"""

from .metrics import recall_at_k, reciprocal_rank, symbol_hit_rate
from .runner import load_benchmark_suite, run_benchmark_suite

__all__ = [
    "load_benchmark_suite",
    "run_benchmark_suite",
    "recall_at_k",
    "reciprocal_rank",
    "symbol_hit_rate",
]
```

---

## 5. Phase C - 质检 (2-3 天)

### 5.1 @QA 提供验证方案

```python
# polaris/kernelone/benchmark/tests/test_context_adapter.py
"""Context Benchmark Adapter Tests

Covers:
- Context evaluation metrics calculation
- Fixture mapping from legacy format
- Error handling for compilation failures
- DI behavior with custom compilers
"""

from __future__ import annotations

import pytest
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.adapters.context_adapter import (
    ContextBenchmarkAdapter,
    ContextCompilationError,
    ContextCompilerProtocol,
    ContextCompilationResult,
)
from polaris.kernelone.benchmark.adapters.context_fixture_mapper import (
    ContextFixtureMapper,
)
from polaris.kernelone.benchmark.unified_models import (
    BenchmarkMode,
    BudgetConditions,
    JudgeConfig,
    UnifiedBenchmarkCase,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def sample_case() -> UnifiedBenchmarkCase:
    """Sample benchmark case for testing."""
    return UnifiedBenchmarkCase(
        case_id="locate_bug_root_cause",
        role="director",
        title="Locate Bug Root Cause",
        prompt="Find the bug in polaris/kernelone/context/strategy_*.py",
        expected_evidence_path=(
            "polaris/kernelone/context/strategy_benchmark.py",
            "polaris/kernelone/context/strategy_scoring.py",
        ),
        judge=JudgeConfig(
            score_threshold=0.70,
            mode=BenchmarkMode.CONTEXT,
        ),
    )


@pytest.fixture
def legacy_fixture() -> dict[str, Any]:
    """Legacy fixture format."""
    return {
        "case_id": "test_context_case",
        "task": "Find the root cause of scoring inconsistency",
        "expected_files": [
            "src/scoring.py",
            "src/receipts.py",
        ],
        "workspace_fixture": "test_workspace",
        "max_tokens": 50000,
        "max_turns": 5,
        "max_wall_time_seconds": 60.0,
        "score_threshold": 0.65,
    }


# ------------------------------------------------------------------
# ContextBenchmarkAdapter Tests
# ------------------------------------------------------------------

class TestContextBenchmarkAdapter:
    """Adapter evaluation logic tests."""

    def test_evaluate_returns_observed_run(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """evaluate() returns ObservedBenchmarkRun with metrics."""
        adapter = ContextBenchmarkAdapter(
            context_compiler=_FakeCompiler(
                selected_files=["src/scoring.py", "src/receipts.py"]
            )
        )

        workspace = str(tmp_path)
        result = adapter.evaluate(sample_case, workspace)

        assert result.case_id == sample_case.case_id
        assert result.role == sample_case.role
        assert "context_evaluation" in result.output
        assert "score=" in result.output

    def test_evaluate_with_full_recall(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """100% recall returns score close to 1.0."""
        adapter = ContextBenchmarkAdapter(
            context_compiler=_FakeCompiler(
                selected_files=list(sample_case.expected_evidence_path)
            )
        )

        result = adapter.evaluate(sample_case, str(tmp_path))
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        # Full recall + MRR=1.0 → score ≈ 1.0
        assert eval_data["score"] >= 0.95
        assert eval_data["recall_at_10"] == 1.0
        assert eval_data["mrr"] == 1.0

    def test_evaluate_with_partial_recall(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """Partial recall returns intermediate score."""
        adapter = ContextBenchmarkAdapter(
            context_compiler=_FakeCompiler(
                selected_files=["src/scoring.py"]  # Only 1 of 2
            )
        )

        result = adapter.evaluate(sample_case, str(tmp_path))
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        assert 0.0 < eval_data["score"] < 1.0
        assert eval_data["recall_at_10"] == 0.5  # 1 of 2

    def test_evaluate_with_empty_prediction(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """Empty prediction returns zero score."""
        adapter = ContextBenchmarkAdapter(
            context_compiler=_FakeCompiler(selected_files=[])
        )

        result = adapter.evaluate(sample_case, str(tmp_path))
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        assert eval_data["score"] == 0.0
        assert eval_data["recall_at_5"] == 0.0
        assert eval_data["mrr"] == 0.0

    def test_evaluate_compilation_error_raises(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """ContextCompilationError propagates on failure."""
        adapter = ContextBenchmarkAdapter(
            context_compiler=_FailingCompiler()
        )

        with pytest.raises(ContextCompilationError) as exc_info:
            adapter.evaluate(sample_case, str(tmp_path))

        assert sample_case.case_id in str(exc_info.value)

    def test_clear_evaluations(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """clear_evaluations() removes all cached results."""
        adapter = ContextBenchmarkAdapter(
            context_compiler=_FakeCompiler(selected_files=[])
        )
        adapter.evaluate(sample_case, str(tmp_path))

        assert adapter.get_evaluation(sample_case.case_id) is not None

        adapter.clear_evaluations()

        assert adapter.get_evaluation(sample_case.case_id) is None


# ------------------------------------------------------------------
# ContextFixtureMapper Tests
# ------------------------------------------------------------------

class TestContextFixtureMapper:
    """Fixture mapping tests."""

    def test_maps_legacy_to_unified(
        self,
        legacy_fixture: dict[str, Any],
    ) -> None:
        """Legacy fixture maps to UnifiedBenchmarkCase."""
        mapper = ContextFixtureMapper()
        unified = mapper.map(legacy_fixture)

        assert unified.case_id == legacy_fixture["case_id"]
        assert unified.prompt == legacy_fixture["task"]
        assert len(unified.expected_evidence_path) == 2
        assert unified.judge.mode == BenchmarkMode.CONTEXT
        assert unified.budget_conditions.max_turns == 5

    def test_maps_minimal_fixture(
        self,
    ) -> None:
        """Minimal legacy fixture uses defaults."""
        mapper = ContextFixtureMapper()
        unified = mapper.map({
            "case_id": "minimal",
            "task": "Do something",
        })

        assert unified.role == "director"  # Default
        assert unified.judge.score_threshold == 0.70  # Default
        assert unified.budget_conditions.max_tokens == 200_000  # Default

    def test_raises_on_missing_case_id(
        self,
    ) -> None:
        """Missing case_id raises ValueError."""
        mapper = ContextFixtureMapper()

        with pytest.raises(ValueError, match="case_id is required"):
            mapper.map({"task": "test"})

    def test_raises_on_missing_task(
        self,
    ) -> None:
        """Missing task raises ValueError."""
        mapper = ContextFixtureMapper()

        with pytest.raises(ValueError, match="task is required"):
            mapper.map({"case_id": "test"})


# ------------------------------------------------------------------
# Fakes for Testing
# ------------------------------------------------------------------

class _FakeCompiler:
    """Fake context compiler for tests."""

    def __init__(self, selected_files: list[str]) -> None:
        self._files = selected_files

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        return ContextCompilationResult(
            selected_files=tuple(self._files)
        )


class _FailingCompiler:
    """Compiler that always fails for tests."""

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        msg = "Simulated compilation failure"
        raise ContextCompilationError(msg)
```

### 5.2 验证命令

```bash
# 必须全部通过
ruff check polaris/kernelone/benchmark/adapters/context_adapter.py --fix
ruff format polaris/kernelone/benchmark/adapters/context_adapter.py
mypy polaris/kernelone/benchmark/adapters/context_adapter.py --strict
pytest polaris/kernelone/benchmark/tests/test_context_adapter.py -v

# 集成验证
pytest polaris/kernelone/benchmark/tests/ -v --tb=short
```

---

## 6. 执行时间线

```
Week 1:
├── Day 1-2: Phase A - 诊断 + 确认3个核心缺陷
├── Day 3-4: Phase B - Context adapter 重写
└── Day 5:   Phase B - Fixture mapper 实现

Week 2:
├── Day 1-2: Phase C - 测试编写 + 通过
├── Day 3:   CI/CD 集成 (GitHub Actions)
└── Day 4-5: 回归测试 + 性能对比

交付物:
✓ Context adapter 真实实现（接入 metrics.py）
✓ 20+ 测试用例
✓ Fixture mapper
✓ CI/CD 门禁
```

---

## 7. 验收标准

| 指标 | 要求 | 验证方式 |
|------|------|----------|
| Context adapter 实现 | `_evaluate_context_metrics()` 调用 `metrics.py` | 代码审查 |
| 测试覆盖率 | >= 90% | `pytest --cov` |
| mypy 检查 | 0 errors | `mypy --strict` |
| 孤立 metrics.py 被引用 | `infrastructure/accel/eval/metrics.py` 有调用方 | `grep -r "from.*accel.eval.metrics"` |
| Fixture 映射 | 5 个 legacy fixture 全部可映射 | 单元测试 |

---

## 8. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| Context compilation pipeline 不存在 | 🟡 中 | 🟡 中 | 提供 `_StubContextCompiler` 降级 |
| `metrics.py` API 与需求不匹配 | 🔴 高 | 🔴 高 | 先读 `metrics.py` 再实现 adapter |
| Fixture 文件损坏 | 🟢 低 | 🟡 中 | 添加 schema 验证 |

---

## 9. 依赖关系

```
infrastructure/accel/eval/metrics.py
         │
         │ (被引用)
         ▼
benchmark/adapters/context_adapter.py
         │
         │ (使用)
         ▼
benchmark/adapters/context_fixture_mapper.py
         │
         │ (测试)
         ▼
benchmark/tests/test_context_adapter.py
```
