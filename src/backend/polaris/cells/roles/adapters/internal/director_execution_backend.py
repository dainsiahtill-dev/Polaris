"""Director execution backend resolution and projection dispatch helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_EXECUTION_BACKEND = "code_edit"
_PROJECTION_GENERATE_BACKEND = "projection_generate"
_PROJECTION_REFRESH_MAPPING_BACKEND = "projection_refresh_mapping"
_PROJECTION_REPROJECT_BACKEND = "projection_reproject"

SUPPORTED_EXECUTION_BACKENDS = frozenset(
    {
        _DEFAULT_EXECUTION_BACKEND,
        _PROJECTION_GENERATE_BACKEND,
        _PROJECTION_REFRESH_MAPPING_BACKEND,
        _PROJECTION_REPROJECT_BACKEND,
    }
)
PROJECTION_EXECUTION_BACKENDS = frozenset(
    {
        _PROJECTION_GENERATE_BACKEND,
        _PROJECTION_REFRESH_MAPPING_BACKEND,
        _PROJECTION_REPROJECT_BACKEND,
    }
)

_PROJECT_SLUG_PATTERN = re.compile(r"[^a-z0-9_]+")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_backend(value: Any) -> str:
    token = _normalize_text(value).lower()
    if not token:
        return _DEFAULT_EXECUTION_BACKEND
    if token in SUPPORTED_EXECUTION_BACKENDS:
        return token
    return token


def _normalize_project_slug(value: Any, *, default_value: str) -> str:
    lowered = _normalize_text(value).lower()
    normalized = _PROJECT_SLUG_PATTERN.sub("_", lowered).strip("_")
    return normalized or default_value


def _mapping_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return {str(key): value for key, value in payload.items()}
    return {}


@dataclass(frozen=True)
class DirectorExecutionBackendRequest:
    """Resolved execution backend request for one Director task."""

    execution_backend: str = _DEFAULT_EXECUTION_BACKEND
    source: str = "default"
    scenario_id: str = ""
    requirement: str = ""
    experiment_id: str = ""
    project_slug: str = ""
    use_pm_llm: bool = True
    run_verification: bool = True
    overwrite: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_projection_backend(self) -> bool:
        return self.execution_backend in PROJECTION_EXECUTION_BACKENDS

    @property
    def is_supported(self) -> bool:
        return self.execution_backend in SUPPORTED_EXECUTION_BACKENDS

    def to_task_metadata(self) -> dict[str, Any]:
        """Return normalized runtime metadata for taskboard/task_runtime rows."""
        payload: dict[str, Any] = {
            "execution_backend": self.execution_backend,
            "execution_backend_source": self.source,
        }
        if self.is_projection_backend:
            payload["projection"] = {
                "scenario_id": self.scenario_id,
                "experiment_id": self.experiment_id,
                "project_slug": self.project_slug,
                "requirement": self.requirement,
                "use_pm_llm": self.use_pm_llm,
                "run_verification": self.run_verification,
                "overwrite": self.overwrite,
            }
        if self.metadata:
            payload["execution_backend_metadata"] = dict(self.metadata)
        return payload


def resolve_director_execution_backend(
    *,
    input_data: Mapping[str, Any] | None,
    task: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    default_project_slug: str,
) -> DirectorExecutionBackendRequest:
    """Resolve Director execution backend from context, task metadata, and input."""

    context_map = _mapping_payload(context)
    task_map = _mapping_payload(task)
    task_metadata = _mapping_payload(task_map.get("metadata"))
    input_map = _mapping_payload(input_data)
    input_metadata = _mapping_payload(input_map.get("metadata"))

    combined_projection: dict[str, Any] = {}
    for candidate in (
        _mapping_payload(context_map.get("projection")),
        _mapping_payload(task_metadata.get("projection")),
        _mapping_payload(input_metadata.get("projection")),
        _mapping_payload(input_map.get("projection")),
    ):
        if candidate:
            combined_projection.update(candidate)  # type: ignore[union-attr]

    # Re-declare to help mypy narrow the type after the loop
    combined_projection: dict[str, Any] = combined_projection  # type: ignore[no-redef]

    execution_backend = _DEFAULT_EXECUTION_BACKEND
    source = "default"
    for candidate_source, candidate_value in (
        ("context", context_map.get("director_execution_backend") or context_map.get("execution_backend")),
        ("task_metadata", task_metadata.get("execution_backend")),
        ("input_metadata", input_metadata.get("execution_backend")),
        ("input_data", input_map.get("execution_backend")),
    ):
        normalized = _normalize_text(candidate_value).lower()
        if normalized:
            execution_backend = _normalize_backend(normalized)
            source = candidate_source

    requirement = ""
    for candidate in (  # type: ignore[assignment]
        input_map.get("projection_requirement"),
        input_map.get("requirement_delta"),
        input_map.get("requirement"),
        input_metadata.get("projection_requirement"),
        input_metadata.get("requirement_delta"),
        combined_projection.get("requirement"),
        task_metadata.get("projection_requirement"),
        task_metadata.get("requirement_delta"),
    ):
        # candidate is Any | None from .get(), but we normalize to str
        normalized_str = _normalize_text(candidate)  # type: ignore[arg-type]
        if normalized_str:
            requirement = normalized_str
            break

    scenario_id = ""
    for candidate in (  # type: ignore[assignment]
        input_map.get("projection_scenario"),
        input_map.get("scenario_id"),
        input_metadata.get("projection_scenario"),
        input_metadata.get("scenario_id"),
        combined_projection.get("scenario_id"),
        task_metadata.get("projection_scenario"),
        task_metadata.get("scenario_id"),
    ):
        # candidate is Any | None from .get(), but we normalize to str
        normalized_str2 = _normalize_text(candidate).lower()  # type: ignore[arg-type]
        if normalized_str2:
            scenario_id = normalized_str2
            break

    experiment_id = ""
    for candidate in (  # type: ignore[assignment]
        input_map.get("experiment_id"),
        input_map.get("projection_experiment_id"),
        input_metadata.get("experiment_id"),
        input_metadata.get("projection_experiment_id"),
        combined_projection.get("experiment_id"),
        task_metadata.get("experiment_id"),
        task_metadata.get("projection_experiment_id"),
    ):
        # candidate is Any | None from .get(), but we normalize to str
        normalized = _normalize_text(candidate)  # type: ignore[arg-type]
        if normalized:
            experiment_id = normalized
            break

    project_slug = ""
    for candidate in (  # type: ignore[assignment]
        input_map.get("project_slug"),
        input_metadata.get("project_slug"),
        combined_projection.get("project_slug"),
        task_metadata.get("project_slug"),
    ):
        # candidate is Any | None from .get(), but we normalize to str
        normalized = _normalize_text(candidate)  # type: ignore[arg-type]
        if normalized:
            project_slug = _normalize_project_slug(normalized, default_value=default_project_slug)
            break
    if not project_slug:
        project_slug = _normalize_project_slug(default_project_slug, default_value="projection_lab")

    use_pm_llm = _normalize_bool(
        input_map.get("use_pm_llm", input_metadata.get("use_pm_llm", combined_projection.get("use_pm_llm"))),
        default=True,
    )
    run_verification = _normalize_bool(
        input_map.get(
            "run_verification",
            input_metadata.get("run_verification", combined_projection.get("run_verification")),
        ),
        default=True,
    )
    overwrite = _normalize_bool(
        input_map.get("overwrite", input_metadata.get("overwrite", combined_projection.get("overwrite"))),
        default=False,
    )

    return DirectorExecutionBackendRequest(
        execution_backend=execution_backend,
        source=source,
        scenario_id=scenario_id,
        requirement=requirement,
        experiment_id=experiment_id,
        project_slug=project_slug,
        use_pm_llm=use_pm_llm,
        run_verification=run_verification,
        overwrite=overwrite,
        metadata={
            "context_projection": combined_projection,
        },
    )


class DirectorProjectionBackendRunner:
    """Invoke projection/back-mapping capabilities via `factory.pipeline` public contracts."""

    def __init__(self, workspace: str) -> None:
        self._workspace = _normalize_text(workspace)
        if not self._workspace:
            raise ValueError("workspace is required")

    def execute(
        self,
        request: DirectorExecutionBackendRequest,
    ) -> dict[str, Any]:
        """Execute one projection-oriented backend request."""
        if request.execution_backend == _PROJECTION_GENERATE_BACKEND:
            return self._run_projection_generate(request)
        if request.execution_backend == _PROJECTION_REFRESH_MAPPING_BACKEND:
            return self._run_projection_refresh_mapping(request)
        if request.execution_backend == _PROJECTION_REPROJECT_BACKEND:
            return self._run_projection_reproject(request)
        raise ValueError(f"unsupported projection backend: {request.execution_backend}")

    def _run_projection_generate(
        self,
        request: DirectorExecutionBackendRequest,
    ) -> dict[str, Any]:
        from polaris.cells.factory.pipeline.public.service import (
            FactoryProjectionLabService,
            RunProjectionExperimentCommandV1,
        )

        if not request.scenario_id:
            raise ValueError("projection_generate requires scenario_id")
        if not request.requirement:
            raise ValueError("projection_generate requires requirement")

        service = FactoryProjectionLabService(self._workspace)
        result = service.run_projection_experiment(
            RunProjectionExperimentCommandV1(
                workspace=self._workspace,
                scenario_id=request.scenario_id,
                requirement=request.requirement,
                project_slug=request.project_slug or "projection_lab",
                use_pm_llm=request.use_pm_llm,
                run_verification=request.run_verification,
                overwrite=request.overwrite,
            )
        )
        payload = result.to_dict()
        return {
            "success": bool(result.ok),
            "execution_backend": request.execution_backend,
            "projection_result": payload,
            "artifacts": list(result.artifact_paths),
            "summary": result.summary,
            "experiment_id": result.experiment_id,
            "project_root": result.project_root,
            "cell_ids": list(result.cell_ids),
            "generated_files": list(result.generated_files),
            "verification_ok": bool(result.verification_ok),
            "normalization_source": result.normalization_source,
        }

    def _run_projection_refresh_mapping(
        self,
        request: DirectorExecutionBackendRequest,
    ) -> dict[str, Any]:
        from polaris.cells.factory.pipeline.public.service import (
            ProjectionChangeAnalysisService,
            RefreshProjectionBackMappingCommandV1,
        )

        if not request.experiment_id:
            raise ValueError("projection_refresh_mapping requires experiment_id")

        service = ProjectionChangeAnalysisService(self._workspace)
        result = service.refresh_back_mapping_result(
            RefreshProjectionBackMappingCommandV1(
                workspace=self._workspace,
                experiment_id=request.experiment_id,
            )
        )
        payload = result.to_dict()
        return {
            "success": True,
            "execution_backend": request.execution_backend,
            "projection_result": payload,
            "artifacts": [f"workspace/factory/projection_lab/{request.experiment_id}/back_mapping_refresh_report.json"],
            "summary": (f"changed_files={len(result.changed_files)}; impacted_cells={len(result.impacted_cell_ids)}"),
            "experiment_id": result.experiment_id,
            "project_root": result.project_root,
            "impacted_cell_ids": list(result.impacted_cell_ids),
            "changed_files": [dict(item) for item in result.changed_files],
        }

    def _run_projection_reproject(
        self,
        request: DirectorExecutionBackendRequest,
    ) -> dict[str, Any]:
        from polaris.cells.factory.pipeline.public.service import (
            FactoryProjectionLabService,
            ReprojectProjectionExperimentCommandV1,
        )

        if not request.experiment_id:
            raise ValueError("projection_reproject requires experiment_id")
        if not request.requirement:
            raise ValueError("projection_reproject requires requirement")

        service = FactoryProjectionLabService(self._workspace)
        result = service.reproject_experiment(
            ReprojectProjectionExperimentCommandV1(
                workspace=self._workspace,
                experiment_id=request.experiment_id,
                requirement=request.requirement,
                use_pm_llm=request.use_pm_llm,
                run_verification=request.run_verification,
            )
        )
        payload = result.to_dict()
        return {
            "success": bool(result.ok),
            "execution_backend": request.execution_backend,
            "projection_result": payload,
            "artifacts": list(result.artifact_paths),
            "summary": result.summary,
            "experiment_id": result.experiment_id,
            "project_root": result.project_root,
            "impacted_cell_ids": list(result.impacted_cell_ids),
            "rewritten_files": list(result.rewritten_files),
            "verification_ok": bool(result.verification_ok),
            "normalization_source": result.normalization_source,
        }
