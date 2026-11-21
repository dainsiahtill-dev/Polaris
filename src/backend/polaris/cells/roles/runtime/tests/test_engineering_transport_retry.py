from __future__ import annotations

import http.client

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import (
    _TRANSIENT_PROVIDER_EXCEPTIONS,
    _provider_transport_message,
)


def test_incomplete_read_is_a_transient_provider_exception() -> None:
    assert http.client.IncompleteRead in _TRANSIENT_PROVIDER_EXCEPTIONS


def test_transport_message_requires_continuation_without_replay() -> None:
    message = _provider_transport_message("IncompleteRead(14049 bytes read)")
    assert message["role"] == "user"
    assert "IncompleteRead" in message["content"]
    assert "before any tool from this turn ran" in message["content"]


@pytest.mark.parametrize("error", [http.client.IncompleteRead(10), ConnectionResetError("reset")])
def test_transient_exceptions_are_classified(error: Exception) -> None:
    assert isinstance(error, _TRANSIENT_PROVIDER_EXCEPTIONS)
