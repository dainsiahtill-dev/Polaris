"""Test: pm_planning Cell modules can be imported without any delivery dependency.

This test verifies the architectural invariant that Cell-layer code
(polaris.cells.orchestration.pm_planning.internal.*) does not import
polaris.delivery.* at module load time.

Technique:
1. sys.modules is temporarily populated with a sentinel that raises ImportError
   for any polaris.delivery.* import attempt.
2. The pm_planning package __init__ is stubbed out (it does ``from .public import *``
   which may trigger a deep import chain with pre-existing circular-import issues
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
        "polaris.delivery.cli.pm.backend": guard,
        "polaris.delivery.cli.pm.polaris_engine": guard,
        "polaris.delivery.cli.pm.utils": guard,
        "polaris.delivery.cli.pm.pipeline_adapter": guard,
        "polaris.delivery.cli.pm.orchestration_core": guard,
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
    """Create a minimal stub module/package for full_name.

    Sets __path__ so that sub-modules can still be found by import machinery.
    """
    stub = types.ModuleType(full_name)
    # Determine the directory where this package's .py files live.
    # We compute it relative to the backend root.
    test_dir = os.path.dirname(os.path.abspath(__file__))  # tests/
    backend_root = os.path.dirname(test_dir)  # src/backend/
    pkg_path = full_name.replace(".", os.sep)  # e.g. polaris/cells/orchestration/pm_planning/internal
    pkg_dir = os.path.join(backend_root, pkg_path)
    stub.__path__ = [pkg_dir]  # type: ignore[attr-defined]
    stub.__package__ = full_name
    return stub


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

#: Modules/packages to evict so they can be reimported fresh.
_CELL_PREFIXES = (
    "polaris.cells.orchestration.pm_planning",
)

#: The target module we care about.
TARGET_MODULES = [
    "polaris.cells.orchestration.pm_planning.internal.pipeline_ports",
    "polaris.cells.orchestration.pm_planning.pipeline",
]

#: Packages whose __init__.py we must stub to avoid the circular-import chain
#: that already exists in the repo and is unrelated to delivery isolation.
_STUB_PACKAGES = [
    "polaris.cells.orchestration.pm_planning",
    "polaris.cells.orchestration.pm_planning.public",
    "polaris.cells.orchestration.pm_planning.internal",
    "polaris.cells.orchestration",
]

#: Cell-internal modules that must be pre-loaded before other internal modules
#: import them.  Listed in dependency order (dependencies first).
#: pipeline.py top-level imports shared_quality; shared_quality imports task_quality_gate.
#: pipeline_ports is also a dependency of pipeline.py.
_PRELOAD_MODULES = [
    "polaris.cells.orchestration.pm_planning.internal.task_quality_gate",
    "polaris.cells.orchestration.pm_planning.internal.shared_quality",
    "polaris.cells.orchestration.pm_planning.internal.pipeline_ports",
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

    # 4. Install stub packages for pm_planning so that importing
    #    polaris.cells.orchestration.pm_planning.internal.XYZ does not
    #    trigger pm_planning/__init__.py (which has an eager ``from .public import *``
    #    that pulls a deep import chain with pre-existing circular imports).
    for pkg in _STUB_PACKAGES:
        sys.modules[pkg] = _make_stub_package(pkg)

    # 5. Pre-load pure Cell-internal modules that other internal modules
    #    depend on at load time (e.g. pipeline_ports is imported by pipeline).
    #    These must be loaded *after* stubs are in place so they also bypass
    #    the pm_planning/__init__ chain.
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
    # The test file is at: tests/test_pm_planning_no_delivery_import.py
    # Backend root is the parent of tests/ (i.e., src/backend/)
    test_dir = os.path.dirname(os.path.abspath(__file__))  # tests/
    backend_root = os.path.dirname(test_dir)  # src/backend/
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


def test_pipeline_ports_no_delivery_symbol(delivery_blocked: None) -> None:
    """pipeline_ports must expose its public API without any delivery reference."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports"
    )
    for symbol in (
        "PmInvokeBackendPort",
        "PmStatePort",
        "NoopPmInvokePort",
        "NoopPmStatePort",
        "normalize_engine_config",
        "normalize_priority",
        "normalize_pm_payload",
        "normalize_path_list",
        "_migrate_tasks_in_place",
        "_looks_like_tool_call_output",
        "collect_schema_warnings",
        "_extract_json_from_llm_output",
        "get_pm_invoke_port",
        "get_pm_state_port",
    ):
        assert hasattr(mod, symbol), (
            f"pipeline_ports is missing expected symbol '{symbol}'"
        )


