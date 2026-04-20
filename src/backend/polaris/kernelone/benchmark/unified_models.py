"""Unified Benchmark Framework - Core Data Models.

This module provides the canonical data models for the unified benchmark
system. All benchmark types (Agentic, Strategy, Context) share these models.

Design Principles
----------------
- @dataclass(frozen=True): Immutable data carriers for thread safety
- kw_only=True: Explicit keyword-only arguments prevent positional confusion
- TypeAlias: Readable type names for complex types
- Complete type hints: All fields annotated, no implicit any

Example
-------
    from polaris.kernelone.constants import RoleId

    case = UnifiedBenchmarkCase(
        case_id="director_root_cause",
        role=RoleId.DIRECTOR,
        title="Root Cause Locator",
        prompt="Find the bug in src/median.py",
        judge=JudgeConfig(
            required_tools=("search_code", "read_file"),
            score_threshold=0.75,
        ),
    )
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:
    from polaris.kernelone.constants import RoleId

# ------------------------------------------------------------------
# Type Aliases
# ------------------------------------------------------------------

BenchmarkMode: TypeAlias = Literal["agentic", "strategy", "context"]
ToolName: TypeAlias = str
FilePath: TypeAlias = str
ValidatorName: TypeAlias = str

# ------------------------------------------------------------------
# Score Weights
# ------------------------------------------------------------------

SCORE_WEIGHTS: dict[str, float] = {
    "tooling": 0.35,
    "safety": 0.25,
    "contract": 0.25,
    "evidence": 0.15,
}

# ------------------------------------------------------------------
# Core Models
# ------------------------------------------------------------------


def _non_empty_string(value: Any, field_name: str) -> str:
    """Validate and normalize a non-empty string field."""
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field_name} must be non-empty")
    return result


def _normalize_role(value: Any) -> str:
    """Normalize a role value to lowercase string.

    Handles RoleId enum, str, or other values.
    """
    if value is None:
        return ""
    # Handle RoleId enum
    if hasattr(value, "value"):
        return str(value.value).strip().lower()
    return str(value).strip().lower()


def _tuple_of_strings(values: Any) -> tuple[str, ...]:
    """Normalize a sequence of strings to a tuple."""
    if not isinstance(values, (Sequence, tuple, list)):
        return ()
    return tuple(str(item).strip() for item in values if str(item).strip())


def _normalize_history_entries(
    values: Any,
) -> tuple[tuple[str, str], ...]:
    """Normalize history entries to role/content tuples."""
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[tuple[str, str]] = []
    for item in values:
        role = ""
        content = ""
        if isinstance(item, Mapping):
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or item.get("message") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            role = str(item[0] or "").strip()
            content = str(item[1] or "").strip()
        if role and content:
            result.append((role, content))
    return tuple(result)


@dataclass(frozen=True, kw_only=True)
class BudgetConditions:
    """Budget constraints for a benchmark case.

    Attributes:
        max_tokens: Maximum token budget for the case.
        max_turns: Maximum number of conversation turns.
        max_wall_time_seconds: Maximum wall-clock time allowed.
    """

    max_tokens: int = 200_000
    max_turns: int = 10
    max_wall_time_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.max_turns <= 0:
            raise ValueError("max_turns must be positive")
        if self.max_wall_time_seconds <= 0:
            raise ValueError("max_wall_time_seconds must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_turns": self.max_turns,
            "max_wall_time_seconds": self.max_wall_time_seconds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BudgetConditions:
        return cls(
            max_tokens=max(1, int(data.get("max_tokens", 200_000))),
            max_turns=max(1, int(data.get("max_turns", 10))),
            max_wall_time_seconds=max(0.1, float(data.get("max_wall_time_seconds", 300.0))),
        )


@dataclass(frozen=True, kw_only=True)
class ToolArgumentRule:
    """One deterministic argument rule for a tool-call trace.

    This defines a pattern that must (or must not) appear in tool arguments.

    Attributes:
        fragment: The string fragment to search for in tool arguments.
        tools: Optional tuple of tool names this rule applies to.
        description: Human-readable description of the rule.
    """

    fragment: str
    tools: tuple[ToolName, ...] = field(default_factory=tuple)
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "fragment", _non_empty_string(self.fragment, "fragment"))
        object.__setattr__(self, "tools", _tuple_of_strings(self.tools))
        object.__setattr__(self, "description", str(self.description or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "fragment": self.fragment,
            "tools": list(self.tools),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ToolArgumentRule:
        return cls(
            fragment=payload.get("fragment", ""),
            tools=tuple(payload.get("tools") or ()),
            description=str(payload.get("description") or ""),
        )


@dataclass(frozen=True, kw_only=True)
class JudgeConfig:
    """Deterministic judge rules attached to a benchmark case.

    This defines all acceptance criteria that will be checked against
    the observed execution trace.

    Attributes:
        score_threshold: Minimum score (0.0-1.0) to pass.
        required_tools: Tools that must appear in the trace.
        forbidden_tools: Tools that must NOT appear in the trace.
        required_tool_arguments: Argument patterns that must exist.
        forbidden_tool_arguments: Argument patterns that must NOT exist.
        min_tool_calls: Minimum number of tool calls required.
        max_tool_calls: Maximum number of tool calls allowed.
        required_output_substrings: Output must contain these substrings.
        forbidden_output_substrings: Output must NOT contain these substrings.
        validators: Additional validator names to run.
        mode: The benchmark mode this config applies to.
    """

    score_threshold: float = 0.75
    required_tools: tuple[ToolName, ...] = field(default_factory=tuple)
    forbidden_tools: tuple[ToolName, ...] = field(default_factory=tuple)
    required_tool_arguments: tuple[ToolArgumentRule, ...] = field(default_factory=tuple)
    forbidden_tool_arguments: tuple[ToolArgumentRule, ...] = field(default_factory=tuple)
    min_tool_calls: int = 0
    max_tool_calls: int | None = None
    required_output_substrings: tuple[str, ...] = field(default_factory=tuple)
    forbidden_output_substrings: tuple[str, ...] = field(default_factory=tuple)
    validators: tuple[ValidatorName, ...] = field(default_factory=tuple)
    mode: BenchmarkMode = "agentic"

    def __post_init__(self) -> None:
        threshold = float(self.score_threshold)
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("score_threshold must be between 0.0 and 1.0")
        object.__setattr__(self, "score_threshold", threshold)

        min_calls = int(self.min_tool_calls)
        if min_calls < 0:
            raise ValueError("min_tool_calls must be >= 0")
        object.__setattr__(self, "min_tool_calls", min_calls)

        max_calls: int | None = None
        if self.max_tool_calls is not None:
            max_calls = int(self.max_tool_calls)
            if max_calls < min_calls:
                raise ValueError("max_tool_calls must be >= min_tool_calls")
        object.__setattr__(self, "max_tool_calls", max_calls)

        object.__setattr__(self, "required_tools", _tuple_of_strings(self.required_tools))
        object.__setattr__(self, "forbidden_tools", _tuple_of_strings(self.forbidden_tools))
        object.__setattr__(
            self,
            "required_tool_arguments",
            tuple(
                item if isinstance(item, ToolArgumentRule) else ToolArgumentRule.from_dict(item)
                for item in list(self.required_tool_arguments or ())
            ),
        )
        object.__setattr__(
            self,
            "forbidden_tool_arguments",
            tuple(
                item if isinstance(item, ToolArgumentRule) else ToolArgumentRule.from_dict(item)
                for item in list(self.forbidden_tool_arguments or ())
            ),
        )
        object.__setattr__(
            self,
            "required_output_substrings",
            _tuple_of_strings(self.required_output_substrings),
        )
        object.__setattr__(
            self,
            "forbidden_output_substrings",
            _tuple_of_strings(self.forbidden_output_substrings),
        )
        object.__setattr__(self, "validators", _tuple_of_strings(self.validators))
        if self.mode not in ("agentic", "strategy", "context"):
            raise ValueError("mode must be one of: agentic, strategy, context")

    def to_dict(self) -> dict[str, Any]:
        return {
            "score_threshold": self.score_threshold,
            "required_tools": list(self.required_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "required_tool_arguments": [item.to_dict() for item in self.required_tool_arguments],
            "forbidden_tool_arguments": [item.to_dict() for item in self.forbidden_tool_arguments],
            "min_tool_calls": self.min_tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "required_output_substrings": list(self.required_output_substrings),
            "forbidden_output_substrings": list(self.forbidden_output_substrings),
            "validators": list(self.validators),
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> JudgeConfig:
        return cls(
            score_threshold=payload.get("score_threshold", 0.75),
            required_tools=tuple(payload.get("required_tools") or ()),
            forbidden_tools=tuple(payload.get("forbidden_tools") or ()),
            required_tool_arguments=tuple(payload.get("required_tool_arguments") or ()),
            forbidden_tool_arguments=tuple(payload.get("forbidden_tool_arguments") or ()),
            min_tool_calls=payload.get("min_tool_calls", 0),
            max_tool_calls=payload.get("max_tool_calls"),
            required_output_substrings=tuple(payload.get("required_output_substrings") or ()),
            forbidden_output_substrings=tuple(payload.get("forbidden_output_substrings") or ()),
            validators=tuple(payload.get("validators") or ()),
            mode=payload.get("mode", "agentic"),
        )


@dataclass(frozen=True, kw_only=True)
class UnifiedBenchmarkCase:
    """Unified Benchmark Case model.

    This is the canonical case definition used across all benchmark modes.
    Whether running Agentic, Strategy, or Context benchmarks, they all
    serialize to this model.

    Attributes:
        case_id: Unique identifier for this case.
        role: The role this case targets (e.g., "director", "pm", "qa").
        title: Human-readable title.
        prompt: The user prompt to execute.
        description: Detailed description of what this case tests.
        workspace_fixture: Optional path to workspace fixture directory.
        expected_evidence_path: Files that should have been read.
        expected_answer_shape: Expected output format.
        budget_conditions: Resource constraints.
        canonical_profile: Which profile to use as baseline.
        history: Prior conversation history as (role, content) tuples.
        context: Additional context metadata.
        metadata: Case metadata (tags, source, etc.).
        tags: Searchable tags for this case.
        judge: Deterministic judge configuration.

    Example:
        >>> case = UnifiedBenchmarkCase(
        ...     case_id="director_safe_scope",
        ...     role="director",
        ...     title="Safe Scope Planning",
        ...     prompt="Plan changes for the project",
        ...     judge=JudgeConfig(
        ...         forbidden_tools=("write_file",),
        ...         required_output_substrings=("scope",),
        ...     ),
        ... )
    """

    case_id: str
    role: RoleId | str  # RoleId preferred; str allowed for backward compatibility
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
        object.__setattr__(self, "case_id", _non_empty_string(self.case_id, "case_id"))
        role_val = _normalize_role(self.role)
        if not role_val:
            raise ValueError("role must be non-empty")
        object.__setattr__(self, "role", role_val)
        object.__setattr__(self, "title", _non_empty_string(self.title, "title"))
        object.__setattr__(self, "prompt", _non_empty_string(self.prompt, "prompt"))
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "workspace_fixture", str(self.workspace_fixture or "").strip())
        object.__setattr__(self, "expected_evidence_path", _tuple_of_strings(self.expected_evidence_path))
        object.__setattr__(self, "expected_answer_shape", str(self.expected_answer_shape or "answer").strip())
        if not isinstance(self.budget_conditions, BudgetConditions):
            bd = self.budget_conditions if isinstance(self.budget_conditions, Mapping) else {}
            object.__setattr__(self, "budget_conditions", BudgetConditions.from_dict(bd))
        object.__setattr__(self, "canonical_profile", str(self.canonical_profile or "canonical_balanced").strip())
        object.__setattr__(self, "history", _normalize_history_entries(self.history))
        object.__setattr__(self, "context", dict(self.context or {}))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        object.__setattr__(self, "tags", _tuple_of_strings(self.tags))
        if not isinstance(self.judge, JudgeConfig):
            jc = self.judge if isinstance(self.judge, Mapping) else {}
            object.__setattr__(self, "judge", JudgeConfig.from_dict(jc))
        else:
            # Already a JudgeConfig instance - validate mode consistency but keep other attrs
            current_judge = self.judge
            # Only recreate if mode needs adjustment
            if current_judge.mode not in ("agentic", "strategy", "context"):
                object.__setattr__(self, "judge", JudgeConfig(mode="agentic"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "role": self.role,
            "title": self.title,
            "prompt": self.prompt,
            "description": self.description,
            "workspace_fixture": self.workspace_fixture,
            "expected_evidence_path": list(self.expected_evidence_path),
            "expected_answer_shape": self.expected_answer_shape,
            "budget_conditions": self.budget_conditions.to_dict(),
            "canonical_profile": self.canonical_profile,
            "history": [{"role": r, "content": c} for r, c in self.history],
            "context": dict(self.context),
            "metadata": dict(self.metadata),
            "tags": list(self.tags),
            "judge": self.judge.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> UnifiedBenchmarkCase:
        return cls(
            case_id=payload.get("case_id", ""),
            role=payload.get("role", ""),
            title=payload.get("title", ""),
            prompt=payload.get("prompt", ""),
            description=str(payload.get("description") or ""),
            workspace_fixture=str(payload.get("workspace_fixture") or ""),
            expected_evidence_path=tuple(payload.get("expected_evidence_path") or ()),
            expected_answer_shape=str(payload.get("expected_answer_shape") or "answer"),
            budget_conditions=BudgetConditions.from_dict(payload.get("budget_conditions") or {}),
            canonical_profile=str(payload.get("canonical_profile") or "canonical_balanced"),
            history=tuple(payload.get("history") or ()),
            context=dict(payload.get("context") or {}),
            metadata=dict(payload.get("metadata") or {}),
            tags=tuple(payload.get("tags") or ()),
            judge=JudgeConfig.from_dict(payload.get("judge") or {}),
        )


@dataclass(frozen=True, kw_only=True)
class ToolCallObservation:
    """One observed tool call emitted during benchmark execution.

    Attributes:
        tool: The canonical tool name.
        args: The tool arguments as a dictionary.
        event_index: Index in the event stream for ordering.
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    event_index: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool", _non_empty_string(self.tool, "tool"))
        object.__setattr__(self, "args", dict(self.args or {}))
        object.__setattr__(self, "event_index", max(0, int(self.event_index)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": dict(self.args),
            "event_index": self.event_index,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ToolCallObservation:
        return cls(
            tool=payload.get("tool", ""),
            args=dict(payload.get("args") or {}),
            event_index=payload.get("event_index", 0),
        )


@dataclass(frozen=True, kw_only=True)
class ObservedBenchmarkRun:
    """Observed execution trace for one benchmark case.

    This captures everything that happened during a benchmark execution,
    including tool calls, output, thinking, and timing.

    Attributes:
        case_id: The case being observed.
        role: The role that was executed.
        workspace: The workspace path used.
        output: The final text output.
        thinking: Optional thinking/reasoning output.
        tool_calls: All observed tool calls in order.
        error: Error message if execution failed.
        duration_ms: Execution time in milliseconds.
        event_count: Total number of events emitted.
        fingerprint: Additional metadata from the runtime.
    """

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

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _non_empty_string(self.case_id, "case_id"))
        object.__setattr__(self, "role", _normalize_role(self.role))
        object.__setattr__(self, "workspace", str(self.workspace or "").strip())
        object.__setattr__(self, "output", str(self.output or ""))
        object.__setattr__(self, "thinking", str(self.thinking or ""))
        object.__setattr__(
            self,
            "tool_calls",
            tuple(
                item if isinstance(item, ToolCallObservation) else ToolCallObservation.from_dict(item)
                for item in list(self.tool_calls or ())
            ),
        )
        object.__setattr__(self, "error", str(self.error or ""))
        object.__setattr__(self, "duration_ms", max(0, int(self.duration_ms)))
        object.__setattr__(self, "event_count", max(0, int(self.event_count)))
        object.__setattr__(self, "fingerprint", dict(self.fingerprint or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "role": self.role,
            "workspace": self.workspace,
            "output": self.output,
            "thinking": self.thinking,
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "error": self.error,
            "duration_ms": self.duration_ms,
            "event_count": self.event_count,
            "fingerprint": dict(self.fingerprint),
        }


@dataclass(frozen=True, kw_only=True)
class JudgeCheck:
    """One deterministic judge check result.

    Attributes:
        code: Unique identifier for this check (e.g., "required_tool:search_code").
        category: The category this check belongs to.
        passed: Whether the check passed.
        message: Human-readable explanation.
        critical: Whether this is a critical (failing blocks overall pass).
        evidence: Additional diagnostic data.
    """

    code: str
    category: str
    passed: bool
    message: str
    critical: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _non_empty_string(self.code, "code"))
        object.__setattr__(self, "category", str(self.category or "").strip().lower())
        object.__setattr__(self, "passed", bool(self.passed))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "critical", bool(self.critical))
        object.__setattr__(self, "evidence", dict(self.evidence or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "passed": self.passed,
            "message": self.message,
            "critical": self.critical,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> JudgeCheck:
        return cls(
            code=payload.get("code", ""),
            category=payload.get("category", ""),
            passed=bool(payload.get("passed", False)),
            message=str(payload.get("message") or ""),
            critical=bool(payload.get("critical", False)),
            evidence=dict(payload.get("evidence") or {}),
        )


@dataclass(frozen=True, kw_only=True)
class UnifiedJudgeVerdict:
    """Final deterministic verdict for one benchmark case.

    This contains the complete judgment results including individual
    checks, category scores, and overall pass/fail decision.

    Attributes:
        case_id: The case being judged.
        passed: Whether the case passed overall.
        score: The overall score (0.0-1.0).
        threshold: The passing threshold.
        categories: Per-category scores.
        summary: Human-readable summary of results.
        checks: All individual check results.
        mode: The benchmark mode this verdict applies to.
    """

    case_id: str
    passed: bool
    score: float
    threshold: float
    categories: dict[str, float] = field(default_factory=dict)
    summary: str = ""
    checks: tuple[JudgeCheck, ...] = field(default_factory=tuple)
    mode: BenchmarkMode = "agentic"

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _non_empty_string(self.case_id, "case_id"))
        object.__setattr__(self, "passed", bool(self.passed))
        object.__setattr__(self, "score", max(0.0, min(1.0, float(self.score))))
        object.__setattr__(self, "threshold", max(0.0, min(1.0, float(self.threshold))))
        object.__setattr__(self, "categories", dict(self.categories or {}))
        object.__setattr__(self, "summary", str(self.summary or ""))
        object.__setattr__(
            self,
            "checks",
            tuple(
                item if isinstance(item, JudgeCheck) else JudgeCheck.from_dict(item) for item in list(self.checks or ())
            ),
        )
        if self.mode not in ("agentic", "strategy", "context"):
            object.__setattr__(self, "mode", "agentic")

    @property
    def critical_failures(self) -> tuple[JudgeCheck, ...]:
        """Return all critical checks that failed."""
        return tuple(c for c in self.checks if c.critical and not c.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": round(self.score, 4),
            "threshold": round(self.threshold, 4),
            "categories": {k: round(v, 4) for k, v in self.categories.items()},
            "summary": self.summary,
            "checks": [item.to_dict() for item in self.checks],
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> UnifiedJudgeVerdict:
        return cls(
            case_id=payload.get("case_id", ""),
            passed=bool(payload.get("passed", False)),
            score=payload.get("score", 0.0),
            threshold=payload.get("threshold", 0.75),
            categories=dict(payload.get("categories") or {}),
            summary=str(payload.get("summary") or ""),
            checks=tuple(payload.get("checks") or ()),
            mode=payload.get("mode", "agentic"),
        )
