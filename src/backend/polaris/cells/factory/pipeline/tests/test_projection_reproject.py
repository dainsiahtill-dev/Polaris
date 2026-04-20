"""Tests for selective reprojection after requirement changes."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.projection_lab import FactoryProjectionLabService
from polaris.cells.factory.pipeline.public.contracts import (
    ReprojectProjectionExperimentCommandV1,
    RunProjectionExperimentCommandV1,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter, set_default_adapter


@pytest.fixture(autouse=True)
def _configure_default_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


def test_reproject_resource_http_service_rewrites_only_impacted_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    service = FactoryProjectionLabService(str(workspace))
    initial_result = service.run_projection_experiment(
        RunProjectionExperimentCommandV1(
            workspace=str(workspace),
            scenario_id="resource_http_service",
            requirement="生成一个支持上传、下载、列表和删除的本地资源服务, 包含 HTTP API 与单元测试。",
            project_slug="resource_http_lab",
            use_pm_llm=False,
            run_verification=False,
            overwrite=False,
        )
    )

    def _fake_normalize_requirement(*_: object, **__: object) -> dict[str, object]:
        return {
            "source": "test_override",
            "project_title": "Resource HTTP Service Experiment v2",
            "summary": "Updated HTTP resource service summary for selective reprojection.",
            "capability_focus": [
                "upload payloads",
                "download payloads",
                "delete files",
            ],
            "host": "0.0.0.0",
            "port": 9001,
            "max_payload_mb": 32,
            "enable_checksum": False,
            "raw_output": "",
        }

    monkeypatch.setattr(service, "_normalize_requirement", _fake_normalize_requirement)
    result = service.reproject_experiment(
        ReprojectProjectionExperimentCommandV1(
            workspace=str(workspace),
            experiment_id=initial_result.experiment_id,
            requirement="把项目说明更新, 并把最大上传限制调到 32MB, 同时关闭 checksum。",
            use_pm_llm=False,
            run_verification=False,
        )
    )

    assert result.ok is True
    rewritten = set(result.rewritten_files)
    assert any(path.endswith("tui_runtime.md") for path in rewritten)
    assert any(path.endswith("application/config.py") for path in rewritten)
    assert any(path.endswith(".env.example") for path in rewritten)
    assert not any(path.endswith("infrastructure/blob_store.py") for path in rewritten)
    assert "target.delivery.cli" in result.impacted_cell_ids
    assert "target.resource.catalog" in result.impacted_cell_ids

    kernel_fs = KernelFileSystem(str(workspace), get_default_adapter())
    manifest = kernel_fs.read_json(f"workspace/factory/projection_lab/{initial_result.experiment_id}/manifest.json")
    assert manifest["manifest"]["settings"]["host"] == "0.0.0.0"
    assert manifest["manifest"]["settings"]["port"] == 9001
    report = kernel_fs.read_json(
        f"workspace/factory/projection_lab/{initial_result.experiment_id}/back_mapping_refresh_report.json"
    )
    assert "target.delivery.cli" in report["impacted_cell_ids"]

    config_text = (Path(result.project_root) / "resource_http_lab_app" / "application" / "config.py").read_text(
        encoding="utf-8"
    )
    env_text = (Path(result.project_root) / ".env.example").read_text(encoding="utf-8")
    assert 'host="0.0.0.0"' in config_text
    assert "port=9001" in config_text
    assert "RESOURCE_HOST=0.0.0.0" in env_text
    assert "RESOURCE_PORT=9001" in env_text
