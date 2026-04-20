"""Projection lab service for factory.pipeline.

This service materializes a small but real target project from Cell IR metadata,
projection rules, and a controlled scenario manifest. The generated project is
written into a workspace experiment directory, while internal IR and projection
artifacts are persisted into Polaris-managed hidden storage.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from polaris.cells.factory.pipeline.public.contracts import (
    FactoryPipelineError,
    ProjectionExperimentResultV1,
    ProjectionReprojectionResultV1,
    ReprojectProjectionExperimentCommandV1,
    RunProjectionExperimentCommandV1,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest

from .back_mapping import build_python_back_mapping_index
from .json_cli_app_renderer import JsonCliAppRenderer
from .models import CommandSpec, EntitySpec, FieldSpec, ProjectionEntry, TargetCellSpec, TargetProjectManifest
from .projection_change_analysis import ProjectionChangeAnalysisService
from .resource_http_service_renderer import ResourceHttpServiceRenderer

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_PROJECT_SLUG_PATTERN = re.compile(r"[^a-z0-9_]+")


class ProjectRenderer(Protocol):
    """Render one project style into a traditional source tree."""

    def render(self, manifest: TargetProjectManifest) -> dict[str, str]:
        """Render a project manifest into relative file paths."""


class ProjectVerificationRunner(Protocol):
    """Run verification commands against a projected project root."""

    def run(
        self,
        project_root: Path,
        commands: tuple[tuple[str, ...], ...],
    ) -> dict[str, object]:
        """Execute verification commands for a project root."""


@dataclass(frozen=True)
class ProjectionScenarioDefinition:
    """Describe how one experiment scenario is normalized and projected."""

    scenario_id: str
    manifest_builder: Callable[[RunProjectionExperimentCommandV1, dict[str, Any]], TargetProjectManifest]
    target_cell_builder: Callable[[TargetProjectManifest], tuple[TargetCellSpec, ...]]
    projection_entry_builder: Callable[[TargetProjectManifest], tuple[ProjectionEntry, ...]]
    pm_prompt_builder: Callable[[str], str]


class SubprocessProjectVerificationRunner:
    """Verification runner backed by subprocess execution."""

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self._timeout_seconds = max(int(timeout_seconds), 5)

    def run(
        self,
        project_root: Path,
        commands: tuple[tuple[str, ...], ...],
    ) -> dict[str, object]:
        if not commands:
            return {
                "ok": True,
                "commands": [],
                "summary": "verification skipped: no commands configured",
            }
        results: list[dict[str, object]] = []
        for command in commands:
            cmd_list = list(command)
            cmd_svc = CommandExecutionService(str(project_root))
            request = CommandRequest(
                executable=cmd_list[0],
                args=cmd_list[1:] if len(cmd_list) > 1 else [],
                cwd=str(project_root),
                timeout_seconds=int(self._timeout_seconds) if self._timeout_seconds else 60,
            )
            completed_result = cmd_svc.run(request)
            result = {
                "command": list(command),
                "returncode": completed_result.get("returncode", -1),
                "stdout": str(completed_result.get("stdout", "") or ""),
                "stderr": str(completed_result.get("stderr", "") or ""),
                "ok": completed_result.get("ok", False),
            }
            results.append(result)
            if not completed_result.get("ok", False):
                return {
                    "ok": False,
                    "commands": results,
                    "summary": f"verification failed: {' '.join(command)} exited with {completed_result.get('returncode', -1)}",
                }
        return {
            "ok": True,
            "commands": results,
            "summary": "verification passed",
        }


class FactoryProjectionLabService:
    """Compile controlled experiment projects from Cell IR manifests."""

    def __init__(
        self,
        workspace: str,
        *,
        kernel_fs: KernelFileSystem | None = None,
        renderer: ProjectRenderer | None = None,
        verification_runner: ProjectVerificationRunner | None = None,
    ) -> None:
        workspace_text = str(workspace or "").strip()
        if not workspace_text:
            raise ValueError("workspace must be a non-empty string")
        self.workspace = str(Path(workspace_text).resolve())
        self._kernel_fs = kernel_fs or KernelFileSystem(self.workspace, get_default_adapter())
        self._renderers: dict[str, ProjectRenderer] = {
            "json_cli_app": renderer or JsonCliAppRenderer(),
            "resource_http_service": ResourceHttpServiceRenderer(),
        }
        self._verification_runner = verification_runner or SubprocessProjectVerificationRunner()
        self._scenarios = self._build_scenarios()

    def run_projection_experiment(
        self,
        command: RunProjectionExperimentCommandV1,
    ) -> ProjectionExperimentResultV1:
        if str(Path(command.workspace).resolve()) != self.workspace:
            raise FactoryPipelineError(
                "Command workspace does not match service workspace",
                code="workspace_mismatch",
                details={"command_workspace": command.workspace, "service_workspace": self.workspace},
            )
        scenario = self._get_scenario_definition(command.scenario_id)
        normalization = self._normalize_requirement(command, scenario)
        manifest = scenario.manifest_builder(command, normalization)
        project_root_relative = manifest.project_root
        if self._kernel_fs.workspace_exists(project_root_relative) and not command.overwrite:
            raise FactoryPipelineError(
                f"Experiment project already exists: {project_root_relative}",
                code="project_exists",
                details={"project_root": project_root_relative},
            )

        run_id = uuid.uuid4().hex
        target_cells = scenario.target_cell_builder(manifest)
        projection_entries = scenario.projection_entry_builder(manifest)
        rendered_files = self._resolve_renderer(manifest.project_style, scenario.scenario_id).render(manifest)

        generated_files: list[str] = []
        for relative_path, content in rendered_files.items():
            destination = f"{project_root_relative}/{relative_path}".replace("//", "/")
            self._kernel_fs.workspace_write_text(destination, content, encoding="utf-8")
            generated_files.append(destination)

        artifact_root = f"workspace/factory/projection_lab/{run_id}"
        requirement_analysis_path = f"{artifact_root}/requirement_analysis.json"
        manifest_path = f"{artifact_root}/manifest.json"
        cell_ir_path = f"{artifact_root}/cell_ir.json"
        projection_map_path = f"{artifact_root}/projection_map.json"
        verification_path = f"{artifact_root}/verification_report.json"
        back_mapping_path = f"{artifact_root}/back_mapping_index.json"

        self._kernel_fs.write_json(requirement_analysis_path, normalization)
        self._kernel_fs.write_json(
            manifest_path,
            {
                "normalization": normalization,
                "manifest": manifest.to_dict(),
            },
        )
        self._kernel_fs.write_json(
            cell_ir_path,
            {
                "run_id": run_id,
                "scenario_id": manifest.scenario_id,
                "project_root": project_root_relative,
                "wave_particle_model": {
                    "wave_form": "semantic_halo",
                    "particle_form": "contract_nucleus",
                    "projection": "traditional_python_project",
                },
                "normalization": normalization,
                "manifest": manifest.to_dict(),
                "target_cells": [item.to_dict() for item in target_cells],
            },
        )
        self._kernel_fs.write_json(
            projection_map_path,
            {
                "run_id": run_id,
                "project_root": project_root_relative,
                "entries": [item.to_dict() for item in projection_entries],
            },
        )
        self._kernel_fs.write_json(
            back_mapping_path,
            build_python_back_mapping_index(
                project_root=self._kernel_fs.resolve_workspace_path(project_root_relative),
                rendered_files=rendered_files,
                projection_entries=projection_entries,
            ),
        )

        verification_report: dict[str, object] = {
            "ok": True,
            "commands": [],
            "summary": "verification skipped",
        }
        if command.run_verification:
            verification_report = self._verification_runner.run(
                self._kernel_fs.resolve_workspace_path(project_root_relative),
                manifest.verification_commands,
            )
        self._kernel_fs.write_json(verification_path, verification_report)

        summary = str(verification_report.get("summary") or "projection completed")
        verification_ok = bool(verification_report.get("ok", False)) if command.run_verification else True
        overall_ok = verification_ok and bool(generated_files)

        self._kernel_fs.append_evidence_record(
            "factory_projection_lab",
            {
                "run_id": run_id,
                "scenario_id": manifest.scenario_id,
                "project_root": project_root_relative,
                "generated_files": generated_files,
                "cell_ids": [item.cell_id for item in target_cells],
                "verification_ok": verification_ok,
                "normalization_source": normalization["source"],
            },
        )
        self._kernel_fs.append_log_line(
            "factory_projection_lab",
            f"run_id={run_id} scenario={manifest.scenario_id} project_root={project_root_relative} verification_ok={verification_ok} normalization_source={normalization['source']}",
        )

        logger.info(
            "Factory projection lab completed: run_id=%s scenario=%s project_root=%s verification_ok=%s",
            run_id,
            manifest.scenario_id,
            project_root_relative,
            verification_ok,
        )

        return ProjectionExperimentResultV1(
            ok=overall_ok,
            workspace=self.workspace,
            experiment_id=run_id,
            scenario_id=manifest.scenario_id,
            project_root=str(self._kernel_fs.resolve_workspace_path(project_root_relative)),
            generated_files=tuple(generated_files),
            artifact_paths=(
                requirement_analysis_path,
                manifest_path,
                cell_ir_path,
                projection_map_path,
                verification_path,
                back_mapping_path,
            ),
            cell_ids=tuple(item.cell_id for item in target_cells),
            verification_ok=verification_ok,
            normalization_source=str(normalization["source"]),
            summary=summary,
        )

    def reproject_experiment(
        self,
        command: ReprojectProjectionExperimentCommandV1,
    ) -> ProjectionReprojectionResultV1:
        if str(Path(command.workspace).resolve()) != self.workspace:
            raise FactoryPipelineError(
                "Command workspace does not match service workspace",
                code="workspace_mismatch",
                details={"command_workspace": command.workspace, "service_workspace": self.workspace},
            )

        artifact_root = f"workspace/factory/projection_lab/{command.experiment_id}"
        manifest_payload = self._read_required_json(f"{artifact_root}/manifest.json")
        projection_map_payload = self._read_required_json(f"{artifact_root}/projection_map.json")

        previous_manifest_payload = manifest_payload.get("manifest")
        if not isinstance(previous_manifest_payload, dict):
            raise FactoryPipelineError(
                "Projection manifest payload is missing nested manifest object",
                code="projection_manifest_invalid",
                details={"artifact_root": artifact_root},
            )

        scenario_id = str(previous_manifest_payload.get("scenario_id") or "").strip()
        scenario = self._get_scenario_definition(scenario_id)
        project_slug = str(previous_manifest_payload.get("project_slug") or "").strip()
        synthetic_run_command = RunProjectionExperimentCommandV1(
            workspace=self.workspace,
            scenario_id=scenario_id,
            requirement=command.requirement,
            project_slug=project_slug,
            use_pm_llm=command.use_pm_llm,
            run_verification=command.run_verification,
            overwrite=True,
        )
        normalization = self._normalize_requirement(synthetic_run_command, scenario)
        manifest = scenario.manifest_builder(synthetic_run_command, normalization)
        target_cells = scenario.target_cell_builder(manifest)
        projection_entries = scenario.projection_entry_builder(manifest)
        rendered_files = self._resolve_renderer(manifest.project_style, scenario.scenario_id).render(manifest)
        impacted_cell_ids = self._resolve_reprojection_impacts(
            scenario_id=scenario.scenario_id,
            previous_manifest=previous_manifest_payload,
            current_manifest=manifest.to_dict(),
            current_target_cells=target_cells,
        )

        project_root_relative = self._extract_project_root(previous_manifest_payload, projection_map_payload)
        rewritten_files: list[str] = []
        for entry in projection_entries:
            if not set(entry.cell_ids).intersection(impacted_cell_ids):
                continue
            content = rendered_files.get(entry.path)
            if content is None:
                continue
            destination = f"{project_root_relative}/{entry.path}".replace("//", "/")
            self._kernel_fs.workspace_write_text(destination, content, encoding="utf-8")
            rewritten_files.append(destination)

        self._kernel_fs.write_json(
            f"{artifact_root}/manifest.json",
            {
                "normalization": normalization,
                "manifest": manifest.to_dict(),
            },
        )
        self._kernel_fs.write_json(
            f"{artifact_root}/cell_ir.json",
            {
                "run_id": command.experiment_id,
                "scenario_id": manifest.scenario_id,
                "project_root": project_root_relative,
                "wave_particle_model": {
                    "wave_form": "semantic_halo",
                    "particle_form": "contract_nucleus",
                    "projection": "traditional_python_project",
                },
                "normalization": normalization,
                "manifest": manifest.to_dict(),
                "target_cells": [item.to_dict() for item in target_cells],
            },
        )
        self._kernel_fs.write_json(
            f"{artifact_root}/projection_map.json",
            {
                "run_id": command.experiment_id,
                "project_root": project_root_relative,
                "entries": [item.to_dict() for item in projection_entries],
            },
        )
        self._kernel_fs.write_json(
            f"{artifact_root}/reprojection_plan.json",
            {
                "experiment_id": command.experiment_id,
                "scenario_id": scenario.scenario_id,
                "project_root": project_root_relative,
                "impacted_cell_ids": sorted(impacted_cell_ids),
                "rewritten_files": rewritten_files,
            },
        )

        verification_report: dict[str, object] = {
            "ok": True,
            "commands": [],
            "summary": "verification skipped",
        }
        if command.run_verification:
            verification_report = self._verification_runner.run(
                self._kernel_fs.resolve_workspace_path(project_root_relative),
                manifest.verification_commands,
            )
        self._kernel_fs.write_json(f"{artifact_root}/verification_report.json", verification_report)

        ProjectionChangeAnalysisService(
            self.workspace,
            kernel_fs=self._kernel_fs,
        ).refresh_back_mapping(command.experiment_id)

        self._kernel_fs.append_evidence_record(
            "factory_projection_lab",
            {
                "event": "projection_reprojected",
                "experiment_id": command.experiment_id,
                "scenario_id": scenario.scenario_id,
                "project_root": project_root_relative,
                "impacted_cell_ids": sorted(impacted_cell_ids),
                "rewritten_files": rewritten_files,
                "verification_ok": bool(verification_report.get("ok", False)) if command.run_verification else True,
            },
        )
        self._kernel_fs.append_log_line(
            "factory_projection_lab",
            (
                f"event=projection_reprojected experiment_id={command.experiment_id} "
                f"scenario={scenario.scenario_id} rewritten_files={len(rewritten_files)} "
                f"impacted_cell_ids={','.join(sorted(impacted_cell_ids)) or '-'}"
            ),
        )

        verification_ok = bool(verification_report.get("ok", False)) if command.run_verification else True
        summary = str(verification_report.get("summary") or "reprojection completed")
        return ProjectionReprojectionResultV1(
            ok=verification_ok,
            workspace=self.workspace,
            experiment_id=command.experiment_id,
            scenario_id=scenario.scenario_id,
            project_root=str(self._kernel_fs.resolve_workspace_path(project_root_relative)),
            impacted_cell_ids=tuple(sorted(impacted_cell_ids)),
            rewritten_files=tuple(rewritten_files),
            artifact_paths=(
                f"{artifact_root}/manifest.json",
                f"{artifact_root}/cell_ir.json",
                f"{artifact_root}/projection_map.json",
                f"{artifact_root}/reprojection_plan.json",
                f"{artifact_root}/verification_report.json",
                f"{artifact_root}/back_mapping_index.json",
                f"{artifact_root}/back_mapping_refresh_report.json",
            ),
            verification_ok=verification_ok,
            normalization_source=str(normalization["source"]),
            summary=summary,
        )

    def _build_scenarios(self) -> dict[str, ProjectionScenarioDefinition]:
        return {
            "record_cli_app": ProjectionScenarioDefinition(
                scenario_id="record_cli_app",
                manifest_builder=self._build_record_cli_manifest,
                target_cell_builder=self._build_record_cli_target_cells,
                projection_entry_builder=self._build_record_cli_projection_entries,
                pm_prompt_builder=self._build_record_cli_pm_requirement_prompt,
            ),
            "resource_http_service": ProjectionScenarioDefinition(
                scenario_id="resource_http_service",
                manifest_builder=self._build_resource_http_service_manifest,
                target_cell_builder=self._build_resource_http_service_target_cells,
                projection_entry_builder=self._build_resource_http_service_projection_entries,
                pm_prompt_builder=self._build_resource_http_service_pm_requirement_prompt,
            ),
        }

    def _get_scenario_definition(self, scenario_id: str) -> ProjectionScenarioDefinition:
        normalized_scenario_id = str(scenario_id or "").strip().lower()
        scenario = self._scenarios.get(normalized_scenario_id)
        if scenario is None:
            raise FactoryPipelineError(
                f"Unsupported projection experiment scenario: {scenario_id}",
                code="unsupported_scenario",
                details={
                    "scenario_id": scenario_id,
                    "supported_scenarios": sorted(self._scenarios),
                },
            )
        return scenario

    def _resolve_renderer(self, project_style: str, scenario_id: str) -> ProjectRenderer:
        renderer = self._renderers.get(project_style)
        if renderer is None:
            raise FactoryPipelineError(
                f"Unsupported project style: {project_style}",
                code="unsupported_project_style",
                details={"project_style": project_style, "scenario_id": scenario_id},
            )
        return renderer

    def _read_required_json(self, logical_path: str) -> dict[str, Any]:
        if not self._kernel_fs.exists(logical_path):
            raise FactoryPipelineError(
                f"Required projection artifact not found: {logical_path}",
                code="projection_artifact_missing",
                details={"logical_path": logical_path},
            )
        payload = self._kernel_fs.read_json(logical_path)
        if not isinstance(payload, dict):
            raise FactoryPipelineError(
                f"Projection artifact is not a JSON object: {logical_path}",
                code="projection_artifact_invalid",
                details={"logical_path": logical_path},
            )
        return payload

    def _extract_project_root(
        self,
        manifest_payload: dict[str, Any],
        projection_map_payload: dict[str, Any],
    ) -> str:
        projection_project_root = str(projection_map_payload.get("project_root") or "").strip()
        if projection_project_root:
            return projection_project_root
        project_root = str(manifest_payload.get("project_root") or "").strip()
        if project_root:
            return project_root
        raise FactoryPipelineError(
            "Projection artifact does not contain project_root",
            code="projection_project_root_missing",
        )

    def _resolve_reprojection_impacts(
        self,
        *,
        scenario_id: str,
        previous_manifest: dict[str, Any],
        current_manifest: dict[str, Any],
        current_target_cells: tuple[TargetCellSpec, ...],
    ) -> set[str]:
        impacted: set[str] = set()
        previous_settings_raw = previous_manifest.get("settings")
        previous_settings: dict[str, Any] = previous_settings_raw if isinstance(previous_settings_raw, dict) else {}
        current_settings_raw = current_manifest.get("settings")
        current_settings: dict[str, Any] = current_settings_raw if isinstance(current_settings_raw, dict) else {}

        if previous_manifest.get("project_title") != current_manifest.get("project_title") or previous_manifest.get(
            "summary"
        ) != current_manifest.get("summary"):
            impacted.add("target.delivery.cli")
        if previous_manifest.get("commands") != current_manifest.get("commands"):
            impacted.add("target.delivery.cli")
            impacted.add(
                "target.tests.resource_http" if scenario_id == "resource_http_service" else "target.tests.record_cli"
            )
        if previous_manifest.get("entity") != current_manifest.get("entity"):
            if scenario_id == "resource_http_service":
                impacted.update(
                    {
                        "target.resource.catalog",
                        "target.resource.blob_store",
                        "target.delivery.http",
                        "target.tests.resource_http",
                    }
                )
            else:
                impacted.update(
                    {
                        "target.records.catalog",
                        "target.records.storage",
                        "target.delivery.cli",
                        "target.tests.record_cli",
                    }
                )

        changed_setting_keys = {
            key
            for key in set(previous_settings) | set(current_settings)
            if previous_settings.get(key) != current_settings.get(key)
        }
        for key in changed_setting_keys:
            if scenario_id == "resource_http_service":
                if key in {"metadata_path", "blob_root"}:
                    impacted.update(
                        {"target.resource.blob_store", "target.delivery.http", "target.tests.resource_http"}
                    )
                elif key in {"max_payload_bytes", "enable_checksum"}:
                    impacted.update({"target.resource.catalog", "target.delivery.http", "target.tests.resource_http"})
                elif key in {"host", "port"}:
                    impacted.update({"target.delivery.http", "target.delivery.cli", "target.tests.resource_http"})
                else:
                    impacted.update({item.cell_id for item in current_target_cells})
            elif key == "storage_path":
                impacted.update({"target.records.storage", "target.delivery.cli", "target.tests.record_cli"})
            else:
                impacted.update({item.cell_id for item in current_target_cells})

        if previous_manifest.get("requirement") != current_manifest.get("requirement") and not impacted:
            impacted.add("target.delivery.cli")
        if (
            not impacted
            and previous_manifest is not None
            and current_manifest is not None
            and previous_manifest != current_manifest
        ):
            impacted.update({item.cell_id for item in current_target_cells})
        if not impacted:
            impacted.update({item.cell_id for item in current_target_cells})
        return impacted

    def _build_record_cli_manifest(
        self,
        command: RunProjectionExperimentCommandV1,
        normalization: dict[str, Any],
    ) -> TargetProjectManifest:
        project_slug = self._normalize_project_slug(command.project_slug, default_value="projection_lab")
        package_name = f"{project_slug}_app"
        entity = EntitySpec(
            singular="record",
            plural="records",
            class_name="RecordEntry",
            archive_field="archived",
            fields=(
                FieldSpec(name="title", kind="str", description="Primary record title", searchable=True),
                FieldSpec(name="content", kind="str", description="Primary record content", searchable=True),
                FieldSpec(name="tags", kind="tags", description="Search tags", required=False, searchable=True),
                FieldSpec(name="archived", kind="bool", description="Archive flag", required=False, searchable=False),
            ),
        )
        return TargetProjectManifest(
            scenario_id="record_cli_app",
            requirement=command.requirement,
            project_slug=project_slug,
            project_title=str(normalization.get("project_title") or "Record CLI Experiment"),
            package_name=package_name,
            summary=str(
                normalization.get("summary")
                or "A local-first CLI record manager generated through the Cell IR projection experiment pipeline."
            ),
            entity=entity,
            commands=(
                CommandSpec(name="add", description="Create a record with title, content, and optional tags."),
                CommandSpec(name="list", description="List active records from local JSON storage."),
                CommandSpec(name="search", description="Search records by title, content, or tags."),
                CommandSpec(name="archive", description="Archive an existing record without deleting history."),
            ),
            project_style="json_cli_app",
            settings={
                "storage_path": f"data/{entity.plural}.json",
            },
            verification_commands=(
                (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"),
                (
                    sys.executable,
                    "-m",
                    f"{package_name}.delivery.cli",
                    "list",
                    "--storage",
                    f"data/{entity.plural}.json",
                ),
            ),
        )

    def _build_record_cli_target_cells(self, manifest: TargetProjectManifest) -> tuple[TargetCellSpec, ...]:
        package_name = manifest.package_name
        return (
            TargetCellSpec(
                cell_id="target.records.catalog",
                purpose="Own record domain rules, application service, and search behavior.",
                depends_on=("target.records.storage",),
                state_owners=("workspace/experiments/*/data/records.json",),
                effects_allowed=("fs.read:workspace/experiments/*", "fs.write:workspace/experiments/*"),
                projection_targets=(
                    f"{package_name}/domain/models.py",
                    f"{package_name}/application/service.py",
                ),
                verification_targets=("tests/test_app.py",),
            ),
            TargetCellSpec(
                cell_id="target.records.storage",
                purpose="Persist record rows in a UTF-8 JSON store.",
                depends_on=(),
                state_owners=("workspace/experiments/*/data/records.json",),
                effects_allowed=("fs.read:workspace/experiments/*", "fs.write:workspace/experiments/*"),
                projection_targets=(f"{package_name}/infrastructure/json_store.py",),
                verification_targets=("tests/test_app.py",),
            ),
            TargetCellSpec(
                cell_id="target.delivery.cli",
                purpose="Expose a traditional argparse CLI for the generated record project.",
                depends_on=("target.records.catalog",),
                state_owners=(),
                effects_allowed=("process.spawn:workspace/experiments/*",),
                projection_targets=(f"{package_name}/delivery/cli.py", f"{package_name}/__main__.py"),
                verification_targets=("tests/test_app.py",),
            ),
            TargetCellSpec(
                cell_id="target.tests.record_cli",
                purpose="Verify record behavior and persistence through a traditional unittest suite.",
                depends_on=("target.records.catalog", "target.records.storage", "target.delivery.cli"),
                state_owners=(),
                effects_allowed=("process.spawn:workspace/experiments/*",),
                projection_targets=("tests/test_app.py",),
                verification_targets=("tests/test_app.py",),
            ),
        )

    def _build_record_cli_projection_entries(self, manifest: TargetProjectManifest) -> tuple[ProjectionEntry, ...]:
        package_name = manifest.package_name
        return (
            ProjectionEntry(
                path="tui_runtime.md",
                cell_ids=("target.delivery.cli",),
                description="User-facing project overview and usage guide.",
            ),
            ProjectionEntry(
                path="pyproject.toml",
                cell_ids=("target.delivery.cli",),
                description="Traditional Python project metadata.",
            ),
            ProjectionEntry(
                path=f"{package_name}/domain/models.py",
                cell_ids=("target.records.catalog",),
                description="Primary record domain entity definitions.",
            ),
            ProjectionEntry(
                path=f"{package_name}/application/service.py",
                cell_ids=("target.records.catalog",),
                description="Application service for record lifecycle and search.",
            ),
            ProjectionEntry(
                path=f"{package_name}/infrastructure/json_store.py",
                cell_ids=("target.records.storage",),
                description="UTF-8 JSON persistence adapter.",
            ),
            ProjectionEntry(
                path=f"{package_name}/delivery/cli.py",
                cell_ids=("target.delivery.cli",),
                description="Argparse command-line interface.",
            ),
            ProjectionEntry(
                path=f"{package_name}/__main__.py",
                cell_ids=("target.delivery.cli",),
                description="Python module entrypoint.",
            ),
            ProjectionEntry(
                path="tests/test_app.py",
                cell_ids=("target.tests.record_cli", "target.records.catalog", "target.records.storage"),
                description="Traditional unittest verification suite.",
            ),
        )

    def _build_resource_http_service_manifest(
        self,
        command: RunProjectionExperimentCommandV1,
        normalization: dict[str, Any],
    ) -> TargetProjectManifest:
        project_slug = self._normalize_project_slug(command.project_slug, default_value="resource_http_lab")
        package_name = f"{project_slug}_app"
        entity = EntitySpec(
            singular="resource",
            plural="resources",
            class_name="ResourceAsset",
            archive_field="deleted",
            fields=(
                FieldSpec(name="filename", kind="str", description="Stable resource label", searchable=True),
                FieldSpec(name="content_type", kind="str", description="Resource MIME type", searchable=True),
                FieldSpec(name="size_bytes", kind="int", description="Stored payload size in bytes", searchable=False),
                FieldSpec(
                    name="checksum",
                    kind="str",
                    description="Checksum for integrity verification",
                    required=False,
                    searchable=True,
                ),
                FieldSpec(name="tags", kind="tags", description="Resource tags", required=False, searchable=True),
                FieldSpec(
                    name="deleted", kind="bool", description="Soft delete flag", required=False, searchable=False
                ),
            ),
        )
        host = self._normalize_host_setting(normalization.get("host"), default_value="127.0.0.1")
        port = self._normalize_int_setting(normalization.get("port"), default_value=8765, minimum=0, maximum=65535)
        max_payload_mb = self._normalize_int_setting(normalization.get("max_payload_mb"), default_value=10, minimum=1)
        enable_checksum = self._normalize_bool_setting(normalization.get("enable_checksum"), default_value=True)
        return TargetProjectManifest(
            scenario_id="resource_http_service",
            requirement=command.requirement,
            project_slug=project_slug,
            project_title=str(normalization.get("project_title") or "Resource HTTP Service Experiment"),
            package_name=package_name,
            summary=str(
                normalization.get("summary")
                or "A resource-oriented HTTP service generated through the Cell IR projection pipeline."
            ),
            entity=entity,
            commands=(
                CommandSpec(name="serve", description="Run the HTTP resource service."),
                CommandSpec(name="list", description="List indexed resources via the traditional CLI."),
                CommandSpec(
                    name="upload", description="Accept HTTP payload uploads and persist metadata plus binary blobs."
                ),
                CommandSpec(name="download", description="Serve stored payloads by resource identifier."),
                CommandSpec(
                    name="delete", description="Soft-delete an existing resource while preserving audit history."
                ),
            ),
            project_style="resource_http_service",
            settings={
                "metadata_path": "data/resource_index.json",
                "blob_root": "storage/blobs",
                "host": host,
                "port": port,
                "max_payload_bytes": max_payload_mb * 1024 * 1024,
                "enable_checksum": enable_checksum,
            },
            verification_commands=(
                (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"),
                (sys.executable, "-m", f"{package_name}.delivery.cli", "list"),
            ),
        )

    def _build_resource_http_service_target_cells(self, manifest: TargetProjectManifest) -> tuple[TargetCellSpec, ...]:
        package_name = manifest.package_name
        return (
            TargetCellSpec(
                cell_id="target.resource.catalog",
                purpose="Own resource metadata domain rules and service orchestration.",
                depends_on=("target.resource.blob_store",),
                state_owners=("workspace/experiments/*/data/resource_index.json",),
                effects_allowed=("fs.read:workspace/experiments/*", "fs.write:workspace/experiments/*"),
                projection_targets=(
                    f"{package_name}/domain/models.py",
                    f"{package_name}/application/service.py",
                ),
                verification_targets=("tests/test_service.py", "tests/test_http_api.py"),
            ),
            TargetCellSpec(
                cell_id="target.resource.blob_store",
                purpose="Persist UTF-8 resource metadata and binary payload blobs.",
                depends_on=(),
                state_owners=(
                    "workspace/experiments/*/data/resource_index.json",
                    "workspace/experiments/*/storage/blobs/*",
                ),
                effects_allowed=("fs.read:workspace/experiments/*", "fs.write:workspace/experiments/*"),
                projection_targets=(
                    f"{package_name}/infrastructure/index_store.py",
                    f"{package_name}/infrastructure/blob_store.py",
                ),
                verification_targets=("tests/test_service.py", "tests/test_http_api.py"),
            ),
            TargetCellSpec(
                cell_id="target.delivery.http",
                purpose="Expose a traditional HTTP transport and runtime composition layer.",
                depends_on=("target.resource.catalog", "target.resource.blob_store"),
                state_owners=(),
                effects_allowed=("process.spawn:workspace/experiments/*",),
                projection_targets=(
                    f"{package_name}/application/config.py",
                    f"{package_name}/delivery/http_api.py",
                ),
                verification_targets=("tests/test_http_api.py",),
            ),
            TargetCellSpec(
                cell_id="target.delivery.cli",
                purpose="Expose a traditional CLI entrypoint for operating the generated service.",
                depends_on=("target.delivery.http",),
                state_owners=(),
                effects_allowed=("process.spawn:workspace/experiments/*",),
                projection_targets=(
                    "tui_runtime.md",
                    "pyproject.toml",
                    ".env.example",
                    f"{package_name}/__init__.py",
                    f"{package_name}/__main__.py",
                    f"{package_name}/delivery/cli.py",
                ),
                verification_targets=("tests/test_service.py",),
            ),
            TargetCellSpec(
                cell_id="target.tests.resource_http",
                purpose="Verify resource metadata, blob persistence, and HTTP transport behavior.",
                depends_on=(
                    "target.resource.catalog",
                    "target.resource.blob_store",
                    "target.delivery.http",
                    "target.delivery.cli",
                ),
                state_owners=(),
                effects_allowed=("process.spawn:workspace/experiments/*",),
                projection_targets=("tests/test_service.py", "tests/test_http_api.py"),
                verification_targets=("tests/test_service.py", "tests/test_http_api.py"),
            ),
        )

    def _build_resource_http_service_projection_entries(
        self, manifest: TargetProjectManifest
    ) -> tuple[ProjectionEntry, ...]:
        package_name = manifest.package_name
        return (
            ProjectionEntry(
                path="tui_runtime.md",
                cell_ids=("target.delivery.cli",),
                description="User-facing project overview and HTTP capability guide.",
            ),
            ProjectionEntry(
                path="pyproject.toml",
                cell_ids=("target.delivery.cli",),
                description="Traditional Python project metadata.",
            ),
            ProjectionEntry(
                path=".env.example",
                cell_ids=("target.delivery.cli",),
                description="Example runtime configuration values.",
            ),
            ProjectionEntry(
                path=f"{package_name}/__init__.py", cell_ids=("target.delivery.cli",), description="Package exports."
            ),
            ProjectionEntry(
                path=f"{package_name}/__main__.py", cell_ids=("target.delivery.cli",), description="Module entrypoint."
            ),
            ProjectionEntry(
                path=f"{package_name}/domain/models.py",
                cell_ids=("target.resource.catalog",),
                description="Resource metadata entity definitions.",
            ),
            ProjectionEntry(
                path=f"{package_name}/application/config.py",
                cell_ids=("target.delivery.http",),
                description="Application configuration composition.",
            ),
            ProjectionEntry(
                path=f"{package_name}/application/service.py",
                cell_ids=("target.resource.catalog",),
                description="Resource application service.",
            ),
            ProjectionEntry(
                path=f"{package_name}/infrastructure/blob_store.py",
                cell_ids=("target.resource.blob_store",),
                description="Binary payload persistence adapter.",
            ),
            ProjectionEntry(
                path=f"{package_name}/infrastructure/index_store.py",
                cell_ids=("target.resource.blob_store",),
                description="UTF-8 JSON metadata store.",
            ),
            ProjectionEntry(
                path=f"{package_name}/delivery/http_api.py",
                cell_ids=("target.delivery.http",),
                description="HTTP transport layer.",
            ),
            ProjectionEntry(
                path=f"{package_name}/delivery/cli.py",
                cell_ids=("target.delivery.cli",),
                description="Traditional CLI entrypoint.",
            ),
            ProjectionEntry(
                path="tests/test_service.py",
                cell_ids=("target.tests.resource_http", "target.resource.catalog", "target.resource.blob_store"),
                description="Service-level verification suite.",
            ),
            ProjectionEntry(
                path="tests/test_http_api.py",
                cell_ids=("target.tests.resource_http", "target.delivery.http"),
                description="HTTP integration verification suite.",
            ),
        )

    def _normalize_project_slug(self, raw_value: str, *, default_value: str) -> str:
        normalized = str(raw_value or "").strip().lower()
        normalized = normalized.replace("-", "_")
        normalized = _PROJECT_SLUG_PATTERN.sub("_", normalized)
        normalized = normalized.strip("_")
        if not normalized:
            return default_value
        return normalized

    def _normalize_requirement(
        self,
        command: RunProjectionExperimentCommandV1,
        scenario: ProjectionScenarioDefinition,
    ) -> dict[str, Any]:
        fallback = self._build_requirement_fallback(scenario.scenario_id)
        if not command.use_pm_llm:
            return fallback

        try:
            from polaris.cells.llm.provider_runtime.public.service import invoke_role_runtime_provider
        except (RuntimeError, ValueError) as exc:
            logger.debug("PM runtime provider import unavailable for projection lab: %s", exc)
            return fallback

        prompt = scenario.pm_prompt_builder(command.requirement)
        try:
            provider_result = invoke_role_runtime_provider(
                role="pm",
                workspace=self.workspace,
                prompt=prompt,
                fallback_model="default",
                timeout=45,
                blocked_provider_types={""},
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("PM runtime provider invocation failed: %s", exc)
            return fallback

        output = str(provider_result.output or "").strip()
        if not provider_result.attempted or not output:
            return fallback

        parsed = self._parse_json_object(output)
        if not isinstance(parsed, dict):
            enriched = dict(fallback)
            enriched["source"] = "pm_llm_unparseable_fallback"
            enriched["raw_output"] = output[:4000]
            return enriched

        capability_focus = parsed.get("capability_focus")
        normalized_focus = (
            [str(item).strip() for item in capability_focus if str(item).strip()]
            if isinstance(capability_focus, list)
            else list(fallback["capability_focus"])
        )
        normalized_payload = dict(parsed)
        normalized_payload.update(
            {
                "source": "pm_llm",
                "project_title": str(parsed.get("project_title") or fallback["project_title"]).strip()
                or fallback["project_title"],
                "summary": str(parsed.get("summary") or fallback["summary"]).strip() or fallback["summary"],
                "capability_focus": normalized_focus,
                "raw_output": output[:4000],
            }
        )
        return normalized_payload

    def _build_requirement_fallback(self, scenario_id: str) -> dict[str, Any]:
        normalized_scenario_id = str(scenario_id or "").strip().lower()
        if normalized_scenario_id == "resource_http_service":
            return {
                "source": "deterministic_fallback",
                "project_title": "Resource HTTP Service Experiment",
                "summary": "A resource-oriented HTTP service generated through the Cell IR projection experiment pipeline.",
                "capability_focus": [
                    "upload payloads",
                    "list resources",
                    "download resources",
                    "delete resources",
                    "local blob persistence",
                    "traditional unittest verification",
                ],
                "host": "127.0.0.1",
                "port": 8765,
                "max_payload_mb": 10,
                "enable_checksum": True,
                "raw_output": "",
            }
        return {
            "source": "deterministic_fallback",
            "project_title": "Record CLI Experiment",
            "summary": "A local-first CLI record manager generated through the Cell IR projection experiment pipeline.",
            "capability_focus": [
                "create records",
                "list records",
                "search records",
                "archive records",
                "local JSON persistence",
                "traditional unittest verification",
            ],
            "raw_output": "",
        }

    def _build_record_cli_pm_requirement_prompt(self, requirement: str) -> str:
        return (
            "你是 Polaris 的 PM 需求归一化器。"
            "当前场景是一个受控的 record_cli_app 投影实验，目标是把自然语言需求规范化为一个"
            "传统 Python CLI 记录管理项目。"
            "请只输出 JSON，不要输出 Markdown。"
            "JSON 必须包含 project_title、summary、capability_focus 三个字段。"
            "capability_focus 必须是字符串数组。"
            "需求如下：\n"
            f"{requirement.strip()}\n"
        )

    def _build_resource_http_service_pm_requirement_prompt(self, requirement: str) -> str:
        return (
            "你是 Polaris 的 PM 需求归一化器。"
            "当前场景是一个受控的 resource_http_service 投影实验，目标是把自然语言需求规范化为一个"
            "传统 Python HTTP 资源服务项目。"
            "请只输出 JSON，不要输出 Markdown。"
            "JSON 必须包含 project_title、summary、capability_focus 三个字段。"
            "可以额外包含 host（字符串）、port（整数）、max_payload_mb（整数）和 enable_checksum（布尔值）。"
            "capability_focus 必须是字符串数组。"
            "需求如下：\n"
            f"{requirement.strip()}\n"
        )

    def _normalize_int_setting(
        self,
        raw_value: Any,
        *,
        default_value: int,
        minimum: int = 0,
        maximum: int | None = None,
    ) -> int:
        try:
            normalized = int(raw_value)
        except (TypeError, ValueError):
            return max(default_value, minimum)
        bounded = max(normalized, minimum)
        if maximum is not None:
            bounded = min(bounded, maximum)
        return bounded

    def _normalize_host_setting(
        self,
        raw_value: Any,
        *,
        default_value: str,
    ) -> str:
        normalized = str(raw_value or "").strip()
        return normalized or str(default_value).strip() or "127.0.0.1"

    def _normalize_bool_setting(
        self,
        raw_value: Any,
        *,
        default_value: bool,
    ) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if raw_value is None:
            return default_value
        text = str(raw_value).strip().lower()
        if not text:
            return default_value
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default_value

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        payload = str(text or "").strip()
        if not payload:
            return None
        try:
            loaded = json.loads(payload)
            return loaded if isinstance(loaded, dict) else None
        except (RuntimeError, ValueError):
            logger.debug("projection_lab.py: JSON parse failed for payload, trying extraction")
        start = payload.find("{")
        end = payload.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            loaded = json.loads(payload[start : end + 1])
            return loaded if isinstance(loaded, dict) else None
        except (RuntimeError, ValueError):
            logger.debug("projection_lab.py: JSON extraction parse failed for payload")
            return None


__all__ = ["FactoryProjectionLabService", "ProjectVerificationRunner", "SubprocessProjectVerificationRunner"]