def test_pipeline_no_delivery_at_module_level(delivery_blocked: None) -> None:
    """pipeline must not have imported any delivery module at load time."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.pipeline"
    )
    for symbol in (
        "run_pm_planning_iteration",
        "_should_promote_pm_quality_candidate",
        "_build_pm_quality_retry_prompt",
        "_build_pm_json_retry_prompt",
        "_merge_engine_config",
        "_pick_task_scope_hint",
        "_handle_invoke_error",
    ):
        assert hasattr(mod, symbol), (
            f"pipeline is missing expected public callable '{symbol}'"
        )


def test_normalize_priority_values(delivery_blocked: None) -> None:
    """normalize_priority must return canonical integer values."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports"
    )
    fn = mod.normalize_priority
    assert fn("high") == 1
    assert fn("normal") == 5
    assert fn("low") == 9
    assert fn("urgent") == 0
    assert fn(3) == 3
    assert fn("invalid") == 5  # fallback


def test_looks_like_tool_call_output(delivery_blocked: None) -> None:
    """_looks_like_tool_call_output detects tool-call markers."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports"
    )
    fn = mod._looks_like_tool_call_output
    assert fn("[tool_call]do something[/tool_call]") is True
    assert fn("<tool_call>action</tool_call>") is True
    assert fn("Hello world, this is normal text.") is False
    assert fn("") is False


def test_migrate_tasks_in_place_basic(delivery_blocked: None) -> None:
    """_migrate_tasks_in_place must add backlog_ref and fix failed status."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports"
    )
    fn = mod._migrate_tasks_in_place

    payload = {
        "tasks": [
            {"title": "task1"},
            {"status": "failed", "error_code": None},
            {"status": "blocked"},
        ]
    }
    fn(payload)
    assert payload["tasks"][0]["backlog_ref"] == ""
    assert "error_code" in payload["tasks"][1]
    assert "error_code" in payload["tasks"][2]


def test_normalize_engine_config(delivery_blocked: None) -> None:
    """normalize_engine_config must extract execution mode settings."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports"
    )
    fn = mod.normalize_engine_config
    assert fn({}) == {}
    result = fn({"director_execution_mode": "multi", "max_directors": 4})
    assert result["director_execution_mode"] == "multi"
    assert result["max_directors"] == 4


def test_extract_json_from_llm_output(delivery_blocked: None) -> None:
    """_extract_json_from_llm_output must parse JSON from raw LLM output."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports"
    )
    fn = mod._extract_json_from_llm_output

    # Simple JSON
    assert fn('{"tasks": [], "overall_goal": "test"}') == {
        "tasks": [],
        "overall_goal": "test",
    }

    # JSON in markdown fence
    assert fn('```json\n{"tasks": []}\n```') == {"tasks": []}

    # JSON with extra text
    result = fn('Here is the plan:\n{"tasks": [], "focus": "done"}\nThanks!')
    assert result is not None
    assert result["focus"] == "done"

    # None for invalid input
    assert fn("") is None
    assert fn("just plain text") is None


def test_noop_pm_invoke_port_stub(delivery_blocked: None) -> None:
    """NoopPmInvokePort must raise on invoke() when delivery is not available."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports"
    )
    port = mod.NoopPmInvokePort()
    with pytest.raises(RuntimeError, match="NoopPmInvokePort.invoke called"):
        port.invoke(state=None, prompt="test", backend_kind="auto", args=None, usage_ctx=None)


def test_noop_pm_state_port_properties(delivery_blocked: None) -> None:
    """NoopPmStatePort must return safe empty values for all properties."""
    mod = _import_internal_module(
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports"
    )
    port = mod.NoopPmStatePort()
    assert port.workspace_full == ""
    assert port.timeout == 0
    assert port.events_full == ""
    assert port.show_output is False
