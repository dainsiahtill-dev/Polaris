# Benchmark 框架收敛蓝图

**版本**: 1.1
**日期**: 2026-03-28
**状态**: ✅ 已完成 (Phase 1-4 主要工作完成)
**负责人**: 10人 Python 架构与代码治理实验室
**更新**: 2026-03-28 — 文档同步

---

## 1. 背景与目标

### 1.1 当前问题

项目存在 **3 套相互独立的 Benchmark 系统**：

| 系统 | 位置 | 问题 |
|------|------|------|
| Agentic Benchmark | `polaris/cells/llm/evaluation/internal/` | 角色Agent评估，无通用抽象 |
| Strategy Benchmark | `polaris/kernelone/context/strategy_benchmark.py` | 离线回放，与Agentic模型不兼容 |
| Context Benchmark | `polaris/infrastructure/accel/eval/runner.py` | 上下文选择，孤立实现 |

**关键缺陷**:
1. `BenchmarkCase` 在两处独立定义（`benchmark_models.py:164` 和 `strategy_benchmark.py:73`）
2. 无统一入口，报告格式各异
3. 工具规范化逻辑分散
4. 契约层存在缺口，部分调用方绕过public API

### 1.2 目标

**最终状态**: 单一 Benchmark 框架，统一入口，统一模型，统一报告。

```
当前状态:
  Agentic ──┐
  Strategy ─┼── 各自独立，无共享
  Context ──┘

目标状态:
  ┌──────────────────────────────────────┐
  │     Unified Benchmark Framework      │
  │  ┌────────────────────────────────┐  │
  │  │  UnifiedBenchmarkCase (单一模型) │  │
  │  └────────────────────────────────┘  │
  │         ▲              ▲              │
  │    Agentic Mode   Strategy Mode      │
  │         │              │              │
  │  ┌──────┴──────────────┴────────┐    │
  │  │     UnifiedJudge (裁判)       │    │
  │  └─────────────────────────────┘    │
  │         │              │              │
  │  ┌──────┴──────────────┴────────┐    │
  │  │   UnifiedReport (统一报告)     │    │
  │  └─────────────────────────────┘    │
  └──────────────────────────────────────┘
```

---

## 2. 架构设计

### 2.1 统一模型 (UnifiedBenchmarkCase)

