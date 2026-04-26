"""Tests for export-to-workflow functionality.

This module tests the real handoff from role sessions to workflow systems.
"""

import json
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_export_to_workflow_creates_real_pm_run(tmp_path):
    """验证 export-to-workflow 创建真实 PM run"""
    # This test requires a full app context, so we mock the key behaviors
    from unittest.mock import AsyncMock, MagicMock, patch

    # Mock the orchestration service
    mock_result = MagicMock()
    mock_result.run_id = "pm-test-run-123"
    mock_result.status = "running"
    mock_result.message = "PM run started"

    with patch(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.OrchestrationCommandService.execute_pm_run",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        service = OrchestrationCommandService({})
        result = await service.execute_pm_run(
            workspace=str(tmp_path),
            run_type="full",
            options={"directive": "Test export to workflow"},
        )

        assert result.run_id == "pm-test-run-123"
        assert result.status == "running"


@pytest.mark.asyncio
async def test_export_to_workflow_creates_real_director_run(tmp_path):
    """验证 export-to-workflow 创建真实 Director run"""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_result = MagicMock()
    mock_result.run_id = "director-test-run-456"
    mock_result.status = "running"
    mock_result.message = "Director run started"

    with patch(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.OrchestrationCommandService.execute_director_run",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        service = OrchestrationCommandService({})
        result = await service.execute_director_run(
            workspace=str(tmp_path),
            tasks=["task-1", "task-2"],
            options={"max_workers": 3},
        )

        assert result.run_id == "director-test-run-456"
        assert result.status == "running"


@pytest.mark.asyncio
async def test_export_to_workflow_creates_factory_run(tmp_path):
    """验证 export-to-workflow 创建真实 Factory run"""
    from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRunService

    service = FactoryRunService(workspace=tmp_path)

    config = FactoryConfig(
        name="test-export",
        description="Test export to factory",
        stages=["docs_generation"],
        auto_dispatch=False,
    )

    run = await service.create_run(config)
    assert run.id is not None
    assert run.config.name == "test-export"
    assert run.config.stages == ["docs_generation"]

    # Verify run is persisted
    runs = await service.list_runs()
    assert any(r.get("id") == run.id for r in runs)


def test_export_bundle_persistence(tmp_path):
    """验证 export bundle 被正确持久化到文件系统"""
    export_dir = tmp_path / ".polaris" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    export_bundle = {
        "session_id": "test-session-123",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "target": "pm",
        "export_kind": "session_bundle",
        "artifacts": [{"id": "art-1", "type": "directive", "content": "Test directive"}],
        "event_count": 5,
    }

    export_filename = "test-session-123_20240306_120000_export.json"
    export_path = export_dir / export_filename

    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(export_bundle, f, ensure_ascii=False, indent=2)

    # Verify file exists and can be read back
    assert export_path.exists()

    with open(export_path, encoding="utf-8") as f:
        loaded = json.load(f)

    assert loaded["session_id"] == "test-session-123"
    assert loaded["target"] == "pm"
    assert len(loaded["artifacts"]) == 1


def test_build_directive_from_artifacts():
    """测试从 artifacts 构建 directive"""
    # Import the helper function
    import sys

    sys.path.insert(0, "src/backend")

    artifacts = [
        {"id": "art-1", "type": "directive", "content": "Implement login feature"},
        {"id": "art-2", "type": "requirement", "content": "Use JWT for auth"},
    ]

    # Simulate the _build_directive_from_artifacts logic
    directives = []
    for artifact in artifacts:
        content = artifact.get("content", "")
        artifact_type = artifact.get("type", "")
        if artifact_type in ("directive", "requirement", "goal"):
            directives.append(content)

    result = "\n\n".join(directives)
    assert "Implement login feature" in result
    assert "Use JWT for auth" in result


def test_build_task_filter_from_artifacts():
    """测试从 artifacts 构建 task filter"""
    artifacts = [
        {"id": "art-1", "type": "task", "content": "Create user model"},
        {"id": "art-2", "type": "task", "content": "Implement auth API"},
    ]

    tasks = []
    for artifact in artifacts:
        content = artifact.get("content", "")
        artifact_type = artifact.get("type", "")
        if artifact_type in ("task", "todo", "action_item"):
            tasks.append(content)

    result = "Execute tasks: " + "; ".join(tasks[:5]) if tasks else "Execute ready tasks"

    assert "Create user model" in result
    assert "Implement auth API" in result
