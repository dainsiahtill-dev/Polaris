"""Backend tool runtime tests with exact KernelOne registry isolation.

The ``workspace`` fixture publishes a test filesystem adapter in the process
wide KernelOne default-adapter registry.  Without an exact restore that fake
adapter leaks into every later test of the same session, and production code
then runs against a test double: the Go syntax gate died with
``AttributeError: 'str' object has no attribute 'parent'`` because the
production contract passes ``str`` paths while the test double expects
``Path``.  Evidence:
``.polaris/refactor-management/full-refactor-wave2-20261027-013741/
Root-registry-pollution-repro/run-1793045845834946448/report.json``.

``_IsolatedDefaultAdapter`` snapshots the complete observable registry state,
publishes the fake adapter, and restores the snapshot afterwards - including
the registered factory, the "never initialized" state and the "lazy
initialization failed" state, none of which the public registry API can
express.  The restore is a compare-and-swap: an adapter or factory published
by another thread after the install is kept instead of being overwritten.

The snapshot/restore writes the registry's own module state under the
registry's own lock.  It is deliberately test-local: the production registry
is not modified, because the public API is missing the transitions this
isolation has to restore (see ``_MISSING_PUBLIC_TRANSITIONS``).
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import pytest
from polaris.kernelone.fs import registry as fs_registry
from polaris.kernelone.fs.contracts import KernelFileSystemAdapter
from polaris.kernelone.fs.registry import set_adapter_factory, set_default_adapter
from polaris.kernelone.ports.storage import IFileSystemAdapterFactory
from polaris.kernelone.quality.syntax_gate import check_content_syntax
from polaris.kernelone.tool_execution.runtime_executor import BackendToolRuntime

logger = logging.getLogger(__name__)

# Upper bound for waiting on a concurrently running lazy initialization.
_REGISTRY_QUIESCE_TIMEOUT_SECONDS = 5.0

# Registry fields this isolation observes and restores.  The public API
# (``set_default_adapter`` / ``reset_default_adapter`` / ``set_adapter_factory``)
# cannot express every state the isolation has to restore:
#
#   * "never initialized"          -> reset_default_adapter() + factory kept
#   * "initialized successfully"   -> set_default_adapter(adapter)
#   * "lazy initialization failed" -> NOT expressible: both setters clear the
#                                     recorded error, and a failing factory is
#                                     masked by the migration fallback adapter
#   * "no factory registered"      -> NOT expressible: set_adapter_factory()
#                                     rejects None
#
# Because the failed state and the factory removal are unreachable through
# the public API, the snapshot reads the registry's own module state.  The
# names are asserted up front so a production refactor fails loudly instead
# of silently restoring the wrong state.
_REQUIRED_REGISTRY_FIELDS = (
    "_default_adapter",
    "_adapter_factory",
    "_initialization_attempted",
    "_initialization_error",
    "_initialization_in_progress",
    "_state_changed",
)

_MISSING_PUBLIC_TRANSITIONS = (
    "failed lazy initialization state (no public API clears/republishes the recorded error)",
    "factory removal (set_adapter_factory rejects None)",
)


class _TestFileSystemAdapter(KernelFileSystemAdapter):
    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        return len(content.encode(encoding))

    def write_bytes(self, path: Path, content: bytes) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return len(content)

    def append_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding=encoding) as handle:
            handle.write(content)
        return len(content.encode(encoding))

    def exists(self, path: str | Path) -> bool:
        return Path(path).exists()

    def is_file(self, path: str | Path) -> bool:
        return Path(path).is_file()

    def is_dir(self, path: str | Path) -> bool:
        return Path(path).is_dir()

    def remove(self, path: Path, *, missing_ok: bool = True) -> bool:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            if missing_ok:
                return False
            raise


@dataclass(frozen=True)
class _RegistryState:
    """Observable state of the KernelOne default-adapter registry.

    ``default_adapter is None`` together with ``initialization_attempted is
    False`` is the "never initialized" state; an adapter with
    ``initialization_attempted is True`` is the "initialized successfully"
    state; an ``initialization_error`` with ``initialization_attempted is
    True`` is the "lazy initialization failed" state.  ``adapter_factory`` is
    the factory bootstrap registered for lazy creation.
    """

    default_adapter: KernelFileSystemAdapter | None
    adapter_factory: IFileSystemAdapterFactory | None
    initialization_attempted: bool
    initialization_error: Exception | None


def _require_registry_fields() -> None:
    """Fail loudly when the observed registry internals moved."""
    missing = [name for name in _REQUIRED_REGISTRY_FIELDS if not hasattr(fs_registry, name)]
    if missing:
        raise RuntimeError(
            "KernelOne fs registry internals changed; the test isolation must be "
            f"updated. Missing fields: {', '.join(missing)}"
        )


def _capture_registry_state() -> _RegistryState:
    """Snapshot the registry without triggering a lazy initialization.

    ``get_default_adapter()`` cannot be used here: it initializes an unset
    registry, which would fabricate an initial state that never existed.
    """
    _require_registry_fields()
    with fs_registry._state_changed:
        return _RegistryState(
            default_adapter=fs_registry._default_adapter,
            adapter_factory=fs_registry._adapter_factory,
            initialization_attempted=fs_registry._initialization_attempted,
            initialization_error=fs_registry._initialization_error,
        )


def _wait_for_quiescent_registry(timeout_seconds: float) -> bool:
    """Wait until no lazy initialization is in flight.

    Returns ``False`` when the timeout expires.  The caller then refuses to
    touch the registry instead of racing an initialization that is still
    publishing its result.
    """
    deadline = time.monotonic() + timeout_seconds
    with fs_registry._state_changed:
        while fs_registry._initialization_in_progress:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            fs_registry._state_changed.wait(remaining)
    return True


def _restore_registry_state(
    snapshot: _RegistryState,
    *,
    installed_adapter: KernelFileSystemAdapter | None,
    installed_factory: IFileSystemAdapterFactory | None,
) -> None:
    """Restore ``snapshot`` without overwriting concurrent legitimate updates.

    The default-adapter group (adapter plus initialization outcome) is
    restored only while it still holds the adapter this isolation published.
    A different adapter means another thread published a legitimate update,
    and that update is kept.  The factory is restored under the same rule.
    """
    with fs_registry._state_changed:
        if fs_registry._default_adapter is installed_adapter:
            fs_registry._default_adapter = snapshot.default_adapter
            fs_registry._initialization_attempted = snapshot.initialization_attempted
            fs_registry._initialization_error = snapshot.initialization_error
        elif installed_adapter is not None:
            logger.warning(
                "KernelOne default adapter changed while the isolated test ran; "
                "keeping the concurrently published adapter."
            )
        if installed_factory is not None and fs_registry._adapter_factory is installed_factory:
            fs_registry._adapter_factory = snapshot.adapter_factory
        fs_registry._state_changed.notify_all()


def _force_restore_registry_state(snapshot: _RegistryState) -> None:
    """Restore ``snapshot`` unconditionally (test-owned cleanup only).

    This is used by the tests themselves; it is not part of the isolation
    contract, which never overwrites a concurrent legitimate update.
    """
    with fs_registry._state_changed:
        fs_registry._default_adapter = snapshot.default_adapter
        fs_registry._adapter_factory = snapshot.adapter_factory
        fs_registry._initialization_attempted = snapshot.initialization_attempted
        fs_registry._initialization_error = snapshot.initialization_error
        fs_registry._state_changed.notify_all()


def _publish_failed_initialization_state(error: Exception) -> None:
    """Publish the registry state a failed lazy initialization leaves behind.

    The public API cannot reach this state: a failing factory is masked by
    the migration fallback adapter, so the recorded error is written
    directly.  The isolation must be able to restore it.
    """
    with fs_registry._state_changed:
        fs_registry._default_adapter = None
        fs_registry._initialization_attempted = True
        fs_registry._initialization_error = error
        fs_registry._state_changed.notify_all()


@contextmanager
def _owned_registry_state() -> Iterator[_RegistryState]:
    """Restore the registry state captured on entry (test-owned cleanup)."""
    snapshot = _capture_registry_state()
    try:
        yield snapshot
    finally:
        _force_restore_registry_state(snapshot)


class _IsolatedDefaultAdapter:
    """Publish a test adapter and restore the exact prior registry state.

    The isolation is a compare-and-swap on the fields it changed, so an
    adapter or factory that another thread publishes after the install is
    never overwritten by the restore.
    """

    def __init__(
        self,
        adapter: KernelFileSystemAdapter,
        *,
        factory: IFileSystemAdapterFactory | None = None,
    ) -> None:
        self._adapter = adapter
        self._factory = factory
        self._snapshot: _RegistryState | None = None
        self._restored = False

    def install(self, *, timeout_seconds: float = _REGISTRY_QUIESCE_TIMEOUT_SECONDS) -> None:
        """Snapshot the registry and publish the test adapter."""
        if not _wait_for_quiescent_registry(timeout_seconds):
            raise RuntimeError(
                "KernelOne default adapter initialization did not quiesce; "
                "refusing to touch the registry."
            )
        self._snapshot = _capture_registry_state()
        if self._factory is not None:
            set_adapter_factory(self._factory)
        set_default_adapter(self._adapter)

    def restore(self) -> None:
        """Restore the captured state; safe to call more than once."""
        if self._restored:
            return
        self._restored = True
        snapshot = self._snapshot
        if snapshot is None:
            return
        _restore_registry_state(
            snapshot,
            installed_adapter=self._adapter,
            installed_factory=self._factory,
        )

    def __enter__(self) -> _IsolatedDefaultAdapter:
        self.install()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.restore()


@pytest.fixture(autouse=True)
def _registry_leak_guard() -> Iterator[None]:
    """Fail a test that leaves the test double in the global registry.

    A lazy initialization triggered by production code may legitimately turn
    an uninitialized registry into an initialized one, so the guard rejects
    only a leaked *test double* and not that transition.
    """
    yield
    leaked = _capture_registry_state().default_adapter
    assert not isinstance(leaked, _TestFileSystemAdapter), (
        "the test filesystem adapter leaked into the KernelOne default-adapter registry"
    )


@pytest.fixture
def failing_isolated_workspace(request: pytest.FixtureRequest) -> Path:
    """Fixture whose setup fails *after* the adapter was published.

    It reproduces the hazard the ``workspace`` fixture has to survive: a
    setup step that raises after the registry was already mutated.
    """
    isolation = _IsolatedDefaultAdapter(_TestFileSystemAdapter())
    isolation.install()
    request.addfinalizer(isolation.restore)
    raise RuntimeError("setup failed after the adapter was published")


@pytest.fixture
def workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Path:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "nested").mkdir(parents=True, exist_ok=True)
    (workspace_root / "nested" / "hello.txt").write_text("hello\n", encoding="utf-8")

    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))

    isolation = _IsolatedDefaultAdapter(_TestFileSystemAdapter())
    isolation.install()
    # The restore is registered as a finalizer *before* the remaining setup
    # work, so a failure in that setup still cleans the registry up.
    request.addfinalizer(isolation.restore)

    from polaris.kernelone.tool_execution.tool_spec_registry import migrate_from_contracts_specs

    migrate_from_contracts_specs()
    return workspace_root


def test_backend_tool_runtime_exposes_kernelone_standard_tools(workspace: Path) -> None:
    runtime = BackendToolRuntime(str(workspace))

    tools = runtime.list_tools()

    assert "list_directory" in tools
    assert "read_file" in tools
    assert "write_file" in tools


def test_backend_tool_runtime_invokes_direct_executor_with_cwd(workspace: Path) -> None:
    runtime = BackendToolRuntime(str(workspace))

    result = runtime.invoke("list_directory", {"path": ".", "cwd": "nested"})

    assert result["ok"] is True
    payload = result["result"]
    assert payload["path"] == "."
    assert any(entry["name"] == "hello.txt" for entry in payload["entries"])


def test_workspace_fixture_publishes_the_test_adapter(workspace: Path) -> None:
    assert isinstance(fs_registry._default_adapter, _TestFileSystemAdapter)


def test_isolation_restores_the_uninitialized_registry_state() -> None:
    with _owned_registry_state():
        fs_registry.reset_default_adapter()
        before = _capture_registry_state()
        assert before.default_adapter is None
        assert before.initialization_attempted is False
        assert before.initialization_error is None

        with _IsolatedDefaultAdapter(_TestFileSystemAdapter()):
            assert isinstance(fs_registry._default_adapter, _TestFileSystemAdapter)

        after = _capture_registry_state()
        assert after.default_adapter is None
        assert after.initialization_attempted is False
        assert after.initialization_error is None
        # The restored state is a real "never initialized" registry: the lazy
        # initialization path is still available and not poisoned by a
        # fabricated error.
        assert fs_registry.get_default_adapter() is not None


def test_isolation_restores_the_initialized_adapter_state() -> None:
    original = _TestFileSystemAdapter()
    with _owned_registry_state():
        set_default_adapter(original)
        before = _capture_registry_state()
        assert before.default_adapter is original

        with _IsolatedDefaultAdapter(_TestFileSystemAdapter()):
            assert fs_registry._default_adapter is not original

        after = _capture_registry_state()
        assert after.default_adapter is original
        assert after.initialization_attempted is True
        assert after.initialization_error is None


def test_isolation_restores_the_failed_initialization_state() -> None:
    failure = RuntimeError("lazy initialization failed")
    with _owned_registry_state():
        _publish_failed_initialization_state(failure)
        assert _capture_registry_state().initialization_error is failure

        with _IsolatedDefaultAdapter(_TestFileSystemAdapter()):
            assert isinstance(fs_registry._default_adapter, _TestFileSystemAdapter)

        after = _capture_registry_state()
        assert after.default_adapter is None
        assert after.initialization_attempted is True
        assert after.initialization_error is failure


def test_isolation_keeps_the_registered_factory() -> None:
    def factory() -> KernelFileSystemAdapter:
        return _TestFileSystemAdapter()

    with _owned_registry_state():
        set_adapter_factory(factory)

        with _IsolatedDefaultAdapter(_TestFileSystemAdapter()):
            assert isinstance(fs_registry._default_adapter, _TestFileSystemAdapter)

        assert _capture_registry_state().adapter_factory is factory


def test_isolation_restores_a_factory_that_it_installed() -> None:
    def original_factory() -> KernelFileSystemAdapter:
        return _TestFileSystemAdapter()

    def replacement_factory() -> KernelFileSystemAdapter:
        return _TestFileSystemAdapter()

    with _owned_registry_state():
        set_adapter_factory(original_factory)

        with _IsolatedDefaultAdapter(_TestFileSystemAdapter(), factory=replacement_factory):
            assert _capture_registry_state().adapter_factory is replacement_factory

        assert _capture_registry_state().adapter_factory is original_factory


def test_isolation_restore_is_idempotent() -> None:
    with _owned_registry_state():
        before = _capture_registry_state()
        isolation = _IsolatedDefaultAdapter(_TestFileSystemAdapter())
        isolation.install()
        isolation.restore()
        isolation.restore()

        assert _capture_registry_state() == before


def test_isolation_keeps_an_adapter_published_by_another_thread() -> None:
    with _owned_registry_state():
        start = threading.Event()
        published = threading.Event()
        published_adapters: list[KernelFileSystemAdapter] = []

        def publish() -> None:
            start.wait(timeout=_REGISTRY_QUIESCE_TIMEOUT_SECONDS)
            adapter = _TestFileSystemAdapter()
            published_adapters.append(adapter)
            set_default_adapter(adapter)
            published.set()

        worker = threading.Thread(target=publish, name="kernelone-registry-publisher")
        isolation = _IsolatedDefaultAdapter(_TestFileSystemAdapter())
        isolation.install()
        worker.start()
        try:
            start.set()
            assert published.wait(timeout=_REGISTRY_QUIESCE_TIMEOUT_SECONDS)
        finally:
            worker.join(timeout=_REGISTRY_QUIESCE_TIMEOUT_SECONDS)

        isolation.restore()

        assert fs_registry._default_adapter is published_adapters[0]
        assert fs_registry._default_adapter is not isolation._adapter


def test_isolation_keeps_a_factory_published_by_another_thread() -> None:
    def installed_factory() -> KernelFileSystemAdapter:
        return _TestFileSystemAdapter()

    with _owned_registry_state():
        start = threading.Event()
        published = threading.Event()
        published_factories: list[IFileSystemAdapterFactory] = []

        def publish() -> None:
            start.wait(timeout=_REGISTRY_QUIESCE_TIMEOUT_SECONDS)

            def factory() -> KernelFileSystemAdapter:
                return _TestFileSystemAdapter()

            published_factories.append(factory)
            set_adapter_factory(factory)
            published.set()

        worker = threading.Thread(target=publish, name="kernelone-registry-factory-publisher")
        isolation = _IsolatedDefaultAdapter(_TestFileSystemAdapter(), factory=installed_factory)
        isolation.install()
        worker.start()
        try:
            start.set()
            assert published.wait(timeout=_REGISTRY_QUIESCE_TIMEOUT_SECONDS)
        finally:
            worker.join(timeout=_REGISTRY_QUIESCE_TIMEOUT_SECONDS)

        isolation.restore()

        assert fs_registry._adapter_factory is published_factories[0]


def test_isolation_refuses_to_touch_a_registry_with_an_initialization_in_flight() -> None:
    with _owned_registry_state():
        before = _capture_registry_state()
        with fs_registry._state_changed:
            fs_registry._initialization_in_progress = True
            fs_registry._initialization_owner = threading.get_ident()
            fs_registry._state_changed.notify_all()
        try:
            isolation = _IsolatedDefaultAdapter(_TestFileSystemAdapter())
            with pytest.raises(RuntimeError, match="did not quiesce"):
                isolation.install(timeout_seconds=0.05)
            assert _capture_registry_state() == before
        finally:
            with fs_registry._state_changed:
                fs_registry._initialization_in_progress = False
                fs_registry._initialization_owner = None
                fs_registry._state_changed.notify_all()


def test_setup_failure_after_publish_still_restores_the_registry(
    request: pytest.FixtureRequest,
) -> None:
    with pytest.raises(RuntimeError, match="setup failed after the adapter was published"):
        request.getfixturevalue("failing_isolated_workspace")

    # The adapter was published before the setup failed, so the leak is
    # still visible here ...
    assert isinstance(fs_registry._default_adapter, _TestFileSystemAdapter)
    # ... and the finalizer registered by the failing fixture removes it
    # again.  The autouse ``_registry_leak_guard`` asserts that removal
    # after the test body finished.


@pytest.mark.skipif(shutil.which("gofmt") is None, reason="gofmt not available")
def test_syntax_gate_is_not_polluted_by_the_test_adapter() -> None:
    with _owned_registry_state():
        with _IsolatedDefaultAdapter(_TestFileSystemAdapter()):
            pass

        assert not isinstance(fs_registry._default_adapter, _TestFileSystemAdapter)

        result = check_content_syntax("engine/rules.go", "package engine\n\nfunc cast() {}na < cost {\n")

        assert result.path == "engine/rules.go"
        assert result.checked is True
        assert result.ok is False