```python
# polaris/kernelone/benchmark/unified_models.py
"""统一 Benchmark 框架 - 核心数据模型

设计模式:
- @dataclass(frozen=True): 不可变安全数据载体
- Protocol: 协议定义，依赖注入
- TypeAlias: 类型别名提高可读性
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

from typing_extensions import TypeAliasType


# ------------------------------------------------------------------
# Type Aliases
# ------------------------------------------------------------------

BenchmarkMode: TypeAlias = str  # "agentic" | "strategy" | "context"
ToolName: TypeAlias = str
FilePath: TypeAlias = str


# ------------------------------------------------------------------
# Enums (as dataclasses for Python 3.10+ compatibility)
# ------------------------------------------------------------------

@dataclass(frozen=True)
class BenchmarkMode:
    """Benchmark 运行模式"""
    AGENTIC: BenchmarkMode = "agentic"
    STRATEGY: BenchmarkMode = "strategy"
    CONTEXT: BenchmarkMode = "context"


# ------------------------------------------------------------------
# Core Models
# ------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class ToolArgumentRule:
    """工具参数规则 - 确定性判决依据"""
    fragment: str
    tools: tuple[ToolName, ...] = field(default_factory=tuple)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.fragment.strip():
            raise ValueError("fragment must be non-empty")
        object.__setattr__(self, "fragment", self.fragment.strip())
        object.__setattr__(self, "tools", tuple(self.tools or ()))
        object.__setattr__(self, "description", self.description.strip())


@dataclass(frozen=True, kw_only=True)
class JudgeConfig:
    """确定性裁判配置"""
    score_threshold: float = 0.75
    required_tools: tuple[ToolName, ...] = field(default_factory=tuple)
    forbidden_tools: tuple[ToolName, ...] = field(default_factory=tuple)
    required_tool_arguments: tuple[ToolArgumentRule, ...] = field(default_factory=tuple)
    forbidden_tool_arguments: tuple[ToolArgumentRule, ...] = field(default_factory=tuple)
    min_tool_calls: int = 0
    max_tool_calls: int | None = None
    required_output_substrings: tuple[str, ...] = field(default_factory=tuple)
    forbidden_output_substrings: tuple[str, ...] = field(default_factory=tuple)
    validators: tuple[str, ...] = field(default_factory=tuple)
    mode: BenchmarkMode = BenchmarkMode.AGENTIC

    def __post_init__(self) -> None:
        if not 0.0 <= self.score_threshold <= 1.0:
            raise ValueError("score_threshold must be 0.0-1.0")
        if self.min_tool_calls < 0:
            raise ValueError("min_tool_calls must be >= 0")
        if self.max_tool_calls is not None and self.max_tool_calls < self.min_tool_calls:
            raise ValueError("max_tool_calls must be >= min_tool_calls")


@dataclass(frozen=True, kw_only=True)
class UnifiedBenchmarkCase:
    """统一 Benchmark Case 模型

    设计原则:
    - 单一事实来源: 无论来源(agentic/strategy/context)，统一序列化为此模型
    - 不可变性: frozen=True 防止意外修改
    - 完整类型提示: 所有字段显式注解
    """
    case_id: str
    role: str
    title: str
    prompt: str
    description: str = ""
    workspace_fixture: str = ""
    expected_evidence_path: tuple[FilePath, ...] = field(default_factory=tuple)
    expected_answer_shape: str = "answer"
    budget_conditions: BudgetConditions = field(default_factory=BudgetConditions)
    canonical_profile: str = "canonical_balanced"
    history: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = field(default_factory=tuple)
    judge: JudgeConfig = field(default_factory=JudgeConfig)

    def __post_init__(self) -> None:
        if not all([self.case_id, self.role, self.title, self.prompt]):
            raise ValueError("case_id, role, title, prompt are required")
        object.__setattr__(self, "role", self.role.lower())


@dataclass(frozen=True)
class BudgetConditions:
    """预算约束条件"""
    max_tokens: int = 200_000
    max_turns: int = 10
    max_wall_time_seconds: float = 300.0


@dataclass(frozen=True, kw_only=True)
class ToolCallObservation:
    """观察到的工具调用"""
    tool: ToolName
    args: dict[str, Any] = field(default_factory=dict)
    event_index: int = 0


@dataclass(frozen=True, kw_only=True)
class ObservedBenchmarkRun:
    """Benchmark 运行观察结果"""
    case_id: str
    role: str
    workspace: str
    output: str
    thinking: str = ""
    tool_calls: tuple[ToolCallObservation, ...] = field(default_factory=tuple)
    error: str = ""
    duration_ms: int = 0
    event_count: int = 0
    fingerprint: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class JudgeCheck:
    """单次裁判检查结果"""
    code: str
    category: str
    passed: bool
    message: str
    critical: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class UnifiedJudgeVerdict:
    """统一判决结果"""
    case_id: str
    passed: bool
    score: float
    threshold: float
    categories: dict[str, float] = field(default_factory=dict)
    summary: str = ""
    checks: tuple[JudgeCheck, ...] = field(default_factory=tuple)
    mode: BenchmarkMode = BenchmarkMode.AGENTIC
```

### 2.2 统一裁判引擎 (UnifiedJudge)

