"""Test: pm_dispatch Cell modules can be imported without any delivery dependency.

This test verifies the architectural invariant that Cell-layer code
(polaris.cells.orchestration.pm_dispatch.internal.*) does not import
polaris.delivery.* at module load time.

Technique:
1. sys.modules is temporarily populated with a sentinel that raises ImportError
   for any polaris.delivery.* import attempt.
2. The pm_dispatch package __init__ is stubbed out (it does ``from .public import *``
   which triggers a deep import chain with pre-existing circular-import issues
   unrelated to the delivery isolation we are testing).
3. The target Cell module is imported fresh via importlib.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from collections.abc import Generator

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _DeliveryBlocker(types.ModuleType):
    """Fake package that raises ImportError for any non-dunder sub-attribute access.

    This is placed into sys.modules under all polaris.delivery.* keys so that
    any Cell module that tries to import delivery code at load time will fail
    with a clear, attributable ImportError.

    Dunder attributes (__file__, __spec__, __loader__, etc.) are passed through
    to the underlying ModuleType so that import machinery introspection (e.g.
    inspect.getmodule -> hasattr(module, '__file__')) does not accidentally
    trigger our guard.
    """

    def __getattr__(self, name: str):  # type: ignore[override]
        if name.startswith("__") and name.endswith("__"):
            # Allow import-machinery dunder access to pass through
            raise AttributeError(name)
        raise ImportError(
            f"[isolation-test] polaris.delivery accessed from Cell layer: "
            f"polaris.delivery.{name}"
        )


def _make_delivery_guard() -> dict:
    """Return a sys.modules patch dict that blocks all polaris.delivery.* imports."""
    guard = _DeliveryBlocker("polaris.delivery")
    return {
        "polaris.delivery": guard,
        "polaris.delivery.cli": guard,
        "polaris.delivery.cli.pm": guard,
        "polaris.delivery.cli.pm.config": guard,
        "polaris.delivery.cli.pm.tasks": guard,
        "polaris.delivery.cli.pm.tasks_utils": guard,
        "polaris.delivery.cli.pm.orchestration_core": guard,
        "polaris.delivery.cli.pm.report_utils": guard,
        "polaris.delivery.cli.pm.shangshuling_adapter": guard,
    }


def _evict_modules(*prefixes: str) -> dict:
    """Remove and return all sys.modules entries matching any of the prefixes."""
    evicted = {}
    for name in list(sys.modules):
        for prefix in prefixes:
            if name == prefix or name.startswith(prefix + "."):
                evicted[name] = sys.modules.pop(name)
                break
    return evicted


def _restore_modules(saved: dict) -> None:
    """Restore sys.modules from a {name: module_or_None} dict."""
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _make_stub_package(full_name: str) -> types.ModuleType:
    """Create a minimal stub module/package for full_name."""
    stub = types.ModuleType(full_name)
    stub.__path__ = []  # type: ignore[attr-defined]  # marks it as a package
    stub.__package__ = full_name
    return stub


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

#: Modules/packages to evict so they can be reimported fresh.
_CELL_PREFIXES = (
    "polaris.cells.orchestration.pm_dispatch",
)

#: The three specific target modules we care about.
TARGET_MODULES = [
    "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils",
    "polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry",
    "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline",
    "polaris.cells.orchestration.pm_dispatch.internal.iteration_state",
]

#: Packages whose __init__.py we must stub to avoid the circular-import chain
#: that already exists in the repo and is unrelated to delivery isolation.
_STUB_PACKAGES = [
    "polaris.cells.orchestration.pm_dispatch",
    "polaris.cells.orchestration.pm_dispatch.public",
    "polaris.cells.orchestration.pm_dispatch.internal",
]

#: Cell-internal modules that must be pre-loaded before other internal modules
#: import them.  Listed in dependency order (dependencies first).
_PRELOAD_MODULES = [
    "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils",
]


@pytest.fixture()
def delivery_blocked() -> Generator[None, None, None]:
    """Isolate Cell modules from delivery AND from the package __init__ chain."""
    # 1. Evict Cell modules so they reimport fresh inside this fixture.
    evicted = _evict_modules(*_CELL_PREFIXES)

    # 2. Record pre-existing states for everything we will touch.
    delivery_guard = _make_delivery_guard()
    stub_keys = _STUB_PACKAGES
    all_keys = list(delivery_guard.keys()) + list(stub_keys)
    saved = {k: sys.modules.get(k) for k in all_keys}

    # 3. Install delivery blockers.
    sys.modules.update(delivery_guard)

    # 4. Install stub packages for pm_dispatch so that importing
    #    polaris.cells.orchestration.pm_dispatch.internal.XYZ does not
    #    trigger pm_dispatch/__init__.py (which has an eager ``from .public import *``
    #    that pulls a deep import chain with pre-existing circular imports).
    for pkg in _STUB_PACKAGES:
        sys.modules[pkg] = _make_stub_package(pkg)

    # 5. Pre-load pure Cell-internal modules that other internal modules
    #    depend on at load time (e.g. iteration_state imports pm_task_utils).
    #    These must be loaded *after* stubs are in place so they also bypass
    #    the pm_dispatch/__init__ chain.
    for mod_name in _PRELOAD_MODULES:
        if mod_name not in sys.modules:
            _import_internal_module(mod_name)

    try:
        yield
    finally:
        _restore_modules(saved)
        _restore_modules({k: evicted.get(k) for k in evicted})


# ---------------------------------------------------------------------------
# Helper: import a module given its dotted name, bypassing its package __init__
# ---------------------------------------------------------------------------

def _import_internal_module(module_name: str) -> types.ModuleType:
    """Import a module directly from its .py file, bypassing package __init__."""
    # Convert dotted name to file path relative to the backend root.
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rel_path = module_name.replace(".", os.sep) + ".py"
    abs_path = os.path.join(backend_root, rel_path)

    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot build spec for {module_name} at {abs_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module_name", TARGET_MODULES)
def test_cell_module_importable_without_delivery(
    delivery_blocked: None,
    module_name: str,
) -> None:
    """Each Cell module must import successfully even when delivery is blocked.

    This proves there is no delivery import at module load time.
    """
    try:
        mod = _import_internal_module(module_name)
    except ImportError as exc:
        pytest.fail(
            f"Module '{module_name}' triggered a delivery import at load time: {exc}"
        )
    assert mod is not None, f"_import_internal_module returned None for {module_name}"


def test_pm_task_utils_no_delivery_symbol(delivery_blocked: None) -> None:
    """pm_task_utils must expose its public API without any delivery reference."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    for symbol in (
        "PM_SPIN_GUARD_STATUS",
        "normalize_task_status",
        "get_task_signature",
        "get_director_task_status_summary",
        "to_bool",
        "append_pm_report",
        "ShangshulingPort",
        "NoopShangshulingPort",
    ):
        assert hasattr(mod, symbol), (
            f"pm_task_utils is missing expected symbol '{symbol}'"
        )


