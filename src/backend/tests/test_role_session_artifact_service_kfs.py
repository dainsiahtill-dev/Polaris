from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.session.internal.artifact_service import (
    RoleSessionArtifactService,
)


def test_role_session_artifact_service_roundtrip_with_kfs(tmp_path: Path) -> None:
    service = RoleSessionArtifactService(tmp_path)
    artifact = service.write_artifact(
        session_id="session-1",
        artifact_type="document",
        content="# hello",
        metadata={"source": "unit-test"},
    )

    loaded = service.read_artifact("session-1", artifact.id)
    assert loaded is not None
    assert loaded.id == artifact.id
    assert loaded.content == "# hello"

    listed = service.list_artifacts("session-1")
    assert any(item.id == artifact.id for item in listed)

    export_dir = tmp_path / ".polaris" / "exports"
    exported = service.export_artifacts("session-1", export_dir)
    assert len(exported) == 1
    assert exported[0].read_text(encoding="utf-8") == "# hello"

    assert service.delete_artifact("session-1", artifact.id) is True
    assert service.read_artifact("session-1", artifact.id) is None


def test_role_session_artifact_export_rejects_workspace_escape(tmp_path: Path) -> None:
    service = RoleSessionArtifactService(tmp_path)
    service.write_artifact(
        session_id="session-2",
        artifact_type="document",
        content="content",
    )

    outside_target = tmp_path.parent / "outside_exports"
    with pytest.raises(ValueError):
        service.export_artifacts("session-2", outside_target)
