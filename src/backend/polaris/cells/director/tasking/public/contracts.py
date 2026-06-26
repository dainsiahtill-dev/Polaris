"""Public contracts for director.tasking cell.

These contracts define the stable public interface for task lifecycle management,
worker pool orchestration, and task execution within the Director system.

All symbols here should be imported by external consumers (Facade, other Cells).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _to_str_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_items: list[Any] = [values]
    elif isinstance(values, (list, tuple, set, frozenset)):
        raw_items = list(values)
    else:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            normalized.append(token)
    return tuple(normalized)


def _to_path_roles(payload: Mapping[str, Any] | None) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for raw_path, raw_roles in dict(payload or {}).items():
        path = str(raw_path or "").strip().replace("\\", "/")
        if not path:
            continue
        roles = _to_str_tuple(raw_roles)
        if roles:
            result[path] = roles
    return result


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = int(default)
    return max(1, normalized)


# ---------------------------------------------------------------------------
# Canonical execution metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskExecutionProfileV1:
    """Canonical task execution profile.

    Polaris normalizes loose PM/CE/task metadata into this contract once, then
    reuses it for dispatch compatibility, prompt guidance, sampling policy,
    output protocol, and final request audit. This prevents prompt, execution,
    audit, and temperature code from maintaining independent task classifiers.
    """

    schema_version: str = "task.execution_profile.v1"
    source: str = "director.tasking"
    dispatch_type: str = "generic"
    task_type: str = "generic"
    phase: str = "implementation"
    project_type: str = "generic"
    language: str = "generic"
    language_display_name: str = "generic/unknown"
    framework: str = ""
    framework_display_name: str = ""
    task_foci: tuple[str, ...] = ()
    task_focus_labels: tuple[str, ...] = ()
    file_roles: tuple[str, ...] = ()
    file_role_labels: tuple[str, ...] = ()
    file_roles_by_path: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    generation_mode: str = "proposal_then_apply"
    output_contract_id: str = "director.patch_file.v1"
    scope_policy: str = "target_files_or_declared_scopes"
    sampling_mode: str = "precise"
    temperature_phase: str = "code_generation"
    temperature: float = 0.15
    temperature_source: str = "task.execution_profile.v1"
    target_files: tuple[str, ...] = ()
    scope_paths: tuple[str, ...] = ()
    signal_evidence: Mapping[str, Any] = field(default_factory=dict)
    normalization_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "dispatch_type", _require_non_empty("dispatch_type", self.dispatch_type))
        object.__setattr__(self, "task_type", _require_non_empty("task_type", self.task_type))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "project_type", _require_non_empty("project_type", self.project_type))
        object.__setattr__(self, "language", _require_non_empty("language", self.language))
        object.__setattr__(self, "language_display_name", str(self.language_display_name or "generic/unknown"))
        object.__setattr__(self, "framework", str(self.framework or "").strip())
        object.__setattr__(self, "framework_display_name", str(self.framework_display_name or "").strip())
        object.__setattr__(self, "task_foci", _to_str_tuple(self.task_foci))
        object.__setattr__(self, "task_focus_labels", _to_str_tuple(self.task_focus_labels))
        object.__setattr__(self, "file_roles", _to_str_tuple(self.file_roles))
        object.__setattr__(self, "file_role_labels", _to_str_tuple(self.file_role_labels))
        object.__setattr__(self, "file_roles_by_path", _to_path_roles(self.file_roles_by_path))
        object.__setattr__(self, "generation_mode", _require_non_empty("generation_mode", self.generation_mode))
        object.__setattr__(
            self,
            "output_contract_id",
            _require_non_empty("output_contract_id", self.output_contract_id),
        )
        object.__setattr__(self, "scope_policy", _require_non_empty("scope_policy", self.scope_policy))
        object.__setattr__(self, "sampling_mode", _require_non_empty("sampling_mode", self.sampling_mode))
        object.__setattr__(self, "temperature_phase", _require_non_empty("temperature_phase", self.temperature_phase))
        object.__setattr__(self, "temperature", max(0.0, min(2.0, float(self.temperature))))
        object.__setattr__(
            self,
            "temperature_source",
            _require_non_empty("temperature_source", self.temperature_source),
        )
        object.__setattr__(self, "target_files", _to_str_tuple(self.target_files))
        object.__setattr__(self, "scope_paths", _to_str_tuple(self.scope_paths))
        object.__setattr__(self, "signal_evidence", _to_dict_copy(self.signal_evidence))
        object.__setattr__(self, "normalization_warnings", _to_str_tuple(self.normalization_warnings))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe profile payload for runtime context and receipts."""

        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "dispatch_type": self.dispatch_type,
            "task_type": self.task_type,
            "phase": self.phase,
            "project_type": self.project_type,
            "language": self.language,
            "language_display_name": self.language_display_name,
            "framework": self.framework,
            "framework_display_name": self.framework_display_name,
            "task_foci": list(self.task_foci),
            "task_focus_labels": list(self.task_focus_labels),
            "file_roles": list(self.file_roles),
            "file_role_labels": list(self.file_role_labels),
            "file_roles_by_path": {path: list(roles) for path, roles in self.file_roles_by_path.items()},
            "generation_mode": self.generation_mode,
            "output_contract_id": self.output_contract_id,
            "scope_policy": self.scope_policy,
            "sampling_mode": self.sampling_mode,
            "temperature_phase": self.temperature_phase,
            "temperature": self.temperature,
            "temperature_source": self.temperature_source,
            "target_files": list(self.target_files),
            "scope_paths": list(self.scope_paths),
            "signal_evidence": dict(self.signal_evidence),
            "normalization_warnings": list(self.normalization_warnings),
        }