def test_shangshuling_registry_port_no_delivery_symbol(delivery_blocked: None) -> None:
    """shangshuling_registry must expose its port without any delivery reference."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry"
    )
    for symbol in (
        "LocalShangshulingPort",
        "get_shangshuling_port",
    ):
        assert hasattr(mod, symbol), (
            f"shangshuling_registry is missing expected symbol '{symbol}'"
        )


def test_normalize_task_status_values(delivery_blocked: None) -> None:
    """normalize_task_status must return canonical tokens for common inputs."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    fn = mod.normalize_task_status
    assert fn("done") == "done"
    assert fn("success") == "done"
    assert fn("completed") == "done"
    assert fn("in_progress") == "in_progress"
    assert fn("doing") == "in_progress"
    assert fn("failed") == "failed"
    assert fn("blocked") == "blocked"
    assert fn("todo") == "todo"
    assert fn("pending") == "todo"
    assert fn("unknown_value") == "todo"


def test_get_task_signature_uses_id(delivery_blocked: None) -> None:
    """get_task_signature must return the first task's id when present."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    fn = mod.get_task_signature
    tasks = [{"id": "T-001", "title": "foo"}, {"id": "T-002", "title": "bar"}]
    assert fn(tasks) == "T-001"


def test_get_task_signature_falls_back_to_fingerprint(delivery_blocked: None) -> None:
    """get_task_signature falls back to 'fingerprint' when 'id' is absent."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    fn = mod.get_task_signature
    tasks = [{"fingerprint": "fp-abc", "title": "foo"}]
    assert fn(tasks) == "fp-abc"


