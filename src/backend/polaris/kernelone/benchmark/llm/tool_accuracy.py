"""Tool Call Accuracy Benchmarking Framework.

This module provides comprehensive tool calling accuracy testing
including selection accuracy, parameter extraction, and success rate.

Example
-------
    from polaris.kernelone.benchmark.llm.tool_accuracy import (
        ToolCallTestCase,
        ToolCallMetrics,
        ToolCallAccuracyBenchmark,
    )

    test_cases = [
        ToolCallTestCase(
            case_id="search_file",
            task_prompt="Search for 'hello' in src/",
            expected_tool="repo_rg",
            expected_params={"pattern": "hello", "path": "src/"},
        ),
    ]

    benchmark = ToolCallAccuracyBenchmark(test_cases)
    metrics = await benchmark.run(agent)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

# ------------------------------------------------------------------
# Type Aliases
# ------------------------------------------------------------------

ToolName: TypeAlias = str
ParamKey: TypeAlias = str
ParamValue: TypeAlias = Any


# ------------------------------------------------------------------
# Data Models
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ToolCallTestCase:
    """Test case for tool call accuracy evaluation.

    Attributes:
        case_id: Unique identifier for this test case.
        task_prompt: The user prompt that should trigger tool usage.
        expected_tool: The expected tool name to be called.
        expected_params: The expected parameters for the tool call.
        forbidden_tools: Tools that should NOT be called.
        description: Human-readable description of this test case.
        metadata: Additional metadata for the test case.
    """

    case_id: str
    task_prompt: str
    expected_tool: str = ""
    expected_params: dict[str, Any] = field(default_factory=dict)
    forbidden_tools: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("case_id is required")
        if not self.task_prompt:
            raise ValueError("task_prompt is required")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "case_id": self.case_id,
            "task_prompt": self.task_prompt,
            "expected_tool": self.expected_tool,
            "expected_params": dict(self.expected_params),
            "forbidden_tools": list(self.forbidden_tools),
            "description": self.description,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, kw_only=True)
class ToolCallResult:
    """Benchmark-specific tool call result recording.

    P2-018 Intent Separation:
        This class is intentionally scoped to the benchmark/accuracy testing domain.
        It is NOT the same as:
        - polaris.cells.roles.kernel.internal.output_parser.ToolCallResult
            (Parse phase: (tool, args) format)
        - polaris.kernelone.llm.contracts.tool.ToolExecutionResult
            (Execution phase: (tool_call_id, name, success, result, error))

    This benchmark result aggregates test case metadata (case_id) with execution
    details for accuracy measurement. Keep separate from runtime contracts.

    Attributes:
        case_id: The test case that was executed.
        tool_called: The tool that was actually called.
        params: The parameters passed to the tool.
        success: Whether the call succeeded.
        execution_time_ms: Time taken to execute the call.
        error: Error message if the call failed.
    """

    case_id: str
    tool_called: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    execution_time_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "case_id": self.case_id,
            "tool_called": self.tool_called,
            "params": dict(self.params),
            "success": self.success,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "error": self.error,
        }


@dataclass(frozen=True, kw_only=True)
class ToolCallMetrics:
    """Aggregated metrics for tool call accuracy.

    Attributes:
        tool_selection_accuracy: Ratio of correct tool selections (0.0-1.0).
        param_extraction_accuracy: Ratio of correct parameter extractions (0.0-1.0).
        total_calls: Total number of tool calls attempted.
        successful_calls: Number of successful calls.
        tool_breakdown: Per-tool accuracy breakdown.
        param_breakdown: Per-parameter accuracy breakdown.
    """

    tool_selection_accuracy: float = 0.0
    param_extraction_accuracy: float = 0.0
    total_calls: int = 0
    successful_calls: int = 0
    tool_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)
    param_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Clamp values using object.__setattr__ for frozen dataclass
        object.__setattr__(
            self,
            "tool_selection_accuracy",
            max(0.0, min(1.0, self.tool_selection_accuracy)),
        )
        object.__setattr__(
            self,
            "param_extraction_accuracy",
            max(0.0, min(1.0, self.param_extraction_accuracy)),
        )

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_calls == 0:
            return 0.0
        return self.successful_calls / self.total_calls

    @property
    def overall_accuracy(self) -> float:
        """Calculate overall accuracy combining tool and param accuracy."""
        return (self.tool_selection_accuracy + self.param_extraction_accuracy) / 2

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "tool_selection_accuracy": round(self.tool_selection_accuracy, 4),
            "param_extraction_accuracy": round(self.param_extraction_accuracy, 4),
            "success_rate": round(self.success_rate, 4),
            "overall_accuracy": round(self.overall_accuracy, 4),
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "tool_breakdown": self.tool_breakdown,
            "param_breakdown": self.param_breakdown,
        }


# ------------------------------------------------------------------
# Agent Protocol
# ------------------------------------------------------------------


class ToolCallingAgentPort(Protocol):
    """Protocol for an agent that can execute tool calls.

    Implementations should provide an execute method that runs
    a task and returns the tool call result.
    """

    async def execute(self, task_prompt: str) -> ToolCallResult:
        """Execute a task and return the tool call result.

        Args:
            task_prompt: The task to execute.

        Returns:
            ToolCallResult with the execution details.
        """
        ...


# ------------------------------------------------------------------
# Benchmark Runner
# ------------------------------------------------------------------


@dataclass
class ToolCallBenchmarkResult:
    """Result of running the tool call benchmark.

    Attributes:
        total_cases: Total number of test cases.
        passed_cases: Number of cases that passed.
        failed_cases: Number of cases that failed.
        metrics: Aggregated metrics.
        results: Individual test case results.
        execution_time_ms: Total execution time.
    """

    total_cases: int
    passed_cases: int
    failed_cases: int
    metrics: ToolCallMetrics
    results: list[ToolCallResult]
    execution_time_ms: float = 0.0

    @property
    def pass_rate(self) -> float:
        """Calculate pass rate."""
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "pass_rate": round(self.pass_rate, 4),
            "execution_time_ms": round(self.execution_time_ms, 2),
            "metrics": self.metrics.to_dict(),
            "results": [r.to_dict() for r in self.results],
        }


class ToolCallAccuracyBenchmark:
    """Benchmark for tool call accuracy evaluation.

    This class runs a suite of test cases against an agent
    and calculates accuracy metrics.

    Attributes:
        test_cases: List of test cases to run.
        tool_aliases: Optional alias mapping for tool names.
    """

    def __init__(
        self,
        test_cases: list[ToolCallTestCase],
        tool_aliases: dict[str, str] | None = None,
    ) -> None:
        self._test_cases = test_cases
        self._tool_aliases = dict(tool_aliases) if tool_aliases else {}

    def _normalize_tool_name(self, tool: str) -> str:
        """Normalize tool name using alias mapping."""
        return self._tool_aliases.get(tool, tool)

    def _is_tool_match(self, expected: str, actual: str) -> bool:
        """Check if tool names match (considering aliases)."""
        norm_expected = self._normalize_tool_name(expected.lower())
        norm_actual = self._normalize_tool_name(actual.lower())
        return norm_expected == norm_actual

    def _is_params_match(
        self,
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> bool:
        """Check if parameters match."""
        if set(expected.keys()) != set(actual.keys()):
            return False

        for key, exp_value in expected.items():
            act_value = actual.get(key)
            if exp_value != act_value:
                # Try value normalization for strings
                if isinstance(exp_value, str) and isinstance(act_value, str):
                    if exp_value.lower().strip() != act_value.lower().strip():
                        return False
                else:
                    return False

        return True

    def _check_forbidden_tools(
        self,
        forbidden: tuple[str, ...],
        actual_tool: str,
    ) -> bool:
        """Check if any forbidden tool was called."""
        return any(self._is_tool_match(ft, actual_tool) for ft in forbidden)

    async def run(
        self,
        agent: ToolCallingAgentPort,
        stop_on_first_failure: bool = False,
    ) -> ToolCallBenchmarkResult:
        """Run the benchmark against an agent.

        Args:
            agent: The agent to test.
            stop_on_first_failure: Stop after first failure.

        Returns:
            ToolCallBenchmarkResult with all metrics.
        """
        start_time = time.time()
        results: list[ToolCallResult] = []
        passed = 0
        failed = 0

        tool_correct = 0
        param_correct = 0
        success_count = 0
        tool_breakdown: dict[str, dict[str, int]] = {}
        param_breakdown: dict[str, dict[str, int]] = {}

        for case in self._test_cases:
            result = await agent.execute(case.task_prompt)
            results.append(result)

            # Update tool breakdown
            tool = result.tool_called or "none"
            if tool not in tool_breakdown:
                tool_breakdown[tool] = {"correct": 0, "incorrect": 0}

            # Update param breakdown
            for param_key in set(list(result.params.keys()) + list(case.expected_params.keys())):
                if param_key not in param_breakdown:
                    param_breakdown[param_key] = {"correct": 0, "incorrect": 0}

            # Check tool selection
            tool_match = self._is_tool_match(case.expected_tool, result.tool_called)

            # Check forbidden tools
            forbidden_violation = self._check_forbidden_tools(
                case.forbidden_tools,
                result.tool_called,
            )

            # Check params match
            param_match = self._is_params_match(case.expected_params, result.params)

            if tool_match and not forbidden_violation:
                tool_correct += 1
                tool_breakdown[tool]["correct"] += 1
            else:
                tool_breakdown[tool]["incorrect"] += 1

            if param_match:
                param_correct += 1
                for key in result.params:
                    if key in param_breakdown:
                        param_breakdown[key]["correct"] += 1
            else:
                for key in result.params:
                    if key in param_breakdown:
                        param_breakdown[key]["incorrect"] += 1

            if result.success:
                success_count += 1

            # Determine pass/fail
            case_passed = tool_match and not forbidden_violation and param_match and result.success
            if case_passed:
                passed += 1
            else:
                failed += 1
                if stop_on_first_failure:
                    break

        total_cases = len(results)
        execution_time = (time.time() - start_time) * 1000

        metrics = ToolCallMetrics(
            tool_selection_accuracy=tool_correct / total_cases if total_cases > 0 else 0.0,
            param_extraction_accuracy=param_correct / total_cases if total_cases > 0 else 0.0,
            total_calls=total_cases,
            successful_calls=success_count,
            tool_breakdown=tool_breakdown,
            param_breakdown=param_breakdown,
        )

        return ToolCallBenchmarkResult(
            total_cases=total_cases,
            passed_cases=passed,
            failed_cases=failed,
            metrics=metrics,
            results=results,
            execution_time_ms=execution_time,
        )

    def get_test_cases(self) -> list[ToolCallTestCase]:
        """Get the list of test cases."""
        return list(self._test_cases)


# ------------------------------------------------------------------
# Mock Agent for Testing
# ------------------------------------------------------------------


class MockToolCallingAgent:
    """Mock agent for testing tool call benchmarks.

    This agent returns pre-configured results for testing.
    """

    def __init__(self, results: list[ToolCallResult]) -> None:
        self._results = results
        self._index = 0

    async def execute(self, task_prompt: str) -> ToolCallResult:
        """Return the next pre-configured result."""
        if self._index < len(self._results):
            result = self._results[self._index]
            self._index += 1
            return result
        return ToolCallResult(
            case_id="unknown",
            tool_called="",
            success=False,
            error="No more results configured",
        )


# ------------------------------------------------------------------
# Common Test Cases
# ------------------------------------------------------------------


def get_standard_tool_test_cases() -> list[ToolCallTestCase]:
    """Get standard tool call test cases for common tasks.

    Returns a curated list of test cases covering:
    - File reading/writing
    - Code search
    - Directory operations
    - Command execution
    """
    return [
        ToolCallTestCase(
            case_id="read_config_file",
            task_prompt="Read the config file from src/config.json",
            expected_tool="repo_read_head",
            expected_params={"path": "src/config.json"},
            description="Test file reading capability",
        ),
        ToolCallTestCase(
            case_id="search_imports",
            task_prompt="Find all imports of 'pytest' in the project",
            expected_tool="repo_rg",
            expected_params={"pattern": "import pytest"},
            description="Test code search capability",
        ),
        ToolCallTestCase(
            case_id="list_directory",
            task_prompt="List files in the src directory",
            expected_tool="repo_tree",
            expected_params={"path": "src"},
            description="Test directory listing",
        ),
        ToolCallTestCase(
            case_id="read_file_head",
            task_prompt="Show the first 20 lines of main.py",
            expected_tool="repo_read_head",
            expected_params={"path": "main.py", "limit": 20},
            description="Test file head reading",
        ),
        ToolCallTestCase(
            case_id="apply_diff",
            task_prompt="Apply this change to fix the bug: fix the typo in config",
            expected_tool="repo_apply_diff",
            expected_params={"path": "config", "patch": "fix typo"},
            description="Test diff application",
        ),
    ]
