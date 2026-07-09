"""Backend-wide pytest helpers.

The backend suite contains many ``async def`` tests, but lightweight local
verification environments do not always install ``pytest-asyncio``. This
minimal hook keeps those tests executable without making optional plugins a
collection-time dependency.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Declare async-related ini keys accepted by the backend test suite."""
    parser.addini("asyncio_mode", "Backend async test mode", default="auto")
    parser.addini(
        "asyncio_default_fixture_loop_scope",
        "Backend async fixture loop scope",
        default="function",
    )


def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Run coroutine test functions with ``asyncio.run``.

    Pytest has already resolved fixtures by this point. The hook mirrors
    pytest's normal argument filtering so plugin-added fixture values that are
    not function parameters do not leak into the coroutine call.
    """
    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None

    fixture_names = pyfuncitem._fixtureinfo.argnames
    test_arguments: dict[str, Any] = {
        name: pyfuncitem.funcargs[name] for name in fixture_names if name in pyfuncitem.funcargs
    }
    asyncio.run(test_function(**test_arguments))
    return True