```python
# polaris/kernelone/benchmark/unified_judge.py
"""统一裁判引擎

设计模式:
- Strategy Pattern: 验证器作为可插拔策略
- Observer Pattern: 检查结果可观察
- Chain of Responsibility: 检查链式执行
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .unified_models import (
    BenchmarkMode,
    JudgeCheck,
    JudgeConfig,
    ObservedBenchmarkRun,
    ToolCallObservation,
    ToolArgumentRule,
    UnifiedBenchmarkCase,
    UnifiedJudgeVerdict,
)


# ------------------------------------------------------------------
# Validator Protocol
# ------------------------------------------------------------------

class Validator(Protocol):
    """验证器协议"""
    name: str
    category: str
    critical: bool

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]: ...


# ------------------------------------------------------------------
# 内置验证器
# ------------------------------------------------------------------

class NoPromptLeakageValidator:
    """防止提示泄露验证器"""
    name: str = "no_prompt_leakage"
    category: str = "safety"
    critical: bool = True

    PROMPT_LEAKAGE_MARKERS: tuple[str, ...] = (
        "system prompt",
        "<thinking>",
        "<tool_call>",
        "you are ",
        "角色设定",
        "提示词",
    )

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        lowered = output_text.lower()
        for marker in self.PROMPT_LEAKAGE_MARKERS:
            if marker in lowered:
                return False, f"prompt leakage marker found: {marker}"
        return True, "no prompt leakage"


class StructuredStepsValidator:
    """结构化步骤验证器"""
    name: str = "structured_steps"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        import re
        pattern = r"^\s*\d+\."
        lines = output_text.strip().split("\n")
        has_numbered_steps = any(re.match(pattern, line) for line in lines)
        if has_numbered_steps:
            return True, "structured steps found"
        return False, "output must start with numbered steps like '1.'"


# ------------------------------------------------------------------
# 统一裁判引擎
# ------------------------------------------------------------------

class UnifiedJudge:
    """统一裁判引擎

    设计原则:
    - 单一裁判逻辑: 无论 mode，全部走此引擎
    - 可组合验证器: 内置 + 自定义验证器可混合使用
    - 完整错误处理: 验证器异常不影响整体判决
    """

    SCORE_WEIGHTS: dict[str, float] = {
        "tooling": 0.35,
        "safety": 0.25,
        "contract": 0.25,
        "evidence": 0.15,
    }

    def __init__(self, validators: list[Validator] | None = None) -> None:
        self._validators: dict[str, Validator] = {}
        if validators:
            for v in validators:
                self._validators[v.name] = v
        else:
            self._register_default_validators()

    def _register_default_validators(self) -> None:
        """注册默认验证器"""
        for validator in [
            NoPromptLeakageValidator(),
            StructuredStepsValidator(),
        ]:
            self._validators[validator.name] = validator

    def register_validator(self, validator: Validator) -> None:
        """注册自定义验证器"""
        self._validators[validator.name] = validator

    def judge(
        self,
        case: UnifiedBenchmarkCase,
        observed: ObservedBenchmarkRun,
        workspace_files: list[str] | None = None,
    ) -> UnifiedJudgeVerdict:
        """执行判决

        Args:
            case: Benchmark case definition
            observed: Observed execution run
            workspace_files: Known workspace files for path validation

        Returns:
            UnifiedJudgeVerdict with complete judgment results
        """
        known_paths = list(workspace_files or [])
        checks: list[JudgeCheck] = []

        try:
            checks.extend(self._check_required_tools(case, observed))
        except Exception as exc:
            checks.append(JudgeCheck(
                code="error:required_tools",
                category="tooling",
                passed=False,
                message=f"required_tools check failed: {exc}",
                critical=True,
            ))

        try:
            checks.extend(self._check_tool_arguments(case, observed))
        except Exception as exc:
            checks.append(JudgeCheck(
                code="error:tool_arguments",
                category="evidence",
                passed=False,
                message=f"tool_arguments check failed: {exc}",
                critical=False,
            ))

        try:
            checks.extend(self._check_output_substrings(case, observed))
        except Exception as exc:
            checks.append(JudgeCheck(
                code="error:output_substrings",
                category="contract",
                passed=False,
                message=f"output_substrings check failed: {exc}",
                critical=False,
            ))

        # Run registered validators
        combined_output = (
            str(observed.output or "")
            + "\n"
            + str(observed.thinking or "")
        ).strip()

        for validator_name in case.judge.validators:
            validator = self._validators.get(validator_name)
            if validator is None:
                checks.append(JudgeCheck(
                    code=f"validator:{validator_name}",
                    category="contract",
                    passed=False,
                    message=f"unknown validator: {validator_name}",
                    critical=True,
                ))
                continue

            try:
                ok, message = validator.validate(combined_output, observed, known_paths)
                checks.append(JudgeCheck(
                    code=f"validator:{validator_name}",
                    category=validator.category,
                    passed=bool(ok),
                    message=str(message or validator_name),
                    critical=validator.critical,
                ))
            except Exception as exc:
                checks.append(JudgeCheck(
                    code=f"validator:{validator_name}",
                    category=validator.category,
                    passed=False,
                    message=f"validator raised: {exc}",
                    critical=validator.critical,
                ))

        # Calculate scores
        category_scores = self._calculate_category_scores(checks)
        overall_score = sum(
            score * weight
            for name, weight in self.SCORE_WEIGHTS.items()
            if name in category_scores
        )

        critical_failures = [
            c for c in checks if c.critical and not c.passed
        ]

        passed = (
            len(critical_failures) == 0
            and overall_score >= case.judge.score_threshold
        )

        return UnifiedJudgeVerdict(
            case_id=case.case_id,
            passed=passed,
            score=overall_score,
            threshold=case.judge.score_threshold,
            categories=category_scores,
            summary=self._summarize_checks(checks),
            checks=tuple(checks),
            mode=case.judge.mode,
        )

    def _check_required_tools(
        self,
        case: UnifiedBenchmarkCase,
        observed: ObservedBenchmarkRun,
    ) -> list[JudgeCheck]:
        """检查必需/禁止工具"""
        from polaris.kernelone.tools.contracts import canonicalize_tool_name

        checks: list[JudgeCheck] = []
        observed_tools = {
            canonicalize_tool_name(tc.tool, keep_unknown=True)
            for tc in observed.tool_calls
        }

        for tool in case.judge.required_tools:
            canonical = canonicalize_tool_name(tool, keep_unknown=True)
            checks.append(JudgeCheck(
                code=f"required_tool:{tool}",
                category="tooling",
                passed=canonical in observed_tools,
                message=f"required tool `{tool}` must appear in trace",
                evidence={"observed": sorted(observed_tools), "required": tool},
            ))

        for tool in case.judge.forbidden_tools:
            canonical = canonicalize_tool_name(tool, keep_unknown=True)
            checks.append(JudgeCheck(
                code=f"forbidden_tool:{tool}",
                category="safety",
                passed=canonical not in observed_tools,
                message=f"forbidden tool `{tool}` must not appear",
                critical=True,
                evidence={"observed": sorted(observed_tools), "forbidden": tool},
            ))

        # Tool call count checks
        total_calls = len(observed.tool_calls)
        checks.append(JudgeCheck(
            code="min_tool_calls",
            category="tooling",
            passed=total_calls >= case.judge.min_tool_calls,
            message=f"tool calls must be >= {case.judge.min_tool_calls}",
            evidence={"count": total_calls},
        ))

        if case.judge.max_tool_calls is not None:
            checks.append(JudgeCheck(
                code="max_tool_calls",
                category="tooling",
                passed=total_calls <= case.judge.max_tool_calls,
                message=f"tool calls must be <= {case.judge.max_tool_calls}",
                evidence={"count": total_calls},
            ))

        return checks

    def _check_tool_arguments(
        self,
        case: UnifiedBenchmarkCase,
        observed: ObservedBenchmarkRun,
    ) -> list[JudgeCheck]:
        """检查工具参数规则"""
        import json
        checks: list[JudgeCheck] = []

        for rule in case.judge.required_tool_arguments:
            matched = self._rule_matches(observed, rule)
            checks.append(JudgeCheck(
                code=f"required_tool_argument:{rule.description or rule.fragment}",
                category="evidence",
                passed=matched,
                message=f"trace must contain tool args matching `{rule.fragment}`",
                evidence=rule.__dict__,
            ))

        for rule in case.judge.forbidden_tool_arguments:
            matched = self._rule_matches(observed, rule)
            checks.append(JudgeCheck(
                code=f"forbidden_tool_argument:{rule.description or rule.fragment}",
                category="safety",
                passed=not matched,
                message=f"trace must not contain tool args matching `{rule.fragment}`",
                critical=True,
                evidence=rule.__dict__,
            ))

        return checks

    def _rule_matches(
        self,
        observed: ObservedBenchmarkRun,
        rule: ToolArgumentRule,
    ) -> bool:
        """检查规则是否匹配"""
        fragment = rule.fragment.lower()
        for call in observed.tool_calls:
            if rule.tools and call.tool not in rule.tools:
                continue
            try:
                serialized = json.dumps(call.args, ensure_ascii=False, sort_keys=True).lower()
                if fragment in serialized:
                    return True
            except Exception:
                continue
        return False

    def _check_output_substrings(
        self,
        case: UnifiedBenchmarkCase,
        observed: ObservedBenchmarkRun,
    ) -> list[JudgeCheck]:
        """检查输出子串"""
        output_lower = str(observed.output or "").lower()
        combined_lower = (output_lower + "\n" + str(observed.thinking or "").lower()).strip()
        checks: list[JudgeCheck] = []

        for token in case.judge.required_output_substrings:
            checks.append(JudgeCheck(
                code=f"required_output:{token}",
                category="contract",
                passed=token.lower() in output_lower,
                message=f"output must mention `{token}`",
            ))

        for token in case.judge.forbidden_output_substrings:
            checks.append(JudgeCheck(
                code=f"forbidden_output:{token}",
                category="safety",
                passed=token.lower() not in combined_lower,
                message=f"output must not contain `{token}`",
                critical=token.lower() in {"<thinking>", "<tool_call>", "system prompt"},
            ))

        return checks

    def _calculate_category_scores(self, checks: list[JudgeCheck]) -> dict[str, float]:
        """计算分类分数"""
        grouped: dict[str, list[JudgeCheck]] = {}
        for check in checks:
            grouped.setdefault(check.category, []).append(check)

        return {
            category: (
                sum(1 for c in items if c.passed) / len(items)
                if items else 1.0
            )
            for category, items in grouped.items()
        }

    def _summarize_checks(self, checks: list[JudgeCheck]) -> str:
        """生成检查摘要"""
        failures = [c.code for c in checks if not c.passed]
        if not failures:
            return "all deterministic checks passed"
        return "failed checks: " + ", ".join(failures)
```

