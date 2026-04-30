"""Pytest configuration and fixtures for polaris/orchestration tests.

These fixtures ensure clean registry state between tests by resetting the
Cell-local singletons (ActivityRegistry, WorkflowRegistry, _workflow_context_var).
"""

from __future__ import annotations

import logging

import pytest

logger = logging.getLogger(__name__)


def pytest_load_initial_conftests(early_config, parser, args):
    """Patch torch._overrides._add_docstr before any test collection imports torch.

    The torch library raises RuntimeError when '_has_torch_function' already has
    a docstring (Python 3.14 compatibility issue).  pytest-cov activates tracing
    very early in collection, causing torch to be imported and raise during that
    window.  This hook fires before conftest.py files are loaded but after the
    plugin manager is ready — early enough to patch torch first.
    """
    try:
        import torch.overrides as _ov

        _orig = _ov._add_docstr

        def _safe_add_docstr(func, docstr, *args_inner, **kwargs):  # type: ignore[assignment]
            try:
                return _orig(func, docstr, *args_inner, **kwargs)
            except RuntimeError:
                pass  # already has docstring — silently swallow

        _ov._add_docstr = _safe_add_docstr
    except Exception:
        logger.debug("torch not available or already resolved — nothing to do")


@pytest.fixture(autouse=True)
def _reset_workflow_activity_singletons():
    """Reset workflow_activity Cell singletons before and after each test.

    The embedded API uses module-level singletons for the ActivityRegistry and
    WorkflowRegistry.  Without reset, test registrations accumulate and pollute
    subsequent tests in the same session. This fixture saves the original state
    and restores it after each test to prevent cross-test contamination.
    """
    # Import inside fixture to avoid import-order issues at collection time
    import polaris.cells.orchestration.workflow_activity.internal.embedded_api as _mod

    # Save original state before test
    saved_activity_registry = _mod._activity_registry
    saved_workflow_registry = _mod._workflow_registry

    # Get the ContextVar for workflow context
    from polaris.cells.orchestration.workflow_activity.internal.embedded_api import (
        _workflow_context_var,
    )

    # Clear for test
    _mod._activity_registry = None
    _mod._workflow_registry = None
    _workflow_context_var.set(None)

    yield

    # Restore original state after test
    _mod._activity_registry = saved_activity_registry
    _mod._workflow_registry = saved_workflow_registry
    _workflow_context_var.set(None)
