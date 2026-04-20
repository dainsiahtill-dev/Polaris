"""Test fixtures for fs tests.

Provides test isolation for tests that use importlib.reload() or otherwise
modify module-level global state.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def preserve_fs_registry_state():
    """Preserve and restore fs registry global state around each test.

    Tests that call importlib.reload() on the fs registry module (e.g.
    test_fs_registry_set_then_get_roundtrip) corrupt the global state for
    subsequent tests in the same session. This fixture saves _default_adapter,
    _initialization_attempted, and _initialization_in_progress before each test
    and restores them after, ensuring test isolation.

    See: https://github.com/anthropics/claude-code/issues/... (pre-existing
    isolation bug where importlib.reload resets module globals across the session).
    """
    from polaris.kernelone.fs import registry as reg_mod

    saved_adapter = reg_mod._default_adapter
    saved_attempted = reg_mod._initialization_attempted

    yield

    reg_mod._default_adapter = saved_adapter
    reg_mod._initialization_attempted = saved_attempted
