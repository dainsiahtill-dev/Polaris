from __future__ import annotations

from pathlib import Path

from polaris.cells.runtime.task_market.internal.saga import (
    CompensationAction,
    SagaCompensator,
)


def test_commit_clears_actions_and_skips_later_compensation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "artifact.txt"
    target.write_text("hello", encoding="utf-8")

    metadata: dict[str, object] = {}
    compensator = SagaCompensator()
    compensator.register_action(
        metadata,
        CompensationAction(action_type="file_delete", target="artifact.txt"),
    )
    state = compensator.commit(metadata)

    assert state["committed"] is True
    assert state["committed_action_count"] == 1
    assert state["actions"] == []

    summary = compensator.compensate(
        item_metadata=metadata,
        workspace=str(workspace),
        reason="post_commit_failure",
        initiator="test",
    )
    assert summary["executed"] is False
    assert summary["reason"] == "already_committed"
    assert target.exists()


def test_compensate_file_delete_removes_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "artifact.txt"
    target.write_text("hello", encoding="utf-8")

    metadata: dict[str, object] = {}
    compensator = SagaCompensator()
    compensator.register_action(
        metadata,
        CompensationAction(action_type="file_delete", target="artifact.txt"),
    )

    summary = compensator.compensate(
        item_metadata=metadata,
        workspace=str(workspace),
        reason="task_failed",
        initiator="test",
    )
    assert summary["executed"] is True
    assert summary["requires_manual_intervention"] is False
    assert not target.exists()


def test_compensate_rejects_path_escape_and_flags_manual_intervention(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, object] = {}
    compensator = SagaCompensator()
    compensator.register_action(
        metadata,
        CompensationAction(action_type="file_delete", target="../outside.txt"),
    )

    summary = compensator.compensate(
        item_metadata=metadata,
        workspace=str(workspace),
        reason="task_failed",
        initiator="test",
    )
    assert summary["executed"] is True
    assert summary["requires_manual_intervention"] is True
    results = list(summary["results"])
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "outside workspace" in results[0]["error"]
