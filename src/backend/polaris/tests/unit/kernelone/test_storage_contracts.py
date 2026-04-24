"""Tests for polaris.kernelone.storage.contracts (StorageCategory, Lifecycle, StoragePolicy)."""

from __future__ import annotations

import pytest
from polaris.kernelone.storage.contracts import (
    Lifecycle,
    StorageCategory,
    StoragePolicy,
    StorageRoots,
)


class TestStorageCategory:
    """Tests for StorageCategory enum."""

    def test_all_values_present(self) -> None:
        values = {c.value for c in StorageCategory}
        expected = {
            "global_config",
            "workspace_persistent",
            "runtime_current",
            "runtime_run",
            "workspace_history",
            "factory_current",
            "factory_history",
        }
        assert expected.issubset(values)

    def test_is_string_enum(self) -> None:
        assert isinstance(StorageCategory.GLOBAL_CONFIG, str)
        assert StorageCategory.GLOBAL_CONFIG == "global_config"

    def test_each_category_accessible(self) -> None:
        for cat in StorageCategory:
            assert cat.value is not None


class TestLifecycle:
    """Tests for Lifecycle enum."""

    def test_all_values_present(self) -> None:
        values = {lc.value for lc in Lifecycle}
        expected = {"permanent", "active", "ephemeral", "history"}
        assert expected.issubset(values)

    def test_is_string_enum(self) -> None:
        assert isinstance(Lifecycle.PERMANENT, str)
        assert Lifecycle.PERMANENT == "permanent"

    def test_each_lifecycle_accessible(self) -> None:
        for lc in Lifecycle:
            assert lc.value is not None


class TestStoragePolicy:
    """Tests for StoragePolicy dataclass."""

    def test_required_fields(self) -> None:
        policy = StoragePolicy(
            logical_prefix="runtime/events",
            category=StorageCategory.RUNTIME_CURRENT,
            lifecycle=Lifecycle.ACTIVE,
        )
        assert policy.logical_prefix == "runtime/events"
        assert policy.category == StorageCategory.RUNTIME_CURRENT
        assert policy.lifecycle == Lifecycle.ACTIVE

    def test_default_values(self) -> None:
        policy = StoragePolicy(
            logical_prefix="config",
            category=StorageCategory.GLOBAL_CONFIG,
            lifecycle=Lifecycle.PERMANENT,
        )
        assert policy.retention_days == -1
        assert policy.compress is False
        assert policy.archive_on_terminal is False

    def test_all_fields(self) -> None:
        policy = StoragePolicy(
            logical_prefix="workspace/history",
            category=StorageCategory.WORKSPACE_HISTORY,
            lifecycle=Lifecycle.HISTORY,
            retention_days=30,
            compress=True,
            archive_on_terminal=True,
        )
        assert policy.retention_days == 30
        assert policy.compress is True
        assert policy.archive_on_terminal is True

    def test_should_archive(self) -> None:
        policy_no_archive = StoragePolicy(
            logical_prefix="runtime/state",
            category=StorageCategory.RUNTIME_CURRENT,
            lifecycle=Lifecycle.EPHEMERAL,
            archive_on_terminal=False,
        )
        assert policy_no_archive.should_archive() is False

        policy_archive = StoragePolicy(
            logical_prefix="runtime/contracts",
            category=StorageCategory.RUNTIME_CURRENT,
            lifecycle=Lifecycle.ACTIVE,
            archive_on_terminal=True,
        )
        assert policy_archive.should_archive() is True

    def test_should_compress(self) -> None:
        policy_compress = StoragePolicy(
            logical_prefix="workspace/history",
            category=StorageCategory.WORKSPACE_HISTORY,
            lifecycle=Lifecycle.HISTORY,
            compress=True,
        )
        assert policy_compress.should_compress() is True

        policy_no_compress = StoragePolicy(
            logical_prefix="config",
            category=StorageCategory.GLOBAL_CONFIG,
            lifecycle=Lifecycle.PERMANENT,
            compress=False,
        )
        assert policy_no_compress.should_compress() is False

    def test_get_retention_days(self) -> None:
        policy = StoragePolicy(
            logical_prefix="runtime/state",
            category=StorageCategory.RUNTIME_CURRENT,
            lifecycle=Lifecycle.EPHEMERAL,
            retention_days=7,
        )
        assert policy.get_retention_days() == 7

    def test_frozen(self) -> None:
        policy = StoragePolicy(
            logical_prefix="config",
            category=StorageCategory.GLOBAL_CONFIG,
            lifecycle=Lifecycle.PERMANENT,
        )
        with pytest.raises(AttributeError):
            policy.retention_days = 10  # type: ignore[misc]


class TestStorageRoots:
    """Tests for StorageRoots dataclass."""

    def test_all_fields(self) -> None:
        roots = StorageRoots(
            workspace_abs="/ws",
            workspace_key="my-workspace-abcd1234",
            storage_layout_mode="project_local",
            home_root="/home/user/.polaris",
            global_root="/home/user/.polaris",
            config_root="/home/user/.polaris/config",
            projects_root="/ws/.polaris",
            project_root="/ws/.polaris",
            project_persistent_root="/ws/.polaris",
            runtime_projects_root="/tmp/.polaris/projects",
            runtime_project_root="/tmp/.polaris/projects/my-workspace-abcd1234/runtime",
            workspace_persistent_root="/ws/.polaris",
            runtime_base="/tmp/.polaris",
            runtime_root="/tmp/.polaris/projects/my-workspace-abcd1234/runtime",
            runtime_mode="system_cache",
            history_root="/ws/.polaris/history",
        )
        assert roots.workspace_abs == "/ws"
        assert roots.workspace_key == "my-workspace-abcd1234"
        assert roots.runtime_root == "/tmp/.polaris/projects/my-workspace-abcd1234/runtime"
        assert roots.config_root == "/home/user/.polaris/config"
        assert roots.history_root == "/ws/.polaris/history"

    def test_frozen(self) -> None:
        roots = StorageRoots(
            workspace_abs="/ws",
            workspace_key="key",
            storage_layout_mode="test",
            home_root="/home",
            global_root="/home/.polaris",
            config_root="/home/.polaris/config",
            projects_root="/ws/.polaris",
            project_root="/ws/.polaris",
            project_persistent_root="/ws/.polaris",
            runtime_projects_root="/tmp/.polaris/projects",
            runtime_project_root="/tmp/.polaris/projects/key/runtime",
            workspace_persistent_root="/ws/.polaris",
            runtime_base="/tmp/.polaris",
            runtime_root="/tmp/.polaris/projects/key/runtime",
            runtime_mode="test",
            history_root="/ws/.polaris/history",
        )
        with pytest.raises(AttributeError):
            roots.workspace_abs = "/other"  # type: ignore[misc]
