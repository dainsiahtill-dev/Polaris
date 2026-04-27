"""Tests for projection back-mapping refresh after workspace code changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.cells.factory.pipeline.internal.projection_change_analysis import (
    ProjectionChangeAnalysisService,
)
from polaris.cells.factory.pipeline.internal.projection_lab import FactoryProjectionLabService
from polaris.cells.factory.pipeline.public.contracts import RunProjectionExperimentCommandV1
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter, set_default_adapter

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _configure_default_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


def test_refresh_back_mapping_detects_added_method_and_impacted_cell(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    projection_service = FactoryProjectionLabService(str(workspace))
    result = projection_service.run_projection_experiment(
        RunProjectionExperimentCommandV1(
            workspace=str(workspace),
            scenario_id="record_cli_app",
            requirement="生成一个本地 JSON 持久化的记录管理项目, 支持新增、列表、搜索、Finalize和测试。",
            project_slug="record_cli_change_lab",
            run_verification=True,
            overwrite=False,
        )
    )

    kernel_fs = KernelFileSystem(str(workspace), get_default_adapter())
    project_root_relative = kernel_fs.to_workspace_relative_path(result.project_root)
    service_file = f"{project_root_relative}/record_cli_change_lab_app/application/service.py"
    original_source = kernel_fs.workspace_read_text(service_file, encoding="utf-8")
    injected_source = original_source.replace(
        "    def archive_record(",
        (
            "    def summarize_records(self) -> str:\n"
            "        records = self.list_records(include_archived=True)\n"
            "        return f'total={len(records)}'\n"
            "\n"
            "    def archive_record("
        ),
        1,
    )
    kernel_fs.workspace_write_text(service_file, injected_source, encoding="utf-8")

    change_service = ProjectionChangeAnalysisService(str(workspace))
    report = change_service.refresh_back_mapping(result.experiment_id)

    changed_paths = {item["path"] for item in report["changed_files"]}
    assert "record_cli_change_lab_app/application/service.py" in changed_paths
    assert "target.records.catalog" in report["impacted_cell_ids"]
    assert any(item["qualified_name"] == "RecordEntryService.summarize_records" for item in report["added_symbols"])

    updated_index = kernel_fs.read_json(
        f"workspace/factory/projection_lab/{result.experiment_id}/back_mapping_index.json"
    )
    all_symbols = {
        symbol["qualified_name"] for file_record in updated_index["files"] for symbol in file_record["symbols"]
    }
    assert "RecordEntryService.summarize_records" in all_symbols
    assert kernel_fs.exists(f"workspace/factory/projection_lab/{result.experiment_id}/back_mapping_refresh_report.json")