DirectorExecutionProfileV1 = TaskExecutionProfileV1
"""Backward-compatible name for Director-owned consumers.

New code should prefer ``TaskExecutionProfileV1``. The compatibility alias keeps
existing Director imports stable while the profile contract moves to the
platform-level task-execution vocabulary.
"""


@dataclass(frozen=True)
class TaskExecutionStrategyV1:
    """Execution strategy derived from a canonical task execution profile.

    The profile describes what the Director is doing; this strategy describes
    how the runtime should budget and audit that work. Prompts, output tokens,
    sampling, ContextOS budget intent, and final-request audit must consume this
    contract instead of maintaining separate heuristics.
    """

    schema_version: str = "task.execution_strategy.v1"
    source: str = "director.tasking"
    profile_schema_version: str = "task.execution_profile.v1"
    profile_hash_source: str = "task.execution_profile.v1"
    temperature: float = 0.15
    temperature_phase: str = "code_generation"
    sampling_mode: str = "precise"
    output_budget_tokens: int = 16_000
    input_budget_tokens: int = 48_000
    prompt_max_chars: int = 120_000
    min_context_utilization: float = 0.03
    context_underutilized_policy: str = "warn"
    evidence_requirements: tuple[str, ...] = ()
    prompt_profile_mode: str = "profile_driven"
    prompt_profile_required: bool = True
    context_budget_policy: Mapping[str, Any] = field(default_factory=dict)
    target_files: tuple[str, ...] = ()
    scope_paths: tuple[str, ...] = ()
    signal_evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(
            self,
            "profile_schema_version",
            _require_non_empty("profile_schema_version", self.profile_schema_version),
        )
        object.__setattr__(
            self,
            "profile_hash_source",
            _require_non_empty("profile_hash_source", self.profile_hash_source),
        )
        object.__setattr__(self, "temperature", max(0.0, min(2.0, _to_float(self.temperature, 0.15))))
        object.__setattr__(
            self,
            "temperature_phase",
            _require_non_empty("temperature_phase", self.temperature_phase),
        )
        object.__setattr__(self, "sampling_mode", _require_non_empty("sampling_mode", self.sampling_mode))
        object.__setattr__(
            self,
            "output_budget_tokens",
            min(_to_positive_int(self.output_budget_tokens, 16_000), 128_000),
        )
        object.__setattr__(
            self,
            "input_budget_tokens",
            min(_to_positive_int(self.input_budget_tokens, 48_000), 512_000),
        )
        object.__setattr__(
            self,
            "prompt_max_chars",
            min(_to_positive_int(self.prompt_max_chars, 120_000), 1_500_000),
        )
        object.__setattr__(
            self,
            "min_context_utilization",
            max(0.0, min(1.0, _to_float(self.min_context_utilization, 0.03))),
        )
        object.__setattr__(
            self,
            "context_underutilized_policy",
            _require_non_empty("context_underutilized_policy", self.context_underutilized_policy),
        )
        object.__setattr__(self, "evidence_requirements", _to_str_tuple(self.evidence_requirements))
        object.__setattr__(
            self,
            "prompt_profile_mode",
            _require_non_empty("prompt_profile_mode", self.prompt_profile_mode),
        )
        object.__setattr__(self, "prompt_profile_required", bool(self.prompt_profile_required))
        object.__setattr__(self, "context_budget_policy", _to_dict_copy(self.context_budget_policy))
        object.__setattr__(self, "target_files", _to_str_tuple(self.target_files))
        object.__setattr__(self, "scope_paths", _to_str_tuple(self.scope_paths))
        object.__setattr__(self, "signal_evidence", _to_dict_copy(self.signal_evidence))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe strategy payload for runtime context and audit."""

        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "profile_schema_version": self.profile_schema_version,
            "profile_hash_source": self.profile_hash_source,
            "temperature": self.temperature,
            "temperature_phase": self.temperature_phase,
            "sampling_mode": self.sampling_mode,
            "output_budget_tokens": self.output_budget_tokens,
            "input_budget_tokens": self.input_budget_tokens,
            "prompt_max_chars": self.prompt_max_chars,
            "min_context_utilization": self.min_context_utilization,
            "context_underutilized_policy": self.context_underutilized_policy,
            "evidence_requirements": list(self.evidence_requirements),
            "prompt_profile_mode": self.prompt_profile_mode,
            "prompt_profile_required": self.prompt_profile_required,
            "context_budget_policy": dict(self.context_budget_policy),
            "target_files": list(self.target_files),
            "scope_paths": list(self.scope_paths),
            "signal_evidence": dict(self.signal_evidence),
        }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateTaskCommandV1:
    """Command to create a new task in the Director tasking system."""

    subject: str
    workspace: str
    description: str = ""
    command: str | None = None
    priority: str = "medium"
    blocked_by: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _require_non_empty("subject", self.subject))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "description", str(self.description))
        object.__setattr__(self, "blocked_by", list(self.blocked_by or []))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class CancelTaskCommandV1:
    """Command to cancel a pending or ready task."""

    task_id: str
    workspace: str
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskStatusQueryV1:
    """Query the status of one or more tasks."""

    workspace: str
    task_id: str | None = None
    status: str | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class TaskResultQueryV1:
    """Query the result of a completed task."""

    task_id: str
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskCreatedResultV1:
    """Result of a task creation command."""

    ok: bool
    task_id: str
    workspace: str
    subject: str
    status: str = "pending"
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "subject", _require_non_empty("subject", self.subject))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


@dataclass(frozen=True)
class TaskStatusResultV1:
    """Result of a task status query."""

    ok: bool
    workspace: str
    tasks: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class TaskResultResultV1:
    """Result of a task result query."""

    ok: bool
    task_id: str
    workspace: str
    success: bool | None = None
    output: str = ""
    error: str | None = None
    duration_ms: int | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DirectorTaskingError(RuntimeError):
    """Raised when director.tasking contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "director_tasking_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "CancelTaskCommandV1",
    "CreateTaskCommandV1",
    "DirectorExecutionProfileV1",
    "DirectorTaskingError",
    "TaskCreatedResultV1",
    "TaskExecutionProfileV1",
    "TaskExecutionStrategyV1",
    "TaskResultQueryV1",
    "TaskResultResultV1",
    "TaskStatusQueryV1",
    "TaskStatusResultV1",
]
