"""Dynamic process handles must satisfy the declared PID result type."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from polaris.kernelone.process.async_contracts import PopenAsyncHandle


@pytest.mark.parametrize("value", [0, 1, 12345])
def test_integer_pid_is_preserved_without_conversion(value: int) -> None:
    handle = PopenAsyncHandle(SimpleNamespace(pid=value), 5, None, datetime.now(timezone.utc))
    assert handle.pid == value
    assert type(handle.pid) is int


@pytest.mark.parametrize("value", [True, False, "12345", 12345.0, None])
def test_invalid_dynamic_pid_is_rejected_before_it_can_be_published(value: object) -> None:
    handle = PopenAsyncHandle(SimpleNamespace(pid=value), 5, None, datetime.now(timezone.utc))
    with pytest.raises(TypeError, match="process pid must be an integer"):
        _ = handle.pid
