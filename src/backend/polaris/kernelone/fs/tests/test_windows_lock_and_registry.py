"""Tests for Windows stale lock detection and fs/registry thread safety."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch


def _block_psutil_import(name, *args, **kwargs):
    """Custom import that blocks psutil from being imported."""
    if name == "psutil":
        raise ImportError("psutil not available")
    return original_import(name, *args, **kwargs)


original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__


# ─── Fix 3: Windows stale lock ────────────────────────────────────────────────


def test_pid_alive_windows_with_psutil_alive() -> None:
    """On Windows, _pid_alive() must use psutil.pid_exists() and return True."""
    with patch("platform.system", return_value="Windows"):
        mock_psutil = MagicMock()
        mock_psutil.pid_exists.return_value = True
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            # Reload the function from a fresh import to pick up the mock.
            import importlib

            import polaris.kernelone.fs.jsonl.locking as locking_mod

            importlib.reload(locking_mod)
            result = locking_mod._pid_alive(1234)
            mock_psutil.pid_exists.assert_called_once_with(1234)
            assert result is True


def test_pid_alive_windows_with_psutil_dead() -> None:
    """On Windows, _pid_alive() must use psutil.pid_exists() and return False."""
    with patch("platform.system", return_value="Windows"):
        mock_psutil = MagicMock()
        mock_psutil.pid_exists.return_value = False
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            import importlib

            import polaris.kernelone.fs.jsonl.locking as locking_mod

            importlib.reload(locking_mod)
            result = locking_mod._pid_alive(9999)
            assert result is False


def test_pid_alive_windows_no_psutil_is_conservative() -> None:
    """On Windows without psutil, _pid_alive() must return True (conservative)."""
    with (
        patch("platform.system", return_value="Windows"),
        patch("builtins.__import__", side_effect=_block_psutil_import),
    ):
        import importlib

        import polaris.kernelone.fs.jsonl.locking as locking_mod

        importlib.reload(locking_mod)
        result = locking_mod._pid_alive(5678)
        assert result is True, "_pid_alive() must return True (conservative) when psutil is unavailable on Windows"


def test_pid_alive_zero_or_negative() -> None:
    """_pid_alive() must return False for pid <= 0 on any platform."""
    import polaris.kernelone.fs.jsonl.locking as locking_mod

    assert locking_mod._pid_alive(0) is False
    assert locking_mod._pid_alive(-1) is False


def test_pid_alive_posix_process_lookup_error() -> None:
    """On POSIX, ProcessLookupError means process is dead → False."""
    with patch("platform.system", return_value="Linux"), patch("os.kill", side_effect=ProcessLookupError):
        import importlib

        import polaris.kernelone.fs.jsonl.locking as locking_mod

        importlib.reload(locking_mod)
        result = locking_mod._pid_alive(1234)
        assert result is False


def test_pid_alive_posix_permission_error() -> None:
    """On POSIX, PermissionError means process is alive → True."""
    with patch("platform.system", return_value="Linux"), patch("os.kill", side_effect=PermissionError):
        import importlib

        import polaris.kernelone.fs.jsonl.locking as locking_mod

        importlib.reload(locking_mod)
        result = locking_mod._pid_alive(1234)
        assert result is True


# ─── Fix 4: fs/registry thread safety ─────────────────────────────────────────


def test_fs_registry_thread_safety() -> None:
    """Concurrent adapter registration must not produce KeyError or data races."""
    # Reset registry state before test
    import importlib

    from polaris.kernelone.fs import registry as reg_mod

    importlib.reload(reg_mod)

    errors: list[Exception] = []
    adapters_seen: list[object] = []

    class FakeAdapter:
        def __init__(self, tag: str) -> None:
            self.tag = tag

    def writer(tag: str) -> None:
        try:
            adapter = FakeAdapter(tag)
            reg_mod.set_default_adapter(adapter)  # type: ignore[arg-type]
        except (RuntimeError, ValueError) as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            # get_default_adapter may raise RuntimeError if not yet set — that's ok.
            try:
                a = reg_mod.get_default_adapter()
                adapters_seen.append(a)
            except RuntimeError:
                adapters_seen.append(None)  # Not yet set; record as None
        except (RuntimeError, ValueError) as exc:
            errors.append(exc)

    threads = []
    for i in range(10):
        threads.append(threading.Thread(target=writer, args=(f"writer-{i}",)))
        threads.append(threading.Thread(target=reader))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, f"Thread safety errors: {errors}"

    # Final state must be a valid FakeAdapter
    final = reg_mod.get_default_adapter()
    assert hasattr(final, "tag"), "Final adapter must be a FakeAdapter with a tag"


def test_fs_registry_get_before_set_returns_lazy_default() -> None:
    """get_default_adapter() must return a lazy-initialized default when no adapter is set.

    This tests the lazy initialization behavior: when no adapter is explicitly set,
    get_default_adapter() automatically injects a LocalFileSystemAdapter as the default.
    """
    import importlib

    from polaris.kernelone.fs import registry as reg_mod

    importlib.reload(reg_mod)

    # When no adapter is set, get_default_adapter() should auto-inject a default
    adapter = reg_mod.get_default_adapter()
    assert adapter is not None
    # The lazy-initialized adapter should be the LocalFileSystemAdapter
    from polaris.infrastructure.storage import LocalFileSystemAdapter

    assert isinstance(adapter, LocalFileSystemAdapter)


def test_fs_registry_get_after_failed_lazy_init_raises_runtime_error() -> None:
    """When lazy initialization fails, get_default_adapter() must raise RuntimeError.

    Note: This test verifies the behavior by checking the error message
    when lazy initialization cannot complete. In practice, the LocalFileSystemAdapter
    is always available, so this path is rarely hit.
    """
    import importlib

    from polaris.kernelone.fs import registry as reg_mod

    importlib.reload(reg_mod)

    # Simulate lazy init failure by directly setting _initialization_attempted
    # without actually setting _default_adapter
    reg_mod._initialization_attempted = True
    reg_mod._default_adapter = None

    import pytest

    with pytest.raises(RuntimeError, match="not set"):
        reg_mod.get_default_adapter()


def test_fs_registry_set_then_get_roundtrip() -> None:
    """set_default_adapter() followed by get_default_adapter() must return the same object."""
    import importlib

    from polaris.kernelone.fs import registry as reg_mod

    importlib.reload(reg_mod)

    class StubAdapter:
        pass

    stub = StubAdapter()
    reg_mod.set_default_adapter(stub)  # type: ignore[arg-type]
    result = reg_mod.get_default_adapter()
    assert result is stub
