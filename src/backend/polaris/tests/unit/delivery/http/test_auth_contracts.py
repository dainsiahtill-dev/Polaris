"""HTTP authentication contract tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from polaris.cells.runtime.state_owner.public.service import Auth
from polaris.delivery.http import dependencies
from polaris.delivery.http.routers import _shared


def test_router_shared_auth_uses_canonical_dependency() -> None:
    """Routers using _shared must get the same auth semantics as v2 routers."""
    assert _shared.require_auth is dependencies.require_auth


def test_require_auth_binds_auth_context_on_success() -> None:
    request = MagicMock()
    request.app.state.auth = Auth("token")
    request.headers.get.return_value = "Bearer token"
    request.state = MagicMock()

    dependencies.require_auth(request)

    assert request.state.auth_context.principal == "authenticated"
    assert request.state.auth_context.has_scope("*") is True


def test_require_auth_fails_closed_when_not_initialized() -> None:
    request = MagicMock()
    request.app.state.auth = None
    request.headers.get.return_value = ""

    with pytest.raises(HTTPException) as exc_info:
        dependencies.require_auth(request)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "auth not initialized"
