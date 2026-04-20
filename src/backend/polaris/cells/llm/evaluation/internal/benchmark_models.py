"""Typed models for deterministic agentic benchmark fixtures and verdicts.

.. deprecated::
    This module is deprecated. Use ``polaris.kernelone.benchmark.unified_models``
    for new development. The canonical benchmark framework is now
    ``polaris/kernelone/benchmark/unified_models.py`` (UnifiedBenchmarkCase,
    JudgeConfig, UnifiedJudgeVerdict).

    This module is retained for backward compatibility with existing
    evaluation cell internals and will be removed in a future release.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def _non_empty(value: Any) -> str:
    return str(value or "").strip()


def _tuple_of_strings(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    normalized: list[str] = []
    for item in values:
        token = _non_empty(item)
        if token:
            normalized.append(token)
    return tuple(normalized)


def _history_entries(values: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    normalized: list[tuple[str, str]] = []
    for item in values:
        role = ""
        content = ""
        if isinstance(item, Mapping):
            role = _non_empty(item.get("role"))
            content = _non_empty(item.get("content") or item.get("message"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            role = _non_empty(item[0])
            content = _non_empty(item[1])
        if role and content:
            normalized.append((role, content))
    return tuple(normalized)


@dataclass(frozen=True)
class ToolArgumentRule:
    """One deterministic argument rule for a tool-call trace."""

    fragment: str
    tools: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""

    def __post_init__(self) -> None:
        fragment = _non_empty(self.fragment)
        if not fragment:
            raise ValueError("fragment must be a non-empty string")
        object.__setattr__(self, "fragment", fragment)
        object.__setattr__(self, "tools", _tuple_of_strings(self.tools))
        object.__setattr__(self, "description", _non_empty(self.description))

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


@dataclass(frozen=True)
class AgenticJudgeConfig:
    """Deterministic judge rules attached to one benchmark case."""

    score_threshold: float = 0.75
    required_tools: tuple[str, ...] = field(default_factory=tuple)
    forbidden_tools: tuple[str, ...] = field(default_factory=tuple)
    required_tool_arguments: tuple[ToolArgumentRule, ...] = field(default_factory=tuple)
    forbidden_tool_arguments: tuple[ToolArgumentRule, ...] = field(default_factory=tuple)
    min_tool_calls: int = 0
    max_tool_calls: int | None = None
    required_output_substrings: tuple[str, ...] = field(default_factory=tuple)
    forbidden_output_substrings: tuple[str, ...] = field(default_factory=tuple)
    validators: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        threshold = float(self.score_threshold)
        if threshold < 0.0 or threshold > 1.0:
            raise ValueError("score_threshold must be between 0.0 and 1.0")
        min_calls = int(self.min_tool_calls)
        if min_calls < 0:
            raise ValueError("min_tool_calls must be >= 0")
        max_calls = None if self.max_tool_calls is None else int(self.max_tool_calls)
        if max_calls is not None and max_calls < min_calls:
            raise ValueError("max_tool_calls must be >= min_tool_calls")
        object.__setattr__(self, "score_threshold", threshold)
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
        object.__setattr__(self, "min_tool_calls", min_calls)
        object.__setattr__(self, "max_tool_calls", max_calls)
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
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AgenticJudgeConfig:
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
        )


@dataclass(frozen=True)
class AgenticBenchmarkCase:
    """One deterministic role benchmark case."""

    case_id: str
    role: str
    title: str
    prompt: str
    description: str = ""
    workspace_fixture: str = ""
    history: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = field(default_factory=tuple)
    judge: AgenticJudgeConfig = field(default_factory=AgenticJudgeConfig)

    def __post_init__(self) -> None:
        case_id = _non_empty(self.case_id)
        role = _non_empty(self.role).lower()
        title = _non_empty(self.title)
        prompt = _non_empty(self.prompt)
        if not case_id or not role or not title or not prompt:
            raise ValueError("case_id, role, title, and prompt must be non-empty")
        judge = self.judge if isinstance(self.judge, AgenticJudgeConfig) else AgenticJudgeConfig.from_dict(self.judge)
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "description", _non_empty(self.description))
        object.__setattr__(self, "workspace_fixture", _non_empty(self.workspace_fixture))
        object.__setattr__(self, "history", _history_entries(self.history))
        object.__setattr__(self, "context", dict(self.context or {}))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        object.__setattr__(self, "tags", _tuple_of_strings(self.tags))
        object.__setattr__(self, "judge", judge)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "role": self.role,
            "title": self.title,
            "prompt": self.prompt,
            "description": self.description,
            "workspace_fixture": self.workspace_fixture,
            "history": [{"role": role, "content": content} for role, content in self.history],
            "context": dict(self.context),
            "metadata": dict(self.metadata),
            "tags": list(self.tags),
            "judge": self.judge.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AgenticBenchmarkCase:
        return cls(
            case_id=payload.get("case_id", ""),
            role=payload.get("role", ""),
            title=payload.get("title", ""),
            prompt=payload.get("prompt", ""),
            description=str(payload.get("description") or ""),
            workspace_fixture=str(payload.get("workspace_fixture") or ""),
            history=tuple(payload.get("history") or ()),
            context=dict(payload.get("context") or {}),
            metadata=dict(payload.get("metadata") or {}),
            tags=tuple(payload.get("tags") or ()),
            judge=AgenticJudgeConfig.from_dict(payload.get("judge") or {}),
        )


@dataclass(frozen=True)
class ToolCallObservation:
    """One observed tool call emitted by the role runtime stream."""

    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    event_index: int = 0

    def __post_init__(self) -> None:
        tool = _non_empty(self.tool)
        if not tool:
            raise ValueError("tool must be a non-empty string")
        object.__setattr__(self, "tool", tool)
        object.__setattr__(self, "args", dict(self.args or {}))
        object.__setattr__(self, "event_index", int(self.event_index))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": dict(self.args),
            "event_index": self.event_index,
        }


@dataclass(frozen=True)
class ObservedBenchmarkRun:
    """Observed execution trace for one benchmark case."""

    case_id: str
    role: str
    workspace: str
    output: str
    thinking: str = ""
    tool_calls: tuple[ToolCallObservation, ...] = field(default_factory=tuple)
    error: str = ""
    duration_ms: int = 0
    event_count: int = 0
    fingerprint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _non_empty(self.case_id))
        object.__setattr__(self, "role", _non_empty(self.role).lower())
        object.__setattr__(self, "workspace", _non_empty(self.workspace))
        object.__setattr__(self, "output", str(self.output or ""))
        object.__setattr__(self, "thinking", str(self.thinking or ""))
        object.__setattr__(
            self,
            "tool_calls",
            tuple(
                item if isinstance(item, ToolCallObservation) else ToolCallObservation(**dict(item))
                for item in list(self.tool_calls or ())
            ),
        )
        object.__setattr__(self, "error", str(self.error or ""))
        object.__setattr__(self, "duration_ms", int(self.duration_ms))
        object.__setattr__(self, "event_count", int(self.event_count))
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


@dataclass(frozen=True)
class JudgeCheck:
    """One deterministic judge check."""

    code: str
    category: str
    passed: bool
    message: str
    critical: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _non_empty(self.code))
        object.__setattr__(self, "category", _non_empty(self.category))
        object.__setattr__(self, "message", _non_empty(self.message))
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


@dataclass(frozen=True)
class AgenticJudgeVerdict:
    """Final deterministic verdict for one benchmark case."""

    case_id: str
    passed: bool
    score: float
    threshold: float
    categories: Mapping[str, float] = field(default_factory=dict)
    summary: str = ""
    checks: tuple[JudgeCheck, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _non_empty(self.case_id))
        object.__setattr__(self, "passed", bool(self.passed))
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "threshold", float(self.threshold))
        object.__setattr__(self, "categories", dict(self.categories or {}))
        object.__setattr__(self, "summary", str(self.summary or ""))
        object.__setattr__(
            self,
            "checks",
            tuple(
                item if isinstance(item, JudgeCheck) else JudgeCheck(**dict(item)) for item in list(self.checks or ())
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": self.score,
            "threshold": self.threshold,
            "categories": dict(self.categories),
            "summary": self.summary,
            "checks": [item.to_dict() for item in self.checks],
        }
