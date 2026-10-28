"""Backend tool runtime tests that keep the KernelOne default adapter real.

Background
----------
This module used to publish a ``_TestFileSystemAdapter`` double into the
process-wide KernelOne default-adapter registry
(:func:`polaris.kernelone.fs.registry.set_default_adapter`) and never remove
it.  The double diverged from the production adapter contract: the production
boundary (``KernelFileSystem``) hands ``str`` paths to the adapter, while the
double expected ``Path``.  A later test that only ran
``check_content_syntax(...)`` therefore died with
``AttributeError: 'str' object has no attribute 'parent'``.  Evidence:
``.polaris/refactor-management/full-refactor-wave2-20261027-013741/
Root-registry-pollution-repro/run-1793045845834946448/report.json``.

Why there is no isolation machinery here
----------------------------------------
The obvious repair - snapshot the registry, publish the double, restore the
snapshot afterwards - was rejected because it is not safe under concurrency: a
legitimate publication that lands between the snapshot and the publish step is
clobbered by the publish step, and the restore then writes the stale snapshot
back over the newer adapter.  Evidence:
``Root-registry-interleaving-review/result.json``.

This module therefore performs **no** registry mutation of its own.  It
consumes the real ``LocalFileSystemAdapter`` that the surrounding test
configuration already publishes, so:

* there is nothing to restore, and consequently nothing that can overwrite a
  publication made by another thread;
* production code runs against the production adapter contract instead of a
  double that silently diverges from it.

The tests below keep that invariant honest:

* the fixture must not replace the adapter that is already published;
* a publication made by another thread must survive the test;
* the syntax gate must still work after this module ran.

Tests from the previous round and why they no longer apply
-----------------------------------------------------------
The previous round added a registry state machine plus tests for it.  They
were dropped because the state machine itself is gone and there is no longer
any registry state for this module to restore:

* ``test_workspace_fixture_publishes_the_test_adapter`` - inverted into
  ``test_workspace_fixture_does_not_replace_the_default_adapter``.
* ``test_isolation_restores_the_uninitialized_registry_state``,
  ``test_isolation_restores_the_initialized_adapter_state``,
  ``test_isolation_restores_the_failed_initialization_state`` - they asserted
  transitions of the removed snapshot/restore machinery.
* ``test_isolation_keeps_the_registered_factory`` and
  ``test_isolation_restores_a_factory_that_it_installed`` - the module never
  registers a factory, so there is no factory transition to restore.
* ``test_isolation_restore_is_idempotent`` - there is no restore step left.
* ``test_isolation_refuses_to_touch_a_registry_with_an_initialization_in_flight``
  - the module never touches the registry, so it cannot race an in-flight
  initialization.
* ``test_setup_failure_after_publish_still_restores_the_registry`` - the
  fixture publishes nothing, so a failing setup cannot leave a leak behind.

The two concurrency tests of the previous round were valuable and are kept,
rewritten against the current design:
``test_isolation_keeps_an_adapter_published_by_another_thread`` and
``test_isolation_keeps_a_factory_published_by_another_thread`` became
``test_a_concurrent_adapter_publication_survives_the_fixture`` and
``test_a_concurrent_factory_publication_survives_the_fixture``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import get_default_adapter, registry as fs_registry
from polaris.kernelone.fs.contracts import KernelFileSystemAdapter
from polaris.kernelone.quality.syntax_gate import check_content_syntax
from polaris.kernelone.tool_execution.runtime_executor import BackendToolRuntime

# Upper bound for waiting on a publication made by a helper thread.
_REGISTRY_PUBLISH_TIMEOUT_SECONDS = 5.0
_REGISTRY_CHILD_ENV = "POLARIS_REGISTRY_CONCURRENCY_TEST_CHILD"


@pytest.fixture
def registry_adapter_before_workspace() -> KernelFileSystemAdapter | None:
    """The adapter published before the ``workspace`` fixture does its work.

    The registry internals are read directly on purpose: the public
    ``get_default_adapter()`` would lazily initialize an unset registry and
    therefore fabricate a state that never existed.  Reading the private
    attribute is observation-only and matches the convention already used by
    ``polaris/tests/unit/kernelone/fs/test_fs_registry.py``.
    """
    return fs_registry._default_adapter


@pytest.fixture
def workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_guard: dict[str, Any],
    registry_adapter_before_workspace: KernelFileSystemAdapter | None,
) -> Path:
    """A real on-disk workspace wired to the real KernelOne adapter.

    ``registry_adapter_before_workspace`` is requested only to fix the
    fixture order: the snapshot must be taken before this fixture runs.  This
    fixture deliberately does **not** publish an adapter - the surrounding
    test configuration already publishes the production
    ``LocalFileSystemAdapter``, and replacing it is exactly the leak this
    module used to cause.

    ``registry_guard`` is requested for its teardown order only: because
    ``workspace`` depends on it, the guard's post-teardown assertions run
    after this fixture has finished.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "nested").mkdir(parents=True, exist_ok=True)
    (workspace_root / "nested" / "hello.txt").write_text("hello\n", encoding="utf-8")

    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))

    from polaris.kernelone.tool_execution.tool_spec_registry import migrate_from_contracts_specs

    migrate_from_contracts_specs()
    return workspace_root


