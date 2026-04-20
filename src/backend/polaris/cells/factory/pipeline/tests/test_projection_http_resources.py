"""Tests for the resource_http_service projection profile."""

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


def test_resource_http_service_projection_generates_large_verified_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    service = FactoryProjectionLabService(str(workspace))
    result = service.run_projection_experiment(
        RunProjectionExperimentCommandV1(
            workspace=str(workspace),
            scenario_id="resource_http_service",
            requirement="生成一个支持上传、下载、列表和删除的本地资源服务, 包含 HTTP API 与单元测试。",
            project_slug="resource_http_lab",
            run_verification=True,
            overwrite=False,
        )
    )

    assert result.ok is True
    assert result.verification_ok is True
    assert "target.resource.catalog" in result.cell_ids
    assert len(result.generated_files) >= 18

    project_root = Path(result.project_root)
    python_files = sorted(project_root.rglob("*.py"))
    total_lines = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in python_files)
    assert len(python_files) >= 10
    assert total_lines >= 500
    assert (project_root / "tests" / "test_http_api.py").exists()


def test_resource_http_service_projection_consumes_host_and_port_from_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    service = FactoryProjectionLabService(str(workspace))

    def _fake_normalize_requirement(*_: object, **__: object) -> dict[str, object]:
        return {
            "source": "test_override",
            "project_title": "Resource HTTP Service Host Port",
            "summary": "Host and port should be reflected in projected outputs.",
            "capability_focus": ["http api"],
            "host": "0.0.0.0",
            "port": 9001,
            "max_payload_mb": 16,
            "enable_checksum": True,
            "raw_output": "",
        }

    monkeypatch.setattr(service, "_normalize_requirement", _fake_normalize_requirement)
    result = service.run_projection_experiment(
        RunProjectionExperimentCommandV1(
            workspace=str(workspace),
            scenario_id="resource_http_service",
            requirement="生成资源服务, 并监听 0.0.0.0:9001。",
            project_slug="resource_http_host_port",
            use_pm_llm=False,
            run_verification=False,
            overwrite=False,
        )
    )

    assert result.ok is True
    project_root = Path(result.project_root)
    config_text = (project_root / "resource_http_host_port_app" / "application" / "config.py").read_text(
        encoding="utf-8"
    )
    env_text = (project_root / ".env.example").read_text(encoding="utf-8")
    assert 'host="0.0.0.0"' in config_text
    assert "port=9001" in config_text
    assert "RESOURCE_HOST=0.0.0.0" in env_text
    assert "RESOURCE_PORT=9001" in env_text

    kernel_fs = KernelFileSystem(str(workspace), get_default_adapter())
    manifest = kernel_fs.read_json(f"workspace/factory/projection_lab/{result.experiment_id}/manifest.json")
    assert manifest["manifest"]["settings"]["host"] == "0.0.0.0"
    assert manifest["manifest"]["settings"]["port"] == 9001
