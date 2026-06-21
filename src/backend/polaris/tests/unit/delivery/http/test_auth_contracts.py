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


# ── Auth class: dev fallback tests ────────────────────────────────────────────


class TestAuthDevFallback:
    """Verify the allow_dev_fallback parameter on Auth."""

    def test_exact_token_accepted(self) -> None:
        auth = Auth("my-secret-token", allow_dev_fallback=True)
        assert auth.check("Bearer my-secret-token") is True

    def test_wrong_token_rejected(self) -> None:
        auth = Auth("my-secret-token", allow_dev_fallback=True)
        assert auth.check("Bearer wrong-token") is False

    def test_dev_fallback_accepted_when_enabled(self) -> None:
        auth = Auth("auto-generated-token", allow_dev_fallback=True)
        assert auth.check("Bearer polaris-local-dev") is True

    def test_dev_fallback_rejected_when_disabled(self) -> None:
        auth = Auth("auto-generated-token", allow_dev_fallback=False)
        assert auth.check("Bearer polaris-local-dev") is False

    def test_dev_fallback_rejected_when_token_empty(self) -> None:
        auth = Auth("", allow_dev_fallback=True)
        # Empty token means auth is not configured at all
        assert auth.check("Bearer polaris-local-dev") is False

    def test_empty_header_rejected(self) -> None:
        auth = Auth("token", allow_dev_fallback=True)
        assert auth.check("") is False

    def test_no_bearer_prefix_rejected(self) -> None:
        auth = Auth("token", allow_dev_fallback=True)
        assert auth.check("token") is False

    def test_dev_fallback_default_off(self) -> None:
        auth = Auth("token")
        assert auth.check("Bearer polaris-local-dev") is False
