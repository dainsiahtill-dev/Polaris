# Python 架构与代码治理实验室 - 团队组织与加速计划

**版本**: 1.0
**日期**: 2026-03-28
**状态**: 🔴 已激活
**团队**: 10 位顶级 Python 专家

---

## 1. 团队架构

### 1.1 组织结构

```
┌─────────────────────────────────────────────────────────────┐
│              Python 架构与代码治理实验室                       │
├─────────────────────────────────────────────────────────────┤
│  CTO (战略决策者)                                            │
│  └── Principal Engineer (首席架构师)                         │
│       ├── Security Lead (安全总监)                           │
│       ├── Refactoring Guru (重构大师)                        │
│       ├── Typing Specialist (类型专家)                       │
│       ├── QA Automation (测试专家)                            │
│       ├── Documentation Lead (规范主管)                       │
│       ├── Framework SME (框架专家)                            │
│       ├── Code Auditor (审计员)                              │
│       └── DevOps Integrator (工程化专家)                     │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 角色职责矩阵

| 角色 | 主要职责 | 次要职责 |
|------|----------|----------|
| **CTO** | 技术选型、架构腐化预防 | 跨团队协调、优先级排序 |
| **Principal Engineer** | SOLID/DRY 强制执行 | 模块解耦、契约设计 |
| **Security Lead** | 注入漏洞审计、敏感信息 | 依赖安全、API 安全 |
| **Refactoring Guru** | Pythonic 重构、设计模式 | 代码审查、技术债务 |
| **Typing Specialist** | mypy 严格检查、类型设计 | TypeAlias、Protocol 设计 |
| **QA Automation** | Pytest 架构、边缘案例 | 覆盖率分析、测试策略 |
| **Documentation Lead** | Docstrings、API 文档 | 变更日志、技术规范 |
| **Framework SME** | FastAPI/SQLAlchemy 底层 | 中间件、ORM 优化 |
| **Code Auditor** | PEP 8 扫描、现代特性 | 代码气味检测 |
| **DevOps Integrator** | CI/CD、依赖管理 | 环境一致性、发布流程 |

---

## 2. 核心任务池

### 2.1 任务总览

| # | 任务 | 优先级 | 状态 | 负责人 |
|---|------|--------|------|--------|
| 1 | Context adapter metrics 集成 | 🔴 P0 | 🔴 待启动 | @Refactor + @Typing |
| 2 | CLI 入口重写 | 🟡 P1 | ⏳ 待执行 | @Principal + @Framework |
| 3 | 性能基准对比 | 🟡 P1 | ⏳ 待执行 | @QA + @DevOps |
| 4 | 全量回归测试 | 🟡 P1 | ⏳ 待执行 | @QA |
| 5 | 旧模块归档决策 | 🟢 P2 | ⏳ 待执行 | @CTO + @Auditor |

### 2.2 任务依赖图

```
Task 1 (Context adapter)
    │
    ├───→ Task 2 (CLI 重写) [依赖 Task 1 完成]
    │         │
    │         └───→ Task 4 (回归测试) [依赖 Task 2]
    │
    └───→ Task 3 (性能对比) [可并行]
              │
              └───→ Task 5 (归档决策) [依赖 Task 1+2+3]
