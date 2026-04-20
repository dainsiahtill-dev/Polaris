"""Public contracts for `factory.pipeline`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class StartFactoryRunCommandV1:
    workspace: str
    run_name: str
    stages: tuple[str, ...]
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_name", _require_non_empty("run_name", self.run_name))
        object.__setattr__(self, "stages", tuple(str(v) for v in self.stages if str(v).strip()))
        if not self.stages:
            raise ValueError("stages must not be empty")
        object.__setattr__(self, "options", _to_dict_copy(self.options))


@dataclass(frozen=True)
class CancelFactoryRunCommandV1:
    workspace: str
    run_id: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))


@dataclass(frozen=True)
class GetFactoryRunStatusQueryV1:
    workspace: str
    run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))


@dataclass(frozen=True)
class ListFactoryRunsQueryV1:
    workspace: str
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")


@dataclass(frozen=True)
class RunProjectionExperimentCommandV1:
    workspace: str
    scenario_id: str
    requirement: str
    project_slug: str = "projection_lab"
    use_pm_llm: bool = True
    run_verification: bool = True
    overwrite: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "scenario_id", _require_non_empty("scenario_id", self.scenario_id))
        object.__setattr__(self, "requirement", _require_non_empty("requirement", self.requirement))
        object.__setattr__(self, "project_slug", _require_non_empty("project_slug", self.project_slug))
        object.__setattr__(self, "use_pm_llm", bool(self.use_pm_llm))
        object.__setattr__(self, "run_verification", bool(self.run_verification))
        object.__setattr__(self, "overwrite", bool(self.overwrite))


@dataclass(frozen=True)
class RefreshProjectionBackMappingCommandV1:
    workspace: str
    experiment_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))


@dataclass(frozen=True)
class ReprojectProjectionExperimentCommandV1:
    workspace: str
    experiment_id: str
    requirement: str
    use_pm_llm: bool = True
    run_verification: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))
        object.__setattr__(self, "requirement", _require_non_empty("requirement", self.requirement))
        object.__setattr__(self, "use_pm_llm", bool(self.use_pm_llm))
        object.__setattr__(self, "run_verification", bool(self.run_verification))


@dataclass(frozen=True)
class FactoryRunStartedEventV1:
    event_id: str
    workspace: str
    run_id: str
    started_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "started_at", _require_non_empty("started_at", self.started_at))


@dataclass(frozen=True)
class FactoryRunCompletedEventV1:
    event_id: str
    workspace: str
    run_id: str
    status: str
    completed_at: str
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class FactoryRunResultV1:
    ok: bool
    workspace: str
    run_id: str
    status: str
    completed_stages: tuple[str, ...] = field(default_factory=tuple)
    artifact_paths: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_stages", tuple(str(v) for v in self.completed_stages if str(v).strip()))
        object.__setattr__(self, "artifact_paths", tuple(str(v) for v in self.artifact_paths if str(v).strip()))


@dataclass(frozen=True)
class ProjectionExperimentResultV1:
    ok: bool
    workspace: str
    experiment_id: str
    scenario_id: str
    project_root: str
    generated_files: tuple[str, ...] = field(default_factory=tuple)
    artifact_paths: tuple[str, ...] = field(default_factory=tuple)
    cell_ids: tuple[str, ...] = field(default_factory=tuple)
    verification_ok: bool = False
    normalization_source: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))
        object.__setattr__(self, "scenario_id", _require_non_empty("scenario_id", self.scenario_id))
        object.__setattr__(self, "project_root", _require_non_empty("project_root", self.project_root))
        object.__setattr__(self, "generated_files", tuple(str(v) for v in self.generated_files if str(v).strip()))
        object.__setattr__(self, "artifact_paths", tuple(str(v) for v in self.artifact_paths if str(v).strip()))
        object.__setattr__(self, "cell_ids", tuple(str(v) for v in self.cell_ids if str(v).strip()))
        object.__setattr__(self, "verification_ok", bool(self.verification_ok))
        object.__setattr__(self, "normalization_source", str(self.normalization_source or "").strip())
        object.__setattr__(self, "summary", str(self.summary or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "workspace": self.workspace,
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "project_root": self.project_root,
            "generated_files": list(self.generated_files),
            "artifact_paths": list(self.artifact_paths),
            "cell_ids": list(self.cell_ids),
            "verification_ok": self.verification_ok,
            "normalization_source": self.normalization_source,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ProjectionBackMappingRefreshResultV1:
    workspace: str
    experiment_id: str
    project_root: str
    changed_files: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    added_symbols: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    removed_symbols: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    modified_symbols: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    impacted_cell_ids: tuple[str, ...] = field(default_factory=tuple)
    mapping_strategy: str = ""
    previous_mapping_strategy: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))
        object.__setattr__(self, "project_root", _require_non_empty("project_root", self.project_root))
        object.__setattr__(self, "changed_files", tuple(dict(item) for item in self.changed_files))
        object.__setattr__(self, "added_symbols", tuple(dict(item) for item in self.added_symbols))
        object.__setattr__(self, "removed_symbols", tuple(dict(item) for item in self.removed_symbols))
        object.__setattr__(self, "modified_symbols", tuple(dict(item) for item in self.modified_symbols))
        object.__setattr__(self, "impacted_cell_ids", tuple(str(v) for v in self.impacted_cell_ids if str(v).strip()))
        object.__setattr__(self, "mapping_strategy", str(self.mapping_strategy or "").strip())
        object.__setattr__(self, "previous_mapping_strategy", str(self.previous_mapping_strategy or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "experiment_id": self.experiment_id,
            "project_root": self.project_root,
            "changed_files": [dict(item) for item in self.changed_files],
            "added_symbols": [dict(item) for item in self.added_symbols],
            "removed_symbols": [dict(item) for item in self.removed_symbols],
            "modified_symbols": [dict(item) for item in self.modified_symbols],
            "impacted_cell_ids": list(self.impacted_cell_ids),
            "mapping_strategy": self.mapping_strategy,
            "previous_mapping_strategy": self.previous_mapping_strategy,
        }


@dataclass(frozen=True)
class ProjectionReprojectionResultV1:
    ok: bool
    workspace: str
    experiment_id: str
    scenario_id: str
    project_root: str
    impacted_cell_ids: tuple[str, ...] = field(default_factory=tuple)
    rewritten_files: tuple[str, ...] = field(default_factory=tuple)
    artifact_paths: tuple[str, ...] = field(default_factory=tuple)
    verification_ok: bool = False
    normalization_source: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))
        object.__setattr__(self, "scenario_id", _require_non_empty("scenario_id", self.scenario_id))
        object.__setattr__(self, "project_root", _require_non_empty("project_root", self.project_root))
        object.__setattr__(self, "impacted_cell_ids", tuple(str(v) for v in self.impacted_cell_ids if str(v).strip()))
        object.__setattr__(self, "rewritten_files", tuple(str(v) for v in self.rewritten_files if str(v).strip()))
        object.__setattr__(self, "artifact_paths", tuple(str(v) for v in self.artifact_paths if str(v).strip()))
        object.__setattr__(self, "verification_ok", bool(self.verification_ok))
        object.__setattr__(self, "normalization_source", str(self.normalization_source or "").strip())
        object.__setattr__(self, "summary", str(self.summary or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "workspace": self.workspace,
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "project_root": self.project_root,
            "impacted_cell_ids": list(self.impacted_cell_ids),
            "rewritten_files": list(self.rewritten_files),
            "artifact_paths": list(self.artifact_paths),
            "verification_ok": self.verification_ok,
            "normalization_source": self.normalization_source,
            "summary": self.summary,
        }


class FactoryPipelineError(RuntimeError):
    """Raised when `factory.pipeline` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "factory_pipeline_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IFactoryPipeline(Protocol):
    async def run_pipeline(self, project_path: str, config: Mapping[str, Any]) -> Mapping[str, Any]:
        """Compatibility API kept for existing integrations."""


@runtime_checkable
class IFactoryProjectionLab(Protocol):
    def run_projection_experiment(
        self,
        command: RunProjectionExperimentCommandV1,
    ) -> ProjectionExperimentResultV1:
        """Compile one controlled projection experiment into a workspace."""


__all__ = [
    "CancelFactoryRunCommandV1",
    "FactoryPipelineError",
    "FactoryRunCompletedEventV1",
    "FactoryRunResultV1",
    "FactoryRunStartedEventV1",
    "GetFactoryRunStatusQueryV1",
    "IFactoryPipeline",
    "IFactoryProjectionLab",
    "ListFactoryRunsQueryV1",
    "ProjectionBackMappingRefreshResultV1",
    "ProjectionExperimentResultV1",
    "ProjectionReprojectionResultV1",
    "RefreshProjectionBackMappingCommandV1",
    "ReprojectProjectionExperimentCommandV1",
    "RunProjectionExperimentCommandV1",
    "StartFactoryRunCommandV1",
]