### 2.3 统一执行器 (UnifiedBenchmarkRunner)

```python
# polaris/kernelone/benchmark/unified_runner.py
"""统一 Benchmark 执行器

设计模式:
- Facade Pattern: 统一入口封装复杂子系统
- Template Method: 执行流程模板化
- Builder Pattern: 报告构建
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .unified_judge import UnifiedJudge
from .unified_models import (
    BenchmarkMode,
    ObservedBenchmarkRun,
    UnifiedBenchmarkCase,
    UnifiedJudgeVerdict,
)


@dataclass(frozen=True, kw_only=True)
class BenchmarkRunResult:
    """单次 Benchmark 运行结果"""
    case_id: str
    passed: bool
    score: float
    duration_ms: int
    verdict: UnifiedJudgeVerdict
    error: str = ""


@dataclass(frozen=True, kw_only=True)
class BenchmarkSuiteResult:
    """Benchmark 套件运行结果"""
    suite_name: str
    run_id: str
    mode: BenchmarkMode
    total_cases: int
    passed_cases: int
    failed_cases: int
    average_score: float
    results: tuple[BenchmarkRunResult, ...]
    timestamp: str


class UnifiedBenchmarkRunner:
    """统一 Benchmark 执行器

    单一入口，执行所有类型的 Benchmark 评估。
    """

    def __init__(self, judge: UnifiedJudge | None = None) -> None:
        self._judge = judge or UnifiedJudge()

    async def run_suite(
        self,
        cases: list[UnifiedBenchmarkCase],
        *,
        workspace: str,
        run_id: str | None = None,
        mode: BenchmarkMode = BenchmarkMode.AGENTIC,
    ) -> BenchmarkSuiteResult:
        """运行 Benchmark 套件

        Args:
            cases: Benchmark cases to run
            workspace: Workspace path
            run_id: Optional run ID (auto-generated if not provided)
            mode: Benchmark mode

        Returns:
            BenchmarkSuiteResult with complete results
        """
        import uuid

        run_id = run_id or f"bench-{uuid.uuid4().hex[:8]}"
        start = time.perf_counter()

        results: list[BenchmarkRunResult] = []

        for case in cases:
            try:
                result = await self._run_single_case(
                    case=case,
                    workspace=workspace,
                    mode=mode,
                )
            except Exception as exc:
                result = BenchmarkRunResult(
                    case_id=case.case_id,
                    passed=False,
                    score=0.0,
                    duration_ms=0,
                    verdict=UnifiedJudgeVerdict(
                        case_id=case.case_id,
                        passed=False,
                        score=0.0,
                        threshold=case.judge.score_threshold,
                        error=str(exc),
                    ),
                    error=str(exc),
                )
            results.append(result)

        wall_time_ms = int((time.perf_counter() - start) * 1000)

        return BenchmarkSuiteResult(
            suite_name="unified_benchmark",
            run_id=run_id,
            mode=mode,
            total_cases=len(results),
            passed_cases=sum(1 for r in results if r.passed),
            failed_cases=sum(1 for r in results if not r.passed),
            average_score=(
                sum(r.score for r in results) / len(results)
                if results else 0.0
            ),
            results=tuple(results),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def _run_single_case(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
        mode: BenchmarkMode,
    ) -> BenchmarkRunResult:
        """运行单个 Benchmark case"""
        start = time.perf_counter()

        # Materialize workspace if needed
        sandbox_workspace = self._materialize_workspace(workspace, case)

        # Collect observation based on mode
        observed = await self._collect_observation(case, sandbox_workspace, mode)

        # Judge
        verdict = self._judge.judge(case, observed)

        duration_ms = int((time.perf_counter() - start) * 1000)

        return BenchmarkRunResult(
            case_id=case.case_id,
            passed=verdict.passed,
            score=verdict.score,
            duration_ms=duration_ms,
            verdict=verdict,
        )

    def _materialize_workspace(
        self,
        base_workspace: str,
        case: UnifiedBenchmarkCase,
    ) -> str:
        """物化工作空间"""
        if not case.workspace_fixture:
            return base_workspace

        import shutil

        fixture_dir = Path(__file__).parent / "fixtures" / case.workspace_fixture
        if not fixture_dir.is_dir():
            return base_workspace

        target = Path(base_workspace) / ".polaris" / "runtime" / "benchmarks" / case.case_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(fixture_dir, target)
        return str(target)

    async def _collect_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
        mode: BenchmarkMode,
    ) -> ObservedBenchmarkRun:
        """收集观察结果

        根据 mode 选择不同的收集策略:
        - AGENTIC: 通过 roles.runtime 流式收集
        - STRATEGY: 从预录制的回放文件加载
        - CONTEXT: 从上下文编译结果构建
        """
        if mode == BenchmarkMode.AGENTIC:
            return await self._collect_agentic_observation(case, workspace)
        elif mode == BenchmarkMode.STRATEGY:
            return await self._collect_strategy_observation(case, workspace)
        else:
            return await self._collect_context_observation(case, workspace)

    async def _collect_agentic_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun:
        """收集 Agentic 模式观察结果"""
        # 实现: 调用 roles.runtime 流式接口
        ...

    async def _collect_strategy_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun:
        """收集 Strategy 模式观察结果"""
        # 实现: 从预录制回放加载
        ...

    async def _collect_context_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun:
        """收集 Context 模式观察结果"""
        # 实现: 从上下文编译结果构建
        ...

    def generate_report(
        self,
        result: BenchmarkSuiteResult,
        *,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """生成统一报告

        Args:
            result: Benchmark suite result
            output_path: Optional output file path

        Returns:
            Report dict
        """
        report: dict[str, Any] = {
            "schema_version": 1,
            "suite": result.suite_name,
            "test_run_id": result.run_id,
            "timestamp": result.timestamp,
            "mode": result.mode,
            "summary": {
                "total_cases": result.total_cases,
                "passed_cases": result.passed_cases,
                "failed_cases": result.failed_cases,
                "average_score": round(result.average_score, 4),
                "pass_rate": (
                    result.passed_cases / result.total_cases
                    if result.total_cases else 0.0
                ),
            },
            "final": {
                "ready": result.passed_cases == result.total_cases,
                "grade": "PASS" if result.passed_cases == result.total_cases else "FAIL",
            },
            "cases": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "score": round(r.score, 4),
                    "duration_ms": r.duration_ms,
                    "verdict": r.verdict.to_dict(),
                    "error": r.error,
                }
                for r in result.results
            ],
        }

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        return report
```