@pytest.fixture
def registry_guard() -> Iterator[dict[str, Any]]:
    """Assert the registry state *after* the ``workspace`` fixture tore down.

    A snapshot/restore isolation clobbers a concurrent publication during
    **teardown**, so asserting inside the test body is not enough: the
    teardown has not happened yet.

    The ordering is what makes this work.  ``workspace`` depends on this
    fixture, so pytest tears ``workspace`` down first and this fixture down
    last - the assertions below therefore observe the post-teardown registry
    state.  (The inverse dependency would tear this fixture down *first*,
    before the clobber it is meant to detect; that inversion was found by
    mutating the module back to the rejected snapshot/restore design and
    watching the guard fail to notice.)

    The yielded dict is the shared scratch space between the test body and
    the teardown assertions: the test body records the expected registry
    fields, the teardown verifies that they survived.
    """
    scratch: dict[str, Any] = {}
    yield scratch
    expected: dict[str, Any] = scratch.get("expected", {})
    for field, expected_value in expected.items():
        actual = getattr(fs_registry, field)
        assert actual is expected_value, (
            f"the registry field {field!r} was overwritten during teardown: "
            f"a concurrent publication made by another thread must survive"
        )


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


def test_default_adapter_is_the_real_local_adapter(workspace: Path) -> None:
    """Production code must run against the production adapter, not a double.

    A test double is the only thing this module used to publish, so observing
    the real adapter proves that nothing replaced the published adapter.
    """
    assert isinstance(get_default_adapter(), LocalFileSystemAdapter)


def test_workspace_fixture_does_not_replace_the_default_adapter(
    workspace: Path,
    registry_adapter_before_workspace: KernelFileSystemAdapter | None,
) -> None:
    """The fixture must leave the already published adapter untouched."""
    assert registry_adapter_before_workspace is not None
    assert fs_registry._default_adapter is registry_adapter_before_workspace


def test_factory_publication_does_not_leak_to_other_tests(tmp_path: Path) -> None:
    """A passing concurrency test must leave the enclosing pytest process clean."""
    probe = """
import sys
import pytest
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import registry

def sentinel():
    return LocalFileSystemAdapter()

registry.set_adapter_factory(sentinel)
status = pytest.main([sys.argv[1], '-q', '-p', 'no:cacheprovider', '--basetemp', sys.argv[2]])
assert status == 0
assert registry._adapter_factory is sentinel, 'concurrency test leaked its factory'
"""
    node = f"{__file__}::test_a_concurrent_factory_publication_survives_the_fixture"
    result = subprocess.run(
        [sys.executable, "-c", probe, node, str(tmp_path / "nested-pytest")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _run_registry_test_in_child(name: str, workspace: Path) -> bool:
    """Run registry-mutating assertions in a process owned by this test."""
    if os.environ.get(_REGISTRY_CHILD_ENV) == name:
        return False
    env = os.environ.copy()
    env[_REGISTRY_CHILD_ENV] = name
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"{__file__}::{name}",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(workspace.parent / "registry-child"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return True


def test_a_concurrent_adapter_publication_survives_the_fixture(
    workspace: Path,
    registry_guard: dict[str, Any],
) -> None:
    """A publication made by another thread must not be overwritten.

    This is the guard against re-introducing a snapshot/restore isolation:
    such an isolation restores its stale snapshot on teardown and would
    silently drop the adapter published here.  The assertion runs after the
    ``workspace`` fixture tore down, which is where that clobber happens.
    """
    if _run_registry_test_in_child("test_a_concurrent_adapter_publication_survives_the_fixture", workspace):
        return
    published = threading.Event()
    published_adapter = LocalFileSystemAdapter()

    def publish() -> None:
        fs_registry.set_default_adapter(published_adapter)
        published.set()

    worker = threading.Thread(target=publish, name="kernelone-registry-adapter-publisher")
    worker.start()
    try:
        assert published.wait(timeout=_REGISTRY_PUBLISH_TIMEOUT_SECONDS)
    finally:
        worker.join(timeout=_REGISTRY_PUBLISH_TIMEOUT_SECONDS)

    assert fs_registry._default_adapter is published_adapter
    registry_guard["expected"] = {"_default_adapter": published_adapter}


def test_a_concurrent_factory_publication_survives_the_fixture(
    workspace: Path,
    registry_guard: dict[str, Any],
) -> None:
    """A factory published by another thread must not be overwritten.

    The adapter and the factory are two independent registry fields, so both
    need the same "no stale restore" guarantee.
    """
    if _run_registry_test_in_child("test_a_concurrent_factory_publication_survives_the_fixture", workspace):
        return

    def published_factory() -> KernelFileSystemAdapter:
        return LocalFileSystemAdapter()

    published = threading.Event()

    def publish() -> None:
        fs_registry.set_adapter_factory(published_factory)
        published.set()

    worker = threading.Thread(target=publish, name="kernelone-registry-factory-publisher")
    worker.start()
    try:
        assert published.wait(timeout=_REGISTRY_PUBLISH_TIMEOUT_SECONDS)
    finally:
        worker.join(timeout=_REGISTRY_PUBLISH_TIMEOUT_SECONDS)

    assert fs_registry._adapter_factory is published_factory
    registry_guard["expected"] = {"_adapter_factory": published_factory}


@pytest.mark.skipif(shutil.which("gofmt") is None, reason="gofmt not available")
def test_syntax_gate_is_not_polluted_by_the_test_adapter(workspace: Path) -> None:
    """The syntax gate must still work after this module ran.

    The gate writes its candidate file through ``KernelFileSystem``, which
    passes ``str`` paths to the adapter.  A leaked test double turned that
    into ``AttributeError: 'str' object has no attribute 'parent'``; with the
    real adapter the gate reaches its checker and reports the real result.
    """
    result = check_content_syntax("engine/rules.go", "package engine\n\nfunc cast() {}na < cost {\n")

    assert result.path == "engine/rules.go"
    assert result.checked is True
    assert result.ok is False
