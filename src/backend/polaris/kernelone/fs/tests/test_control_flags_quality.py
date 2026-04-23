"""Tests for polaris.kernelone.fs.control_flags — registry, stop/pause signals."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from polaris.kernelone.fs.control_flags import (
    _normalize_signal_name,
    _validate_control_logical_path,
    clear_stop_flag,
    clear_stop_flag_for,
    list_stop_signals,
    pause_requested,
    register_stop_signal,
    stop_flag_path_for,
    stop_requested,
    stop_requested_for,
    unregister_stop_signal,
)

# ---------------------------------------------------------------------------
# _normalize_signal_name
# ---------------------------------------------------------------------------


class TestNormalizeSignalName:
    def test_valid_lowercase(self) -> None:
        assert _normalize_signal_name("stop") == "stop"

    def test_uppercase_is_lowercased(self) -> None:
        assert _normalize_signal_name("STOP") == "stop"

    def test_valid_with_dots_and_hyphens(self) -> None:
        assert _normalize_signal_name("my.stop-flag") == "my.stop-flag"

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid stop signal name"):
            _normalize_signal_name("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid stop signal name"):
            _normalize_signal_name("   ")

    def test_special_chars_raise(self) -> None:
        with pytest.raises(ValueError, match="Invalid stop signal name"):
            _normalize_signal_name("stop/flag")

    def test_starts_with_hyphen_raises(self) -> None:
        with pytest.raises(ValueError):
            _normalize_signal_name("-bad")


# ---------------------------------------------------------------------------
# _validate_control_logical_path
# ---------------------------------------------------------------------------


class TestValidateControlLogicalPath:
    def test_valid_runtime_control_path(self) -> None:
        result = _validate_control_logical_path("runtime/control/stop.flag")
        assert result == "runtime/control/stop.flag"

    def test_bare_runtime_control_is_valid(self) -> None:
        result = _validate_control_logical_path("runtime/control")
        assert result == "runtime/control"

    def test_non_control_path_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported control flag path"):
            _validate_control_logical_path("runtime/events/x.jsonl")

    def test_workspace_path_raises(self) -> None:
        with pytest.raises(ValueError):
            _validate_control_logical_path("workspace/meta/stop.flag")


# ---------------------------------------------------------------------------
# register_stop_signal / unregister_stop_signal
# ---------------------------------------------------------------------------


class TestSignalRegistry:
    def test_register_returns_logical_path(self) -> None:
        path = register_stop_signal("test_signal_abc")
        assert "test_signal_abc" in path
        assert path.startswith("runtime/control/")
        # cleanup
        unregister_stop_signal("test_signal_abc")

    def test_register_custom_path(self) -> None:
        path = register_stop_signal("test_custom_xyz", "runtime/control/custom.xyz.flag")
        assert path == "runtime/control/custom.xyz.flag"
        unregister_stop_signal("test_custom_xyz")

    def test_list_shows_registered(self) -> None:
        register_stop_signal("list_test_sig")
        signals = list_stop_signals()
        assert "list_test_sig" in signals
        unregister_stop_signal("list_test_sig")

    def test_unregister_default_signal_fails(self) -> None:
        # Default signals cannot be unregistered
        assert unregister_stop_signal("stop") is False
        assert unregister_stop_signal("pm") is False
        assert unregister_stop_signal("director") is False

    def test_unregister_custom_signal_succeeds(self) -> None:
        register_stop_signal("temp_unregister_sig")
        result = unregister_stop_signal("temp_unregister_sig")
        assert result is True
        assert "temp_unregister_sig" not in list_stop_signals()

    def test_unregister_nonexistent_returns_false(self) -> None:
        assert unregister_stop_signal("nonexistent_xyz_123") is False


# ---------------------------------------------------------------------------
# stop_requested_for / clear_stop_flag_for — filesystem interaction
# ---------------------------------------------------------------------------


class TestStopFlagFilesystem:
    def test_no_flag_file_means_not_requested(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime"))
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
        workspace = str(tmp_path / "workspace")
        os.makedirs(workspace, exist_ok=True)
        assert stop_requested_for(workspace, "stop") is False

    def test_flag_file_present_means_requested(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime_root = tmp_path / "runtime"
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
        workspace = str(tmp_path / "workspace")
        os.makedirs(workspace, exist_ok=True)

        flag_path = stop_flag_path_for(workspace, "stop")
        os.makedirs(os.path.dirname(flag_path), exist_ok=True)
        Path(flag_path).write_text("stop", encoding="utf-8")

        assert stop_requested_for(workspace, "stop") is True

    def test_clear_stop_flag_removes_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime_root = tmp_path / "runtime"
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
        workspace = str(tmp_path / "workspace")
        os.makedirs(workspace, exist_ok=True)

        flag_path = stop_flag_path_for(workspace, "stop")
        os.makedirs(os.path.dirname(flag_path), exist_ok=True)
        Path(flag_path).write_text("stop", encoding="utf-8")

        assert stop_requested_for(workspace, "stop") is True
        clear_stop_flag_for(workspace, "stop")
        assert stop_requested_for(workspace, "stop") is False

    def test_clear_stop_flag_noop_when_not_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime"))
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
        workspace = str(tmp_path / "workspace")
        os.makedirs(workspace, exist_ok=True)
        # Must not raise even when flag file doesn't exist
        clear_stop_flag_for(workspace, "stop")


# ---------------------------------------------------------------------------
# Legacy aliases
# ---------------------------------------------------------------------------


def test_legacy_stop_requested_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    # Legacy alias routes to "pm" signal
    assert stop_requested(workspace) is False
    clear_stop_flag(workspace)  # must not raise


# ---------------------------------------------------------------------------
# pause_requested
# ---------------------------------------------------------------------------


def test_pause_requested_false_when_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    assert pause_requested(workspace) is False


# ---------------------------------------------------------------------------
# Thread safety of registry
# ---------------------------------------------------------------------------


def test_registry_concurrent_register_unregister_is_safe() -> None:
    errors: list[Exception] = []
    barrier = threading.Barrier(20)

    def worker(idx: int) -> None:
        try:
            barrier.wait()
            name = f"thread_sig_{idx}"
            register_stop_signal(name)
            _ = list_stop_signals()
            unregister_stop_signal(name)
        except (RuntimeError, ValueError) as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Concurrent registry operations raised: {errors}"