def test_get_task_signature_empty(delivery_blocked: None) -> None:
    """get_task_signature returns empty string for empty/invalid input."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    fn = mod.get_task_signature
    assert fn([]) == ""
    assert fn(None) == ""  # type: ignore[arg-type]
    assert fn("not a list") == ""  # type: ignore[arg-type]


def test_to_bool_conversion(delivery_blocked: None) -> None:
    """to_bool must correctly handle all known string and bool values."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    fn = mod.to_bool
    assert fn(True) is True
    assert fn(False) is False
    assert fn("1") is True
    assert fn("true") is True
    assert fn("yes") is True
    assert fn("on") is True
    assert fn("0") is False
    assert fn("false") is False
    assert fn("no") is False
    assert fn("off") is False
    assert fn(None, True) is True
    assert fn(None, False) is False
    assert fn("unknown", True) is True
    assert fn("unknown", False) is False


def test_get_director_task_status_summary(delivery_blocked: None) -> None:
    """get_director_task_status_summary counts Director tasks correctly."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    fn = mod.get_director_task_status_summary
    tasks = [
        {"assigned_to": "director", "status": "done"},
        {"assigned_to": "director", "status": "in_progress"},
        {"assigned_to": "director", "status": "failed"},
        {"assigned_to": "pm", "status": "done"},  # not counted
    ]
    summary = fn(tasks)
    assert summary["total"] == 3
    assert summary["done"] == 1
    assert summary["in_progress"] == 1
    assert summary["failed"] == 1


def test_noop_shangshuling_port_safe(delivery_blocked: None) -> None:
    """NoopShangshulingPort must return safe zero/empty values and never raise."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    port = mod.NoopShangshulingPort()
    assert port.sync_tasks_to_shangshuling("/ws", []) == 0
    assert port.get_shangshuling_ready_tasks("/ws") == []
    assert port.record_shangshuling_task_completion("/ws", "T-001", True, {}) is False
    # archive_task_history should return None and not raise
    result = port.archive_task_history("/ws", "/cache", "run-1", 1, {}, None, "2026-01-01")
    assert result is None


def test_dispatch_pipeline_no_delivery_at_module_level(delivery_blocked: None) -> None:
    """dispatch_pipeline must not have imported any delivery module at load time."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline"
    )
    # Verify none of the expected public callables are missing
    for symbol in (
        "resolve_director_dispatch_tasks",
        "record_dispatch_status_to_shangshuling",
        "run_dispatch_pipeline",
        "run_engine_dispatch",
        "run_integration_qa",
        "run_post_dispatch_integration_qa",
    ):
        assert hasattr(mod, symbol), (
            f"dispatch_pipeline is missing expected public callable '{symbol}'"
        )


def test_iteration_state_no_delivery_at_module_level(delivery_blocked: None) -> None:
    """iteration_state must not have imported any delivery module at load time."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.iteration_state"
    )
    for symbol in (
        "finalize_iteration",
        "handle_invoke_error",
        "handle_spin_guard",
        "record_stop",
        "clear_manual_intervention",
    ):
        assert hasattr(mod, symbol), (
            f"iteration_state is missing expected public callable '{symbol}'"
        )


def test_pm_spin_guard_status_value(delivery_blocked: None) -> None:
    """PM_SPIN_GUARD_STATUS must be the canonical string, not the old fallback."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils"
    )
    assert mod.PM_SPIN_GUARD_STATUS == "PM_SPIN_GUARD_ACTIVE"
