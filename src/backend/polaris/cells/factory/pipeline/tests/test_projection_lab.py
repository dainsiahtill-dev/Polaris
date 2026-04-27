"""Tests for factory.pipeline projection lab."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.projection_lab import FactoryProjectionLabService
from polaris.cells.factory.pipeline.public.contracts import RunProjectionExperimentCommandV1
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter, set_default_adapter


@pytest.fixture(autouse=True)
def _configure_default_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


def test_projection_lab_generates_record_cli_project_and_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    service = FactoryProjectionLabService(str(workspace))
    result = service.run_projection_experiment(
        RunProjectionExperimentCommandV1(
            workspace=str(workspace),
            scenario_id="record_cli_app",
            requirement="生成一个本地 JSON 持久化的记录管理项目, 支持新增、列表、搜索、Finalize和测试。",
            project_slug="record_cli_lab",
            run_verification=True,
            overwrite=False,
        )
    )

    assert result.ok is True
    assert result.verification_ok is True

    project_root = Path(result.project_root)
    assert project_root.exists()
    assert (project_root / "tui_runtime.md").exists()
    assert (project_root / "pyproject.toml").exists()
    assert (project_root / "record_cli_lab_app" / "application" / "service.py").exists()
    assert (project_root / "tests" / "test_app.py").exists()

    kernel_fs = KernelFileSystem(str(workspace), get_default_adapter())
    assert result.normalization_source
    artifact_map = {Path(path).name: path for path in result.artifact_paths}
    cell_ir = kernel_fs.read_json(artifact_map["cell_ir.json"])
    projection_map = kernel_fs.read_json(artifact_map["projection_map.json"])
    verification = kernel_fs.read_json(artifact_map["verification_report.json"])
    back_mapping = kernel_fs.read_json(artifact_map["back_mapping_index.json"])

    assert cell_ir["scenario_id"] == "record_cli_app"
    assert {item["cell_id"] for item in cell_ir["target_cells"]} >= {
        "target.records.catalog",
        "target.records.storage",
        "target.delivery.cli",
        "target.tests.record_cli",
    }
    mapped_paths = {item["path"] for item in projection_map["entries"]}
    assert "tests/test_app.py" in mapped_paths
    service_file = next(item for item in back_mapping["files"] if str(item["path"]).endswith("application/service.py"))
    qualified_names = {symbol["qualified_name"] for symbol in service_file["symbols"]}
    assert any(name.endswith("RecordEntryService") for name in qualified_names)
    assert back_mapping["lookup"]["by_qualified_name"]
    assert verification["ok"] is True
    assert kernel_fs.exists("runtime/events/factory_projection_lab.jsonl")


def test_projection_lab_rejects_unsupported_scenario(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    service = FactoryProjectionLabService(str(workspace))
    with pytest.raises(RuntimeError):
        service.run_projection_experiment(
            RunProjectionExperimentCommandV1(
                workspace=str(workspace),
                scenario_id="kanban",
                requirement="生成一个看板应用。",
                project_slug="kanban_lab",
                run_verification=False,
                overwrite=False,
            )
        )
