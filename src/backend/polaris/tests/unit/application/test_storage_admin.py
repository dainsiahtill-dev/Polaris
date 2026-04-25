"""Tests for polaris.application.storage_admin."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.application.storage_admin import (
    StorageAdminError,
    StorageAdminService,
    StorageEnvironment,
    StorageLayoutSnapshot,
)


class TestStorageAdminError:
    def test_default_code(self) -> None:
        err = StorageAdminError("oops")
        assert err.code == "storage_admin_error"
        assert str(err) == "oops"

    def test_custom_code_and_cause(self) -> None:
        cause = ValueError("inner")
        err = StorageAdminError("oops", code="custom", cause=cause)
        assert err.code == "custom"
        assert err.cause is cause


class TestStorageAdminService:
    # -- get_polaris_home ----------------------------------------------------

    def test_get_polaris_home_success(self) -> None:
        fake_mod = MagicMock()
        fake_mod.polaris_home.return_value = "/home/polaris"
        with patch.dict(
            "sys.modules",
            {"polaris.cells.storage.layout.public": fake_mod},
        ):
            assert StorageAdminService.get_polaris_home() == "/home/polaris"

    def test_get_polaris_home_failure(self) -> None:
        fake_mod = MagicMock()
        fake_mod.polaris_home.side_effect = RuntimeError("boom")
        with (
            patch.dict(
                "sys.modules",
                {"polaris.cells.storage.layout.public": fake_mod},
            ),
            pytest.raises(StorageAdminError) as exc_info,
        ):
            StorageAdminService.get_polaris_home()
        assert exc_info.value.code == "polaris_home_error"

    # -- resolve_env ---------------------------------------------------------

    def test_resolve_env_success(self) -> None:
        fake_mod = MagicMock()
        fake_mod.resolve_env_str.return_value = "/cache"
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone._runtime_config": fake_mod},
        ):
            assert StorageAdminService.resolve_env("runtime_cache_root") == "/cache"

    def test_resolve_env_failure_returns_empty(self) -> None:
        fake_mod = MagicMock()
        fake_mod.resolve_env_str.side_effect = ImportError("no module")
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone._runtime_config": fake_mod},
        ):
            assert StorageAdminService.resolve_env("runtime_root") == ""

    # -- get_storage_environment ---------------------------------------------

    def test_get_storage_environment(self) -> None:
        with (
            patch.object(StorageAdminService, "get_polaris_home", return_value="/home"),
            patch.object(StorageAdminService, "resolve_env", side_effect=lambda k: f"val_{k}"),
        ):
            env = StorageAdminService.get_storage_environment()
        assert isinstance(env, StorageEnvironment)
        assert env.kernelone_home == "/home"
        assert env.runtime_root == "val_runtime_root"
        assert env.runtime_cache_root == "val_runtime_cache_root"
        assert env.state_to_ramdisk == "val_state_to_ramdisk"

    # -- resolve_global_path -------------------------------------------------

    def test_resolve_global_path_success(self) -> None:
        fake_mod = MagicMock()
        fake_mod.resolve_global_path.return_value = "/global/config.json"
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone.storage": fake_mod},
        ):
            result = StorageAdminService.resolve_global_path("config/settings.json")
        assert result == "/global/config.json"

    def test_resolve_global_path_failure(self) -> None:
        fake_mod = MagicMock()
        fake_mod.resolve_global_path.side_effect = ValueError("bad path")
        with (
            patch.dict(
                "sys.modules",
                {"polaris.kernelone.storage": fake_mod},
            ),
            pytest.raises(StorageAdminError) as exc_info,
        ):
            StorageAdminService.resolve_global_path("bad")
        assert exc_info.value.code == "global_path_error"

    # -- resolve_workspace_persistent_path -----------------------------------

    def test_resolve_workspace_persistent_path_success(self) -> None:
        fake_mod = MagicMock()
        fake_mod.resolve_workspace_persistent_path.return_value = "/ws/brain"
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone.storage": fake_mod},
        ):
            result = StorageAdminService.resolve_workspace_persistent_path("/ws", "brain")
        assert result == "/ws/brain"

    def test_resolve_workspace_persistent_path_failure(self) -> None:
        fake_mod = MagicMock()
        fake_mod.resolve_workspace_persistent_path.side_effect = RuntimeError("boom")
        with (
            patch.dict(
                "sys.modules",
                {"polaris.kernelone.storage": fake_mod},
            ),
            pytest.raises(StorageAdminError) as exc_info,
        ):
            StorageAdminService.resolve_workspace_persistent_path("/ws", "x")
        assert exc_info.value.code == "workspace_path_error"

    # -- build_cache_root ----------------------------------------------------

    def test_build_cache_root_success(self) -> None:
        fake_mod = MagicMock()
        fake_mod.build_cache_root.return_value = "/cache/ws"
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone.storage.io_paths": fake_mod},
        ):
            result = StorageAdminService.build_cache_root("/ram", "/ws")
        assert result == "/cache/ws"

    def test_build_cache_root_failure(self) -> None:
        fake_mod = MagicMock()
        fake_mod.build_cache_root.side_effect = ValueError("boom")
        with (
            patch.dict(
                "sys.modules",
                {"polaris.kernelone.storage.io_paths": fake_mod},
            ),
            pytest.raises(StorageAdminError) as exc_info,
        ):
            StorageAdminService.build_cache_root("/ram", "/ws")
        assert exc_info.value.code == "cache_root_error"

    # -- resolve_storage_roots -----------------------------------------------

    def test_resolve_storage_roots_success(self) -> None:
        roots = MagicMock()
        roots.workspace_abs = "/ws"
        fake_mod = MagicMock()
        fake_mod.resolve_storage_roots.return_value = roots
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone.storage": fake_mod},
        ):
            result = StorageAdminService.resolve_storage_roots("/ws")
        assert result is roots

    def test_resolve_storage_roots_failure(self) -> None:
        fake_mod = MagicMock()
        fake_mod.resolve_storage_roots.side_effect = ImportError("no module")
        with (
            patch.dict(
                "sys.modules",
                {"polaris.kernelone.storage": fake_mod},
            ),
            pytest.raises(StorageAdminError) as exc_info,
        ):
            StorageAdminService.resolve_storage_roots("/ws")
        assert exc_info.value.code == "storage_roots_error"

    # -- list_storage_policies -----------------------------------------------

    def test_list_storage_policies_empty(self) -> None:
        fake_mod = MagicMock()
        fake_mod.STORAGE_POLICY_REGISTRY = []
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone.storage": fake_mod},
        ):
            result = StorageAdminService.list_storage_policies()
        assert result == ()

    def test_list_storage_policies_dedup_and_filter(self) -> None:
        policy1 = MagicMock()
        policy1.logical_prefix = "logs"
        cat1 = MagicMock()
        cat1.value = "data"
        policy1.category = cat1
        lc1 = MagicMock()
        lc1.value = "ephemeral"
        policy1.lifecycle = lc1
        policy1.retention_days = 7
        policy1.compress = True
        policy1.archive_on_terminal = False

        policy2 = MagicMock()
        policy2.logical_prefix = "logs"  # duplicate prefix
        policy2.category = cat1
        policy2.lifecycle = lc1
        policy2.retention_days = 7
        policy2.compress = True
        policy2.archive_on_terminal = False

        policy3 = MagicMock()
        policy3.logical_prefix = ""  # empty prefix, should be skipped
        policy3.category = cat1
        policy3.lifecycle = lc1

        fake_mod = MagicMock()
        fake_mod.STORAGE_POLICY_REGISTRY = [policy1, policy2, policy3]
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone.storage": fake_mod},
        ):
            result = StorageAdminService.list_storage_policies()
        assert len(result) == 1
        assert result[0].prefix == "logs"
        assert result[0].category == "data"
        assert result[0].lifecycle == "ephemeral"
        assert result[0].retention_days == 7
        assert result[0].compress is True
        assert result[0].archive_on_terminal is False

    def test_list_storage_policies_import_failure(self) -> None:
        fake_mod = MagicMock()
        fake_mod.STORAGE_POLICY_REGISTRY.side_effect = ImportError("no module")
        with patch.dict(
            "sys.modules",
            {"polaris.kernelone.storage": fake_mod},
        ):
            result = StorageAdminService.list_storage_policies()
        assert result == ()

    # -- resolve_well_known_paths --------------------------------------------

    def test_resolve_well_known_paths(self) -> None:
        with (
            patch.object(
                StorageAdminService,
                "resolve_global_path",
                side_effect=lambda p: f"/global/{p}",
            ),
            patch.object(
                StorageAdminService,
                "resolve_workspace_persistent_path",
                side_effect=lambda ws, p: f"{ws}/{p}",
            ),
        ):
            paths = StorageAdminService.resolve_well_known_paths("/ws")
        assert paths["settings"] == "/global/config/settings.json"
        assert paths["brain"] == "/ws/workspace/brain"
        assert paths["history_runs"] == "/ws/workspace/history/runs"

    # -- resolve_full_layout -------------------------------------------------

    def test_resolve_full_layout(self) -> None:
        roots = MagicMock()
        roots.workspace_abs = "/ws"
        roots.workspace_key = "wskey"
        roots.storage_layout_mode = "v2"
        roots.runtime_mode = "dev"
        roots.home_root = "/home"
        roots.global_root = "/global"
        roots.projects_root = "/projects"
        roots.project_root = "/projects/p1"
        roots.config_root = "/config"
        roots.workspace_persistent_root = "/ws/persist"
        roots.project_persistent_root = "/projects/p1/persist"
        roots.runtime_base = "/runtime"
        roots.runtime_root = "/runtime/r1"
        roots.runtime_project_root = "/runtime/r1/p1"
        roots.history_root = "/history"

        with (
            patch.object(
                StorageAdminService,
                "resolve_storage_roots",
                return_value=roots,
            ),
            patch.object(
                StorageAdminService,
                "get_storage_environment",
                return_value=StorageEnvironment(
                    kernelone_home="/home",
                    runtime_root="/runtime",
                    runtime_cache_root="/cache",
                    state_to_ramdisk="false",
                ),
            ),
            patch.object(
                StorageAdminService,
                "list_storage_policies",
                return_value=(),
            ),
            patch.object(
                StorageAdminService,
                "resolve_well_known_paths",
                return_value={"brain": "/ws/brain"},
            ),
        ):
            snapshot = StorageAdminService.resolve_full_layout("/ws", ramdisk_root="/ram")
        assert isinstance(snapshot, StorageLayoutSnapshot)
        assert snapshot.workspace == "/ws"
        assert snapshot.workspace_key == "wskey"
        assert snapshot.storage_layout_mode == "v2"
        assert snapshot.ramdisk_root == "/ram"
        assert snapshot.paths == {"brain": "/ws/brain"}
        assert snapshot.migration_version == 2

    def test_resolve_full_layout_failure(self) -> None:
        fake_mod = MagicMock()
        fake_mod.resolve_storage_roots.side_effect = RuntimeError("boom")
        with (
            patch.dict(
                "sys.modules",
                {"polaris.kernelone.storage": fake_mod},
            ),
            pytest.raises(StorageAdminError) as exc_info,
        ):
            StorageAdminService.resolve_full_layout("/ws")
        assert exc_info.value.code == "storage_roots_error"