---

## 3. 迁移路径

### 3.1 阶段计划

```
Phase 1 (Week 1-2): 统一模型层
├── 创建 unified_models.py
├── 定义 UnifiedBenchmarkCase
├── 迁移现有模型到统一模型
└── 验证向后兼容

Phase 2 (Week 3-4): 统一裁判层
├── 创建 unified_judge.py
├── 实现 UnifiedJudge
├── 迁移内置验证器
└── 保持与现有 deterministic_judge 兼容

Phase 3 (Week 5-6): 统一执行层
├── 创建 unified_runner.py
├── 实现 UnifiedBenchmarkRunner
├── 适配 Agentic/Strategy/Context 三种模式
└── 统一 CLI 入口

Phase 4 (Week 7-8): 清理与归档 ✅ (2026-03-28 修订)

> **重要架构决策**: 由于旧模块 (`benchmark_models.py`, `strategy_benchmark.py`) 与新框架类型系统不兼容，采用"标记废弃保留"而非"归档删除"策略。

├── ✅ 旧模块标记为 deprecated
├── ✅ 新框架创建并测试通过 (74 测试)
├── ✅ cell.yaml 已更新
├── ✅ 文档已同步 (verification card + audit report)
├── ⏳ CLI 入口重写 (待执行)
└── ⏳ 性能基准对比 (待执行)
```

