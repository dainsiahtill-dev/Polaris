from __future__ import annotations

from pathlib import Path

from polaris.kernelone.runtime.instance_state import (
    InstanceScopedStateStore,
    get_current_instance_id,
    normalize_workspace_instance_id,
    scoped_instance,
)


def test_instance_store_reuses_instance_for_same_workspace(tmp_path: Path) -> None:
    store: InstanceScopedStateStore[dict[str, str]] = InstanceScopedStateStore(
        normalizer=normalize_workspace_instance_id,
    )
    workspace = str(tmp_path / "workspace")

    first = store.get_or_create(workspace, lambda: {"id": "first"})
    second = store.get_or_create(Path(workspace), lambda: {"id": "second"})

    assert first is second
    assert second["id"] == "first"


def test_instance_store_dispose_triggers_cleanup(tmp_path: Path) -> None:
    disposed: list[str] = []
    store: InstanceScopedStateStore[dict[str, str]] = InstanceScopedStateStore(
        normalizer=normalize_workspace_instance_id,
        on_dispose=lambda value: disposed.append(value["id"]),
    )
    workspace = str(tmp_path / "workspace")
    value = store.get_or_create(workspace, lambda: {"id": "manager-1"})
    store.register_cleanup(workspace, lambda item: disposed.append(f"cleanup:{item['id']}"))

    assert value["id"] == "manager-1"
    assert store.dispose(workspace) is True
    assert "manager-1" in disposed
    assert "cleanup:manager-1" in disposed
    assert store.dispose(workspace) is False


def test_scoped_instance_sets_context_and_resets() -> None:
    assert get_current_instance_id() is None
    with scoped_instance("workspace-A"):
        assert get_current_instance_id() == "workspace-A"
    assert get_current_instance_id() is None
