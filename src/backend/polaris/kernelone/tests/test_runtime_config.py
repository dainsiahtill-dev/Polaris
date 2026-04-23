"""Tests for KernelOne runtime configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestWorkspaceMetadataDirName:
    """Tests for workspace metadata directory name injection."""

    def test_default_is_kernelone(self) -> None:
        from polaris.kernelone._runtime_config import (
            get_workspace_metadata_dir_default,
            get_workspace_metadata_dir_name,
        )

        # Polaris project sets default to .polaris (injected at bootstrap)
        assert get_workspace_metadata_dir_default() == ".polaris"
        assert get_workspace_metadata_dir_name() == ".polaris"

    def test_injection_changes_name(self) -> None:
        from polaris.kernelone._runtime_config import (
            get_workspace_metadata_dir_name,
            set_workspace_metadata_dir_name,
        )

        set_workspace_metadata_dir_name(".polaris")
        try:
            assert get_workspace_metadata_dir_name() == ".polaris"
        finally:
            # Restore to Polaris's injected value
            set_workspace_metadata_dir_name(".polaris")

    def test_injection_strips_whitespace(self) -> None:
        from polaris.kernelone._runtime_config import (
            get_workspace_metadata_dir_name,
            set_workspace_metadata_dir_name,
        )

        set_workspace_metadata_dir_name("  .custom-dir  ")
        try:
            assert get_workspace_metadata_dir_name() == ".custom-dir"
        finally:
            # Restore to Polaris's injected value
            set_workspace_metadata_dir_name(".polaris")

    def test_injection_empty_becomes_default(self) -> None:
        from polaris.kernelone._runtime_config import (
            get_workspace_metadata_dir_name,
            set_workspace_metadata_dir_name,
        )

        set_workspace_metadata_dir_name("")
        try:
            # Empty string falls back to Polaris's default (.polaris)
            assert get_workspace_metadata_dir_name() == ".polaris"
        finally:
            # Restore to Polaris's injected value
            set_workspace_metadata_dir_name(".polaris")


class TestEnvResolution:
    """Tests for KERNELONE_* env var resolution."""

    def test_kern_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_str

        monkeypatch.setenv("KERNELONE_WORKSPACE", "/kern/path")

        assert resolve_env_str("workspace") == "/kern/path"

    def test_kern_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_str

        monkeypatch.delenv("KERNELONE_WORKSPACE", raising=False)

        # workspace has empty default
        assert resolve_env_str("workspace") == ""
        # runtime_base has a non-empty default
        assert resolve_env_str("runtime_base") == "runtime"

    def test_float_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_float

        monkeypatch.setenv("KERNELONE_RUNTIME_EVENT_DEDUP_WINDOW_SEC", "2.5")

        assert resolve_env_float("runtime_event_dedup_window_sec") == 2.5

    def test_float_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_float

        monkeypatch.delenv("KERNELONE_RUNTIME_EVENT_DEDUP_WINDOW_SEC", raising=False)

        assert resolve_env_float("runtime_event_dedup_window_sec") == 1.5

    def test_float_invalid_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_float

        monkeypatch.setenv("KERNELONE_RUNTIME_EVENT_DEDUP_WINDOW_SEC", "not-a-float")

        assert resolve_env_float("runtime_event_dedup_window_sec") == 0.0

    def test_int_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_int

        monkeypatch.setenv("KERNELONE_JSONL_FLUSH_BATCH", "100")

        assert resolve_env_int("jsonl_flush_batch") == 100

    def test_int_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_int

        monkeypatch.delenv("KERNELONE_JSONL_FLUSH_BATCH", raising=False)

        assert resolve_env_int("jsonl_flush_batch") == 50

    def test_int_invalid_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_int

        monkeypatch.setenv("KERNELONE_JSONL_FLUSH_BATCH", "bad")

        assert resolve_env_int("jsonl_flush_batch") == 0

    def test_bool_truthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_bool

        monkeypatch.setenv("KERNELONE_JSONL_BUFFERED", "1")
        assert resolve_env_bool("jsonl_buffered") is True

        monkeypatch.setenv("KERNELONE_JSONL_BUFFERED", "true")
        assert resolve_env_bool("jsonl_buffered") is True

        monkeypatch.setenv("KERNELONE_JSONL_BUFFERED", "yes")
        assert resolve_env_bool("jsonl_buffered") is True

    def test_bool_falsy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_bool

        for val in ("0", "false", "no", "off"):
            monkeypatch.setenv("KERNELONE_JSONL_BUFFERED", val)
            assert resolve_env_bool("jsonl_buffered") is False, f"expected False for {val!r}"

    def test_bool_kern_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_bool

        monkeypatch.setenv("KERNELONE_JSONL_BUFFERED", "0")

        assert resolve_env_bool("jsonl_buffered") is False

    def test_bool_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import resolve_env_bool

        monkeypatch.delenv("KERNELONE_JSONL_BUFFERED", raising=False)

        assert resolve_env_bool("jsonl_buffered") is True


class TestConvenienceAccessors:
    """Tests for convenience accessor functions."""

    def test_get_workspace_from_kern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import get_workspace

        monkeypatch.setenv("KERNELONE_WORKSPACE", "/kern/ws")

        assert get_workspace() == "/kern/ws"

    def test_get_workspace_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import get_workspace

        monkeypatch.delenv("KERNELONE_WORKSPACE", raising=False)

        assert get_workspace() == ""

    def test_get_runtime_base_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import get_runtime_base

        monkeypatch.delenv("KERNELONE_RUNTIME_BASE", raising=False)

        assert get_runtime_base() == "runtime"

    def test_get_trace_id_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import get_trace_id

        monkeypatch.delenv("KERNELONE_TRACE_ID", raising=False)

        assert get_trace_id() is None

    def test_get_trace_id_from_kern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.kernelone._runtime_config import get_trace_id

        monkeypatch.setenv("KERNELONE_TRACE_ID", "kern-trace-123")

        assert get_trace_id() == "kern-trace-123"


class TestIntegrationWithStorageLayout:
    """Tests for _runtime_config working with storage/layout."""

    def test_kernelone_home_from_kern_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that kernelone_home respects KERNELONE_HOME env var.

        Note: The polaris_home alias has been removed. For Polaris-specific
        home paths, use polaris_home() from polaris.cells.storage.layout.internal.layout_business.
        """
        from pathlib import Path

        from polaris.kernelone.storage.layout import kernelone_home

        monkeypatch.setenv("KERNELONE_HOME", "/kern/home")

        assert kernelone_home() == str(Path("/kern/home").resolve())

    def test_kernelone_home_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that kernelone_home falls back to default when unset.

        Note: The polaris_home alias has been removed. For Polaris-specific
        home paths, use polaris_home() from polaris.cells.storage.layout.internal.layout_business.
        """
        from pathlib import Path

        from polaris.kernelone.storage.layout import kernelone_home

        monkeypatch.delenv("KERNELONE_HOME", raising=False)

        # When unset, falls back to default (home directory / .polaris)
        expected = str(Path.home() / ".polaris")
        assert kernelone_home() == expected

    def test_storage_roots_use_metadata_dir_name(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from polaris.kernelone._runtime_config import set_workspace_metadata_dir_name
        from polaris.kernelone.storage.layout import resolve_storage_roots

        workspace = tmp_path / "test_project"
        workspace.mkdir()

        set_workspace_metadata_dir_name(".custom-meta")
        try:
            roots = resolve_storage_roots(str(workspace))
            assert ".custom-meta" in roots.project_persistent_root
            assert ".custom-meta" in roots.runtime_project_root
        finally:
            set_workspace_metadata_dir_name(".polaris")