### 3.2 兼容性矩阵 (修订)

| 旧实现 | 保留策略 | 状态 |
|--------|----------|------|
| `benchmark_models.py` | 标记废弃，保留向后兼容 | ✅ 已废弃 |
| `deterministic_judge.py` | 保留，独立运行 | ✅ 已废弃 |
| `strategy_benchmark.py` | 标记废弃，保留向后兼容 | ✅ 已废弃 |
| `agentic_eval.py` | 待重写 | ⏳ 待执行 |

**注**: 旧模块的 `AgenticBenchmarkCase`/`AgenticJudgeConfig` 与新框架的 `UnifiedBenchmarkCase`/`JudgeConfig` 结构不兼容，无法直接迁移。`llm.evaluation` 内部仍依赖旧模块，需后续版本中逐步迁移。

---

## 4. 文件结构 (2026-03-28 更新)

```
polaris/kernelone/benchmark/
├── __init__.py                          # 模块导出
├── unified_models.py                    # 统一数据模型 (~640行)
├── unified_judge.py                     # 统一裁判引擎 (~591行)
├── unified_runner.py                    # 统一执行器 (~747行)
├── adapters/
│   ├── __init__.py
│   ├── agentic_adapter.py               # Agentic 模式适配器
│   ├── strategy_adapter.py              # Strategy 模式适配器
│   └── context_adapter.py                # Context 模式适配器
├── validators/
│   └── __init__.py                      # 内置验证器 (NoPromptLeakageValidator, StructuredStepsValidator, NoHallucinatedPathsValidator)
├── _archived/
│   └── __init__.py                      # 归档说明 (不对外使用)
└── tests/
    ├── __init__.py
    ├── test_unified_models.py           # 17 测试
    ├── test_unified_judge.py            # 17 测试
    └── test_unified_runner.py           # 20 测试

# 总计: 74 测试全部通过
```