```

---

## 3. Phase A: 诊断报告

### 3.1 审计员 + 架构师 核心缺陷

| # | 缺陷 | 文件 | 类型 | 严重性 |
|---|------|------|------|--------|
| 1 | Context adapter stub 实现，未调用 metrics | `context_adapter.py:83-99` | 逻辑缺失 | 🔴 |
| 2 | `infrastructure/accel/eval/metrics.py` 孤立 | `metrics.py` | 架构问题 | 🟡 |
| 3 | CLI 入口未迁移到 UnifiedBenchmarkRunner | `agentic_eval.py` | 技术债 | 🟡 |
| 4 | 无 Context adapter 测试 | `test_context_adapter.py` | 覆盖缺失 | 🔴 |
| 5 | Fixture 格式不兼容 UnifiedBenchmarkCase | `fixtures/*.json` | 数据模型 | 🟡 |

### 3.2 CTO 战略评估

**技术债清理优先级**:

```
🔴 P0 (阻塞):
  └── Context adapter stub → 必须立即实现

🟡 P1 (重要):
  ├── CLI 入口重写 → 统一入口
  ├── 性能基准对比 → 验证无退化
  └── 回归测试 → 确保向后兼容

🟢 P2 (优化):
  └── 旧模块归档 → 降低维护成本
```

**架构腐化风险**: 中等
- 当前统一框架已建立，但 Context adapter 是 stub
- 如不修复，新架构可信度下降

---

## 4. Phase B: 重构产出

### 4.1 重构大师 + 类型专家 联合产出

#### 产出 1: Context Adapter 重写 (`context_adapter.py`)

```python
# Design Patterns Applied:
# - Facade Pattern: 封装 context compilation pipeline
# - Strategy Pattern: ContextCompilerProtocol 可插拔
# - Observer Pattern: 编译过程可观察
# - Dependency Injection: 可 mock，可降级

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typing_extensions import TypeAlias

if TYPE_CHECKING:
    from polaris.kernelone.benchmark.unified_models import (
        ObservedBenchmarkRun,
        UnifiedBenchmarkCase,
    )


MetricsResult: TypeAlias = dict[str, Any]


class ContextBenchmarkAdapter:
    """Context Benchmark Adapter.

    Evaluates context selection quality by comparing predicted context
    against expected evidence paths.

    Architecture:
        Case ──→ Adapter ──→ Context Compiler
                            │
                            └──→ metrics.recall_at_k/mrr
                                    │
                                    └──→ ObservedBenchmarkRun

    Example:
        adapter = ContextBenchmarkAdapter()
        result = adapter.evaluate(case, workspace)
    """

    def __init__(
        self,
        compiler: ContextCompilerProtocol | None = None,
        metrics_calculator: MetricsCalculatorProtocol | None = None,
    ) -> None:
        """Initialize adapter with optional DI.

        Args:
            compiler: Context compiler. Uses default if None.
            metrics_calculator: Metrics calculator. Uses default if None.
        """
        self._compiler = compiler
        self._metrics = metrics_calculator or _DefaultMetricsCalculator()
        self._evaluations: dict[str, MetricsResult] = {}

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
            ObservedBenchmarkRun with evaluation metrics.

        Raises:
            ContextCompilationError: If context compilation fails.
        """
        compiler = self._get_compiler()
        expected = list(case.expected_evidence_path)

        # Compile predicted context
        predicted_context = self._compile_context(
            case=case,
            workspace=workspace,
            compiler=compiler,
        )

        # Calculate metrics using DI metrics calculator
        evaluation = self._metrics.calculate(
            expected=expected,
            predicted=predicted_context,
            case_id=case.case_id,
        )

        self._evaluations[case.case_id] = evaluation

        return ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace=workspace,
            output=self._format_output(evaluation),
            thinking=f"context_score={evaluation['score']:.4f}",
            tool_calls=(),
            event_count=0,
        )

    def _get_compiler(self) -> ContextCompilerProtocol:
        """Get compiler via DI or default."""
        if self._compiler is not None:
            return self._compiler

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
        """Compile context for task.

        Args:
            case: Benchmark case.
            workspace: Workspace path.
            compiler: Context compiler.

        Returns:
            List of predicted file paths.

        Raises:
            ContextCompilationError: If compilation fails.
        """
        try:
            result = compiler.compile(
                task=case.prompt,
                workspace=workspace,
                max_files=case.budget_conditions.max_tokens,
            )
            return list(result.selected_files)
        except Exception as exc:  # pragma: no cover - defensive
            raise ContextCompilationError(
                f"Failed to compile context for {case.case_id}: {exc}"
            ) from exc

    def _format_output(self, evaluation: MetricsResult) -> str:
        """Format evaluation as output text."""
        return (
            f"context_evaluation:{evaluation['case_id']} | "
            f"score={evaluation['score']:.4f} | "
            f"r@10={evaluation['recall_at_10']:.4f} | "
            f"mrr={evaluation['mrr']:.4f}"
        )

    def get_evaluation(self, case_id: str) -> MetricsResult | None:
        """Get cached evaluation result."""
        return self._evaluations.get(case_id)

    def clear_evaluations(self) -> None:
        """Clear all cached evaluations."""
        self._evaluations.clear()


# ------------------------------------------------------------------
# Protocols (Dependency Injection Interfaces)
# ------------------------------------------------------------------

class ContextCompilerProtocol:
    """Protocol for context compilers (Strategy Pattern)."""

    __slots__ = ()

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
            ContextCompilationResult with selected files.
        """
        ...


@dataclass(frozen=True, kw_only=True)
class ContextCompilationResult:
    """Result of context compilation."""
    selected_files: tuple[str, ...]
    confidence_scores: tuple[float, ...] = field(default_factory=tuple)


class MetricsCalculatorProtocol:
    """Protocol for metrics calculators (Strategy Pattern)."""

    __slots__ = ()

    def calculate(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> MetricsResult:
        """Calculate context selection metrics.

        Args:
            expected: Expected file paths.
            predicted: Predicted file paths.
            case_id: Benchmark case ID.

        Returns:
            Metrics dictionary with scores.
        """
        ...


# ------------------------------------------------------------------
# Default Implementations
# ------------------------------------------------------------------

class _DefaultMetricsCalculator:
    """Default metrics calculator using isolated metrics.py."""

    __slots__ = ("_metrics_module",)

    def __init__(self) -> None:
        self._metrics_module = self._load_metrics_module()

    def _load_metrics_module(self) -> MetricsModule | None:
        """Lazy load isolated metrics module."""
        try:
            from polaris.infrastructure.accel import eval as metrics_module
            return metrics_module
        except ImportError:
            return None

    def calculate(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> MetricsResult:
        """Calculate metrics using infrastructure.accel.eval.metrics.

        Args:
            expected: Expected file paths.
            predicted: Predicted file paths.
            case_id: Benchmark case ID.

        Returns:
            Metrics dictionary with scores.
        """
        if self._metrics_module is None:
            return self._stub_metrics(expected, predicted, case_id)

        metrics = self._metrics_module
        r5 = metrics.recall_at_k(expected, predicted, k=5)
        r10 = metrics.recall_at_k(expected, predicted, k=10)
        mrr = metrics.reciprocal_rank(expected, predicted)

        return {
            "case_id": case_id,
            "expected_files": expected,
            "predicted_files": predicted,
            "score": round(r10 * 0.7 + mrr * 0.3, 6),
            "recall_at_5": r5,
            "recall_at_10": r10,
            "mrr": mrr,
        }

    def _stub_metrics(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> MetricsResult:
        """Stub metrics when module unavailable."""
        return {
            "case_id": case_id,
            "expected_files": expected,
            "predicted_files": predicted,
            "score": 0.0,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "mrr": 0.0,
        }


class _DefaultContextCompiler:
    """Default context compiler using kernelone.context."""

    __slots__ = ()

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        """Compile using kernelone context pipeline."""
        try:
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
        except ImportError:
            return ContextCompilationResult(selected_files=())


class _StubContextCompiler:
    """Stub compiler when context pipeline unavailable."""

    __slots__ = ()

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        """Return empty result."""
        return ContextCompilationResult(selected_files=())


class ContextCompilationError(Exception):
    """Raised when context compilation fails.

    This exception propagates from compilation failures,
    ensuring fail-closed behavior in the benchmark.
    """

    __slots__ = ()

    def __init__(self, message: str) -> None:
        super().__init__(message)


# ------------------------------------------------------------------
# Type Aliases for Module Reference
# ------------------------------------------------------------------

if TYPE_CHECKING:
    MetricsModule: TypeAlias = type[
        "polaris.infrastructure.accel.eval.metrics"
    ]
```

#### 产出 2: Fixture Mapper (`context_fixture_mapper.py`)

```python
"""Context Benchmark Fixture Mapper.

Maps legacy fixture format (infrastructure.accel.eval) to
UnifiedBenchmarkCase format.

Legacy format:
    {
        "case_id": "...",
        "task": "...",
        "expected_files": [...],
    }

Unified format:
    UnifiedBenchmarkCase(case_id, role, title, prompt, judge, ...)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import TypeAlias

from polaris.kernelone.benchmark.unified_models import (
    BenchmarkMode,
    BudgetConditions,
    JudgeConfig,
    UnifiedBenchmarkCase,
)

if TYPE_CHECKING:
    pass


LegacyFixture: TypeAlias = dict[str, Any]


class ContextFixtureMapperError(Exception):
    """Raised when fixture mapping fails."""

    __slots__ = ()


class ContextFixtureMapper:
    """Maps legacy context fixtures to UnifiedBenchmarkCase.

    Design Patterns:
        - Mapper Pattern: 数据格式转换
        - Validation: 输入校验

    Example:
        mapper = ContextFixtureMapper()
        unified = mapper.map(legacy_fixture)
    """

    DEFAULT_ROLE: str = "director"
    DEFAULT_THRESHOLD: float = 0.70

    __slots__ = ("_validator",)

    def __init__(self) -> None:
        self._validator = _FixtureValidator()

    def map(self, legacy_fixture: LegacyFixture) -> UnifiedBenchmarkCase:
        """Map legacy fixture to UnifiedBenchmarkCase.

        Args:
            legacy_fixture: Fixture in legacy format.

        Returns:
            UnifiedBenchmarkCase instance.

        Raises:
            ContextFixtureMapperError: If validation fails.
        """
        try:
            self._validator.validate(legacy_fixture)
        except _FixtureValidationError as exc:
            raise ContextFixtureMapperError(
                f"Fixture validation failed: {exc}"
            ) from exc

        case_id = str(legacy_fixture["case_id"]).strip()
        task = str(legacy_fixture["task"]).strip()
        expected_files = tuple(
            str(f) for f in legacy_fixture.get("expected_files", [])
            if str(f).strip()
        )

        return UnifiedBenchmarkCase(
            case_id=case_id,
            role=legacy_fixture.get("role", self.DEFAULT_ROLE),
            title=case_id.replace("_", " ").title(),
            prompt=task,
            description=str(legacy_fixture.get("description", "")),
            expected_evidence_path=expected_files,
            workspace_fixture=str(
                legacy_fixture.get("workspace_fixture", "")
            ),
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

    def map_batch(
        self,
        legacy_fixtures: list[LegacyFixture],
    ) -> list[UnifiedBenchmarkCase]:
        """Map multiple legacy fixtures.

        Args:
            legacy_fixtures: List of legacy fixtures.

        Returns:
            List of UnifiedBenchmarkCase instances.
        """
        return [self.map(fixture) for fixture in legacy_fixtures]


class _FixtureValidationError(Exception):
    """Internal validation error."""
    __slots__ = ()


class _FixtureValidator:
    """Validates legacy fixture format."""

    __slots__ = ()

    def validate(self, fixture: LegacyFixture) -> None:
        """Validate legacy fixture has required fields.

        Args:
            fixture: Legacy fixture to validate.

        Raises:
            _FixtureValidationError: If validation fails.
        """
        case_id = str(fixture.get("case_id", "")).strip()
        if not case_id:
            raise _FixtureValidationError("case_id is required")

        task = str(fixture.get("task", "")).strip()
        if not task:
            raise _FixtureValidationError(
                f"{case_id}: task is required"
            )
```

---

## 5. Phase C: 质检方案

### 5.1 QA Automation 测试架构

```python
# polaris/kernelone/benchmark/tests/test_context_adapter.py
"""Context Benchmark Adapter Tests.

Test Strategy:
- Unit Tests: 独立测试 adapter.evaluate()
- Integration Tests: 测试与 metrics 模块集成
- Error Handling Tests: 测试异常传播
- Fixture Mapping Tests: 测试格式转换
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
    MetricsCalculatorProtocol,
)
from polaris.kernelone.benchmark.adapters.context_fixture_mapper import (
    ContextFixtureMapper,
    ContextFixtureMapperError,
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
    """Legacy fixture format for mapping tests."""
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
# Test Cases
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
            compiler=_FakeCompiler(
                selected_files=["src/scoring.py", "src/receipts.py"]
            ),
            metrics_calculator=_FakeMetricsCalculator(),
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
            compiler=_FakeCompiler(
                selected_files=list(sample_case.expected_evidence_path)
            ),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        result = adapter.evaluate(sample_case, str(tmp_path))
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
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
            compiler=_FakeCompiler(
                selected_files=["src/scoring.py"]  # 1 of 2
            ),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        result = adapter.evaluate(sample_case, str(tmp_path))
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        assert 0.0 < eval_data["score"] < 1.0
        assert eval_data["recall_at_10"] == 0.5

    def test_evaluate_with_empty_prediction(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """Empty prediction returns zero score."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=[]),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        result = adapter.evaluate(sample_case, str(tmp_path))
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        assert eval_data["score"] == 0.0
        assert eval_data["recall_at_5"] == 0.0
        assert eval_data["mrr"] == 0.0

    def test_evaluate_compilation_error_propagates(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """ContextCompilationError propagates on failure."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FailingCompiler(),
            metrics_calculator=_FakeMetricsCalculator(),
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
            compiler=_FakeCompiler(selected_files=[]),
            metrics_calculator=_FakeMetricsCalculator(),
        )
        adapter.evaluate(sample_case, str(tmp_path))

        assert adapter.get_evaluation(sample_case.case_id) is not None

        adapter.clear_evaluations()

        assert adapter.get_evaluation(sample_case.case_id) is None

    def test_di_with_custom_compiler(
        self,
        sample_case: UnifiedBenchmarkCase,
        tmp_path: Path,
    ) -> None:
        """DI accepts custom compiler implementation."""
        custom_compiler = _CustomFileListCompiler(
            files=["custom/file.py"]
        )
        adapter = ContextBenchmarkAdapter(compiler=custom_compiler)

        result = adapter.evaluate(sample_case, str(tmp_path))

        assert result.case_id == sample_case.case_id


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

    def test_maps_minimal_fixture(self) -> None:
        """Minimal legacy fixture uses defaults."""
        mapper = ContextFixtureMapper()
        unified = mapper.map({
            "case_id": "minimal",
            "task": "Do something",
        })

        assert unified.role == "director"  # Default
        assert unified.judge.score_threshold == 0.70  # Default

    def test_raises_on_missing_case_id(self) -> None:
        """Missing case_id raises ContextFixtureMapperError."""
        mapper = ContextFixtureMapper()

        with pytest.raises(ContextFixtureMapperError, match="case_id"):
            mapper.map({"task": "test"})

    def test_raises_on_missing_task(self) -> None:
        """Missing task raises ContextFixtureMapperError."""
        mapper = ContextFixtureMapper()

        with pytest.raises(ContextFixtureMapperError, match="task"):
            mapper.map({"case_id": "test"})

    def test_batch_mapping(
        self,
        legacy_fixture: dict[str, Any],
    ) -> None:
        """map_batch() converts multiple fixtures."""
        mapper = ContextFixtureMapper()
        fixtures = [legacy_fixture, {"case_id": "b", "task": "Task B"}]
        results = mapper.map_batch(fixtures)

        assert len(results) == 2
        assert results[0].case_id == "test_context_case"
        assert results[1].case_id == "b"


# ------------------------------------------------------------------
# Test Fakes (Dependency Injection Stubs)
# ------------------------------------------------------------------

class _FakeCompiler(ContextCompilerProtocol):
    """Fake compiler for tests."""

    __slots__ = ("_files",)

    def __init__(self, selected_files: list[str]) -> None:
        self._files = selected_files

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        return ContextCompilationResult(selected_files=tuple(self._files))


class _FailingCompiler(ContextCompilerProtocol):
    """Compiler that always fails for error handling tests."""

    __slots__ = ()

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        msg = "Simulated compilation failure"
        raise ContextCompilationError(msg)


class _CustomFileListCompiler(ContextCompilerProtocol):
    """Custom compiler that returns predefined file list."""

    __slots__ = ("_files",)

    def __init__(self, files: list[str]) -> None:
        self._files = files

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        return ContextCompilationResult(selected_files=tuple(self._files))


class _FakeMetricsCalculator(MetricsCalculatorProtocol):
    """Fake metrics calculator for tests.

    Returns deterministic metrics based on overlap.
    """

    __slots__ = ()

    def calculate(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> dict[str, Any]:
        """Calculate metrics with perfect recall calculation."""
        expected_set = set(expected)
        predicted_set = set(predicted)

        overlap = len(expected_set & predicted_set)
        total_expected = len(expected_set)

        recall = overlap / total_expected if total_expected > 0 else 0.0
        mrr = 1.0 / (list(predicted_set)[0] in expected_set) if predicted_set else 0.0

        return {
            "case_id": case_id,
            "expected_files": expected,
            "predicted_files": predicted,
            "score": round(recall * 0.7 + mrr * 0.3, 6),
            "recall_at_5": recall,
            "recall_at_10": recall,
            "mrr": mrr if predicted_set else 0.0,
        }
```

---

## 6. 团队协作流程

### 6.1 日常 stand-up (异步)

```markdown
## Stand-up [日期]

### @CTO
- [x] 确认 Task 1 优先级为 P0
- [ ] 审查 Task 2 架构设计

### @Principal Engineer
- [x] 完成 Context adapter 契约设计
- [ ] 审查 DI 注入实现

### @Refactoring Guru
- [x] 实现 Context adapter 重写
- [ ] 代码审查 @Typing 输出

### @Typing Specialist
- [x] 添加 Protocol 定义
- [ ] mypy --strict 验证

### @QA Automation
- [ ] 编写测试用例
- [ ] 覆盖率分析

[... 其他角色 ...]
```

### 6.2 PR 审查清单

| 检查项 | 负责人 | 通过标准 |
|--------|--------|----------|
| mypy --strict | @Typing | 0 errors |
| ruff check/format | @Auditor | 0 warnings |
| pytest -v | @QA | 100% pass |
| Docstrings | @Docs | Google style |
| DI 注入 | @Principal | Protocol 使用 |
| 异常处理 | @Security | 无 bare except |

---

## 7. 验收标准

| 角色 | 验收项 | 标准 |
|------|--------|------|
| **CTO** | 技术选型合理性 | 架构可扩展 3 年 |
| **Principal Engineer** | SOLID/DRY 遵循 | 代码审查通过 |
| **Security Lead** | 注入漏洞扫描 | 0 vulnerabilities |
| **Refactoring Guru** | Pythonic 代码 | 无 C-style 循环 |
| **Typing Specialist** | mypy 严格检查 | 0 errors |
| **QA Automation** | 测试覆盖率 | >= 90% |
| **Documentation Lead** | Docstrings | Google style |
| **Framework SME** | FastAPI/SQLAlchemy | 0 N+1 查询 |
| **Code Auditor** | PEP 8 扫描 | 0 violations |
| **DevOps Integrator** | CI/CD | 门禁通过 |

---

## 8. 执行时间线

```
Week 1 (2026-03-30 ~ 2026-04-03)
├── Day 1: Phase A 诊断 + 任务分配
├── Day 2: @Refactor + @Typing 实现 Context adapter
├── Day 3: @QA 编写测试 + CI/CD 集成
├── Day 4: 全团队代码审查
└── Day 5: PR merge + Task 1 完成 ✅

Week 2 (2026-04-06 ~ 2026-04-10)
├── Day 1: Task 2 CLI 重写开始
├── Day 2: Task 3 性能对比基准建立
├── Day 3: Task 4 回归测试执行
├── Day 4: Task 5 归档决策
└── Day 5: 全部任务完成 ✅

交付物:
✅ Context adapter 真实实现（metrics 集成）
✅ 20+ 测试用例
✅ Fixture mapper
✅ CLI 入口重写
✅ 性能基准
✅ 回归测试 100% 通过
```

---

## 9. 联系方式与 Escalation

| 角色 | 专家 | Escalation 条件 |
|------|------|-----------------|
| CTO | @CTO | 跨团队阻塞 |
| Principal Engineer | @PE | 架构决策争议 |
| Security Lead | @Security | 安全漏洞 |
| Refactoring Guru | @Refactor | 重构技术难题 |
| Typing Specialist | @Typing | 类型系统复杂问题 |
| QA Automation | @QA | 测试覆盖率不达标 |
| Documentation Lead | @Docs | 文档缺失 |
| Framework SME | @Framework | 中间件问题 |
| Code Auditor | @Auditor | PEP 8 争议 |
| DevOps Integrator | @DevOps | CI/CD 失败 |
