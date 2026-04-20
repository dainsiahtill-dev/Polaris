"""Refresh and analyze projection back-mapping after workspace code changes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from polaris.cells.factory.pipeline.public.contracts import (
    FactoryPipelineError,
    ProjectionBackMappingRefreshResultV1,
    RefreshProjectionBackMappingCommandV1,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

from .back_mapping import build_python_back_mapping_index_from_workspace
from .models import ProjectionEntry

logger = logging.getLogger(__name__)


class ProjectionChangeAnalysisService:
    """Refresh back-mapping artifacts and compute impacted target cells."""

    def __init__(
        self,
        workspace: str,
        *,
        kernel_fs: KernelFileSystem | None = None,
    ) -> None:
        workspace_text = str(workspace or "").strip()
        if not workspace_text:
            raise ValueError("workspace must be a non-empty string")
        self.workspace = str(Path(workspace_text).resolve())
        self._kernel_fs = kernel_fs or KernelFileSystem(self.workspace, get_default_adapter())

    def refresh_back_mapping(self, experiment_id: str) -> dict[str, Any]:
        """Refresh the current back-mapping index and compute change impact."""

        normalized_experiment_id = str(experiment_id or "").strip()
        if not normalized_experiment_id:
            raise ValueError("experiment_id must be a non-empty string")

        artifact_root = f"workspace/factory/projection_lab/{normalized_experiment_id}"
        manifest_payload = self._read_required_json(f"{artifact_root}/manifest.json")
        projection_map_payload = self._read_required_json(f"{artifact_root}/projection_map.json")
        previous_index = self._read_optional_json(f"{artifact_root}/back_mapping_index.json")

        project_root = self._extract_project_root(manifest_payload, projection_map_payload)
        projection_entries = self._parse_projection_entries(projection_map_payload)
        refreshed_index = build_python_back_mapping_index_from_workspace(
            kernel_fs=self._kernel_fs,
            project_root=project_root,
            projection_entries=projection_entries,
        )
        diff_report = self._diff_indexes(previous_index, refreshed_index)

        refresh_report = {
            "experiment_id": normalized_experiment_id,
            "project_root": project_root,
            "changed_files": diff_report["changed_files"],
            "added_symbols": diff_report["added_symbols"],
            "removed_symbols": diff_report["removed_symbols"],
            "modified_symbols": diff_report["modified_symbols"],
            "impacted_cell_ids": diff_report["impacted_cell_ids"],
            "mapping_strategy": refreshed_index.get("mapping_strategy", ""),
            "previous_mapping_strategy": str(previous_index.get("mapping_strategy", "")) if previous_index else "",
        }

        self._kernel_fs.write_json(f"{artifact_root}/back_mapping_index.json", refreshed_index)
        self._kernel_fs.write_json(f"{artifact_root}/back_mapping_refresh_report.json", refresh_report)
        self._kernel_fs.append_evidence_record(
            "factory_projection_lab",
            {
                "event": "back_mapping_refreshed",
                "experiment_id": normalized_experiment_id,
                "project_root": project_root,
                "changed_files": refresh_report["changed_files"],
                "impacted_cell_ids": refresh_report["impacted_cell_ids"],
            },
        )
        self._kernel_fs.append_log_line(
            "factory_projection_lab",
            (
                f"event=back_mapping_refreshed experiment_id={normalized_experiment_id} "
                f"changed_files={len(refresh_report['changed_files'])} "
                f"impacted_cell_ids={','.join(refresh_report['impacted_cell_ids']) or '-'}"
            ),
        )
        logger.info(
            "Refreshed projection back-mapping: experiment_id=%s changed_files=%s impacted_cells=%s",
            normalized_experiment_id,
            len(refresh_report["changed_files"]),
            len(refresh_report["impacted_cell_ids"]),
        )
        return refresh_report

    def refresh_back_mapping_result(
        self,
        command: RefreshProjectionBackMappingCommandV1,
    ) -> ProjectionBackMappingRefreshResultV1:
        report = self.refresh_back_mapping(command.experiment_id)
        return ProjectionBackMappingRefreshResultV1(
            workspace=self.workspace,
            experiment_id=str(report["experiment_id"]),
            project_root=str(report["project_root"]),
            changed_files=tuple(report["changed_files"]),
            added_symbols=tuple(report["added_symbols"]),
            removed_symbols=tuple(report["removed_symbols"]),
            modified_symbols=tuple(report["modified_symbols"]),
            impacted_cell_ids=tuple(report["impacted_cell_ids"]),
            mapping_strategy=str(report["mapping_strategy"]),
            previous_mapping_strategy=str(report["previous_mapping_strategy"]),
        )

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

    def _read_optional_json(self, logical_path: str) -> dict[str, Any] | None:
        if not self._kernel_fs.exists(logical_path):
            return None
        payload = self._kernel_fs.read_json(logical_path)
        return payload if isinstance(payload, dict) else None

    def _extract_project_root(
        self,
        manifest_payload: dict[str, Any],
        projection_map_payload: dict[str, Any],
    ) -> str:
        projection_project_root = str(projection_map_payload.get("project_root") or "").strip()
        if projection_project_root:
            return projection_project_root
        manifest = manifest_payload.get("manifest")
        if isinstance(manifest, dict):
            project_root = str(manifest.get("project_root") or "").strip()
            if project_root:
                return project_root
        raise FactoryPipelineError(
            "Projection artifact does not contain project_root",
            code="projection_project_root_missing",
        )

    def _parse_projection_entries(self, projection_map_payload: dict[str, Any]) -> tuple[ProjectionEntry, ...]:
        raw_entries = projection_map_payload.get("entries")
        if not isinstance(raw_entries, list):
            raise FactoryPipelineError(
                "Projection map does not contain entry list",
                code="projection_entries_missing",
            )
        entries: list[ProjectionEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            entries.append(
                ProjectionEntry(
                    path=str(item.get("path") or ""),
                    cell_ids=tuple(item.get("cell_ids") or ()),
                    description=str(item.get("description") or ""),
                )
            )
        if not entries:
            raise FactoryPipelineError(
                "Projection map entry list is empty",
                code="projection_entries_empty",
            )
        return tuple(entries)

    def _diff_indexes(
        self,
        previous_index: dict[str, Any] | None,
        current_index: dict[str, Any],
    ) -> dict[str, Any]:
        previous_files = self._index_files(previous_index)
        current_files = self._index_files(current_index)

        changed_files: list[dict[str, Any]] = []
        added_symbols: list[dict[str, Any]] = []
        removed_symbols: list[dict[str, Any]] = []
        modified_symbols: list[dict[str, Any]] = []
        impacted_cell_ids: set[str] = set()

        for path in sorted(set(previous_files) | set(current_files)):
            previous_file = previous_files.get(path)
            current_file = current_files.get(path)
            if previous_file is None or current_file is None:
                impacted_cell_ids.update(self._file_cell_ids(previous_file))
                impacted_cell_ids.update(self._file_cell_ids(current_file))
                changed_files.append(
                    {
                        "path": path,
                        "change_type": "file_added" if current_file is not None else "file_removed",
                        "cell_ids": sorted(self._file_cell_ids(previous_file) | self._file_cell_ids(current_file)),
                    }
                )
                continue

            previous_sha = str(previous_file.get("sha256") or "")
            current_sha = str(current_file.get("sha256") or "")
            previous_symbol_map = self._index_symbols(previous_file)
            current_symbol_map = self._index_symbols(current_file)

            file_changed = previous_sha != current_sha or previous_symbol_map != current_symbol_map
            if file_changed:
                impacted_cell_ids.update(self._file_cell_ids(previous_file))
                impacted_cell_ids.update(self._file_cell_ids(current_file))
                changed_files.append(
                    {
                        "path": path,
                        "change_type": "file_modified",
                        "previous_sha256": previous_sha,
                        "current_sha256": current_sha,
                        "cell_ids": sorted(self._file_cell_ids(previous_file) | self._file_cell_ids(current_file)),
                    }
                )

            previous_keys = set(previous_symbol_map)
            current_keys = set(current_symbol_map)
            for qualified_name in sorted(current_keys - previous_keys):
                symbol = current_symbol_map[qualified_name]
                impacted_cell_ids.update(self._symbol_cell_ids(symbol))
                added_symbols.append(self._symbol_report(path, symbol))
            for qualified_name in sorted(previous_keys - current_keys):
                symbol = previous_symbol_map[qualified_name]
                impacted_cell_ids.update(self._symbol_cell_ids(symbol))
                removed_symbols.append(self._symbol_report(path, symbol))
            for qualified_name in sorted(previous_keys & current_keys):
                previous_symbol = previous_symbol_map[qualified_name]
                current_symbol = current_symbol_map[qualified_name]
                if previous_symbol != current_symbol:
                    impacted_cell_ids.update(self._symbol_cell_ids(previous_symbol))
                    impacted_cell_ids.update(self._symbol_cell_ids(current_symbol))
                    modified_symbols.append(
                        {
                            "path": path,
                            "qualified_name": qualified_name,
                            "previous": previous_symbol,
                            "current": current_symbol,
                        }
                    )

        return {
            "changed_files": changed_files,
            "added_symbols": added_symbols,
            "removed_symbols": removed_symbols,
            "modified_symbols": modified_symbols,
            "impacted_cell_ids": sorted(impacted_cell_ids),
        }

    def _index_files(self, payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if not isinstance(payload, dict):
            return {}
        raw_files = payload.get("files")
        if not isinstance(raw_files, list):
            return {}
        indexed: dict[str, dict[str, Any]] = {}
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if path:
                indexed[path] = item
        return indexed

    def _index_symbols(self, file_record: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw_symbols = file_record.get("symbols")
        if not isinstance(raw_symbols, list):
            return {}
        indexed: dict[str, dict[str, Any]] = {}
        for item in raw_symbols:
            if not isinstance(item, dict):
                continue
            qualified_name = str(item.get("qualified_name") or "").strip()
            if qualified_name:
                indexed[qualified_name] = item
        return indexed

    def _file_cell_ids(self, file_record: dict[str, Any] | None) -> set[str]:
        if not isinstance(file_record, dict):
            return set()
        raw_cell_ids = file_record.get("cell_ids")
        if not isinstance(raw_cell_ids, list):
            return set()
        return {str(item).strip() for item in raw_cell_ids if str(item).strip()}

    def _symbol_cell_ids(self, symbol: dict[str, Any]) -> set[str]:
        raw_cell_ids = symbol.get("cell_ids")
        if not isinstance(raw_cell_ids, list):
            return set()
        return {str(item).strip() for item in raw_cell_ids if str(item).strip()}

    def _symbol_report(self, path: str, symbol: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": path,
            "qualified_name": str(symbol.get("qualified_name") or ""),
            "kind": str(symbol.get("kind") or ""),
            "line_start": int(symbol.get("line_start") or 1),
            "line_end": int(symbol.get("line_end") or int(symbol.get("line_start") or 1)),
            "cell_ids": sorted(self._symbol_cell_ids(symbol)),
        }


__all__ = ["ProjectionChangeAnalysisService"]