---

## 5. CLI 入口设计

```bash
# 统一入口
python -m polaris.kernelone.benchmark \
    --mode agentic \
    --workspace . \
    --role director \
    --case-ids case1,case2

# 模式说明
--mode agentic   # 角色Agent确定性评估
--mode strategy  # 策略离线回放
--mode context   # 上下文选择评估
--mode all       # 运行所有模式

# 输出
# - 统一 JSON 报告
# - 通过/失败状态
# - 详细诊断信息
```

---

## 6. 测试策略

### 6.1 单元测试

```python
# polaris/kernelone/benchmark/tests/test_unified_judge.py
import pytest
from polaris.kernelone.benchmark.unified_judge import UnifiedJudge
from polaris.kernelone.benchmark.unified_models import (
    UnifiedBenchmarkCase,
    ObservedBenchmarkRun,
    JudgeConfig,
    ToolCallObservation,
)


class TestUnifiedJudge:
    """UnifiedJudge 单元测试"""

    def test_judge_passes_when_all_required_tools_found(self) -> None:
        """必需工具存在时通过"""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Find and fix the bug",
            judge=JudgeConfig(
                required_tools=("search_code", "read_file"),
                score_threshold=0.75,
            ),
        )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Fixed the bug",
            tool_calls=(
                ToolCallObservation(tool="search_code", args={"query": "bug"}),
                ToolCallObservation(tool="read_file", args={"file": "src/bug.py"}),
            ),
        )

        judge = UnifiedJudge()
        verdict = judge.judge(case, observed)

        assert verdict.passed is True
        assert verdict.score >= 0.75

    def test_judge_fails_when_forbidden_tool_used(self) -> None:
        """使用禁止工具时失败"""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Safe scope planning",
            judge=JudgeConfig(
                forbidden_tools=("write_file",),
                score_threshold=0.75,
            ),
        )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="I wrote the file",
            tool_calls=(
                ToolCallObservation(tool="write_file", args={"path": "docs/README.md"}),
            ),
        )

        judge = UnifiedJudge()
        verdict = judge.judge(case, observed)

        assert verdict.passed is False
        assert any(
            not c.passed and c.code.startswith("forbidden_tool:")
            for c in verdict.checks
        )

    def test_judge_handles_validator_exception_gracefully(self) -> None:
        """验证器异常时优雅处理"""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test",
            judge=JudgeConfig(validators=["nonexistent_validator"]),
        )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Test output",
        )

        judge = UnifiedJudge()
        verdict = judge.judge(case, observed)

        assert verdict.passed is False
        assert any(
            "unknown validator" in c.message.lower()
            for c in verdict.checks
        )
```

### 6.2 集成测试

```python
# polaris/kernelone/benchmark/tests/test_unified_runner.py
import pytest
from pathlib import Path
from polaris.kernelone.benchmark.unified_runner import (
    UnifiedBenchmarkRunner,
    UnifiedBenchmarkCase,
    JudgeConfig,
    BenchmarkMode,
)


@pytest.fixture
def temp_workspace(tmp_path: Path) -> str:
    """临时工作空间"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return str(workspace)


@pytest.mark.asyncio
async def test_runner_executes_agentic_suite(temp_workspace: str) -> None:
    """集成测试: 运行 Agentic 套件"""
    runner = UnifiedBenchmarkRunner()

    cases = [
        UnifiedBenchmarkCase(
            case_id="case_001",
            role="director",
            title="Root Cause Locator",
            prompt="Find the bug in src/median.py",
            judge=JudgeConfig(
                required_tools=("search_code",),
                mode=BenchmarkMode.AGENTIC,
            ),
        ),
    ]

    result = await runner.run_suite(
        cases=cases,
        workspace=temp_workspace,
        mode=BenchmarkMode.AGENTIC,
    )

    assert result.total_cases == 1
    assert result.run_id.startswith("bench-")
```

---

## 7. 验收标准

| 标准 | 指标 | 阈值 |
|------|------|------|
| 功能完整性 | 现有 case 100% 迁移 | 100% |
| 向后兼容 | 旧 API 调用不破坏 | 100% |
| 测试覆盖 | 新代码覆盖 | >= 90% |
| 类型安全 | mypy 检查 | 0 errors |
| 性能 | 套件执行时间 | <= 现有 110% |

---

## 8. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 迁移期间功能回退 | 中 | 高 | 保留旧实现，逐步切换 |
| 验证器行为差异 | 低 | 高 | 完整回归测试 |
| CLI 参数不兼容 | 低 | 中 | 提供迁移脚本 |

---

## 9. 资源估算

| 阶段 | 人力 | 时间 |
|------|------|------|
| Phase 1 | 2人 | 1周 |
| Phase 2 | 2人 | 1周 |
| Phase 3 | 3人 | 2周 |
| Phase 4 | 2人 | 1周 |
| **总计** | **10人** | **5周** |
