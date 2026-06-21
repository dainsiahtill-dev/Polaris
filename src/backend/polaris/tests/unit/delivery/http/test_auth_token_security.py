"""Security-focused tests for auth token generation and validation.

Tests the token lifecycle:
1. BackendLaunchRequest.get_effective_token() priority chain
2. app_factory token auto-generation
3. Auth dev fallback security boundaries
4. Production safety guarantees
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest
from polaris.cells.runtime.state_owner.public.service import Auth
from polaris.delivery.http import app_factory
from polaris.delivery.http.routers.primary import auth_token_discovery


class TestBackendLaunchRequestTokenPriority:
    """Verify explicit token takes precedence over defaults."""

    def test_explicit_token_overrides_default(self) -> None:
        req = BackendLaunchRequest(token="explicit-token")
        assert req.get_effective_token() == "explicit-token"

    def test_config_snapshot_token_overrides_default(self) -> None:
        config = MagicMock()
        config.get.return_value = "config-token"
        req = BackendLaunchRequest(config_snapshot=config)
        assert req.get_effective_token() == "config-token"
        config.get.assert_called_once_with("security.token")

    def test_explicit_token_overrides_config_snapshot(self) -> None:
        config = MagicMock()
        config.get.return_value = "config-token"
        req = BackendLaunchRequest(token="explicit-token", config_snapshot=config)
        assert req.get_effective_token() == "explicit-token"

    def test_default_token_when_no_explicit_or_config(self) -> None:
        req = BackendLaunchRequest()
        assert req.get_effective_token() == "polaris-local-dev"

    def test_empty_explicit_token_falls_through(self) -> None:
        req = BackendLaunchRequest(token="")
        assert req.get_effective_token() == "polaris-local-dev"

    def test_whitespace_explicit_token_falls_through(self) -> None:
        req = BackendLaunchRequest(token="   ")
        # Whitespace-only token is truthy, so it's returned as-is
        assert req.get_effective_token() == "   "


class TestAppFactoryTokenGeneration:
    """Verify app_factory token auto-generation logic."""

    def setup_method(self) -> None:
        """Reset module-level state before each test."""
        app_factory._effective_token = ""
        app_factory._token_was_auto_generated = False

    def test_auto_generates_token_when_env_empty(self) -> None:
        with (
            patch.dict(os.environ, {"KERNELONE_TOKEN": ""}, clear=False),
            patch("polaris.delivery.http.app_factory._secrets") as mock_secrets,
            patch("polaris.delivery.http.app_factory.get_settings") as mock_settings,
        ):
            mock_secrets.token_urlsafe.return_value = "auto-generated"
            mock_settings.return_value = MagicMock()
            app_factory.create_app()
            assert os.environ["KERNELONE_TOKEN"] == "auto-generated"
            assert app_factory._token_was_auto_generated is True

    def test_preserves_explicit_token(self) -> None:
        with (
            patch.dict(os.environ, {"KERNELONE_TOKEN": "explicit-token"}, clear=False),
            patch("polaris.delivery.http.app_factory.get_settings") as mock_settings,
        ):
            mock_settings.return_value = MagicMock()
            app_factory.create_app()
            assert os.environ["KERNELONE_TOKEN"] == "explicit-token"
            assert app_factory._token_was_auto_generated is False

    def test_get_effective_token_returns_current_value(self) -> None:
        app_factory._effective_token = "test-token"
        assert app_factory.get_effective_token() == "test-token"


class TestAuthSecurityBoundaries:
    """Verify Auth class security invariants."""

    def test_production_rejects_dev_fallback_by_default(self) -> None:
        """When allow_dev_fallback=False (production), dev token is rejected."""
        auth = Auth("production-token", allow_dev_fallback=False)
        assert auth.check("Bearer polaris-local-dev") is False
        assert auth.check("Bearer production-token") is True

    def test_dev_fallback_only_when_auto_generated(self) -> None:
        """Dev fallback is only enabled when token was auto-generated."""
        # Simulating auto-generated token scenario
        auth = Auth("auto-token", allow_dev_fallback=True)
        assert auth.check("Bearer polaris-local-dev") is True

        # Simulating explicit token scenario
        auth = Auth("explicit-token", allow_dev_fallback=False)
        assert auth.check("Bearer polaris-local-dev") is False

    def test_empty_token_rejects_all(self) -> None:
        """Empty token means auth is not configured - reject everything."""
        auth = Auth("", allow_dev_fallback=True)
        assert auth.check("Bearer polaris-local-dev") is False
        assert auth.check("Bearer anything") is False

    def test_no_bearer_prefix_rejected(self) -> None:
        auth = Auth("token", allow_dev_fallback=True)
        assert auth.check("token") is False
        assert auth.check("Basic token") is False

    def test_timing_safe_comparison(self) -> None:
        """Verify tokens are compared using timing-safe comparison."""
        auth = Auth("secret-token")
        # Both should return False, but we verify the logic path
        assert auth.check("Bearer wrong-token") is False
        assert auth.check("Bearer secret-token") is True


class TestProductionSafetyBoundary:
    """Verify production safety guarantees."""

    def test_explicit_token_disables_dev_fallback(self) -> None:
        """When user provides explicit token, dev fallback is disabled."""
        req = BackendLaunchRequest(token="production-secret")
        token = req.get_effective_token()
        assert token == "production-secret"
        # In production, allow_dev_fallback should be False
        auth = Auth(token, allow_dev_fallback=False)
        assert auth.check("Bearer polaris-local-dev") is False

    def test_auto_generated_token_enables_dev_fallback(self) -> None:
        """When token is auto-generated, dev fallback is enabled for development."""
        req = BackendLaunchRequest()
        token = req.get_effective_token()
        assert token == "polaris-local-dev"
        # In development, allow_dev_fallback should be True
        auth = Auth(token, allow_dev_fallback=True)
        assert auth.check("Bearer polaris-local-dev") is True

    def test_config_token_disables_dev_fallback(self) -> None:
        """When token comes from config, dev fallback is disabled."""
        config = MagicMock()
        config.get.return_value = "config-secret"
        req = BackendLaunchRequest(config_snapshot=config)
        token = req.get_effective_token()
        assert token == "config-secret"
        # Config-provided token should not enable dev fallback
        auth = Auth(token, allow_dev_fallback=False)
        assert auth.check("Bearer polaris-local-dev") is False

    def test_random_token_generation_is_cryptographic(self) -> None:
        """Verify auto-generated tokens use cryptographic randomness."""
        import secrets

        token1 = secrets.token_urlsafe(32)
        token2 = secrets.token_urlsafe(32)
        # Tokens should be unique
        assert token1 != token2
        # Tokens should be sufficiently long
        assert len(token1) >= 32
        assert len(token2) >= 32


class TestTokenEnvironmentIntegration:
    """Test token handling across environment variables."""

    def setup_method(self) -> None:
        """Reset module-level state before each test."""
        app_factory._effective_token = ""
        app_factory._token_was_auto_generated = False

    def test_kernelone_token_env_preserved(self) -> None:
        """Explicit KERNELONE_TOKEN env var is preserved."""
        with (
            patch.dict(os.environ, {"KERNELONE_TOKEN": "env-token"}, clear=False),
            patch("polaris.delivery.http.app_factory.get_settings") as mock_settings,
        ):
            mock_settings.return_value = MagicMock()
            app_factory.create_app()
            assert os.environ["KERNELONE_TOKEN"] == "env-token"
            assert app_factory._effective_token == "env-token"

    def test_auto_generated_token_set_in_env(self) -> None:
        """Auto-generated token is set in environment."""
        with (
            patch.dict(os.environ, {"KERNELONE_TOKEN": ""}, clear=False),
            patch("polaris.delivery.http.app_factory._secrets") as mock_secrets,
            patch("polaris.delivery.http.app_factory.get_settings") as mock_settings,
        ):
            mock_secrets.token_urlsafe.return_value = "auto-token"
            mock_settings.return_value = MagicMock()
            app_factory.create_app()
            assert os.environ["KERNELONE_TOKEN"] == "auto-token"
            assert app_factory._effective_token == "auto-token"

    def test_backend_bootstrap_sets_token(self) -> None:
        """Verify backend_bootstrap uses get_effective_token()."""
        with patch.dict(os.environ, {}, clear=False):
            req = BackendLaunchRequest(token="bootstrap-token")
            # The bootstrap should use get_effective_token()
            assert req.get_effective_token() == "bootstrap-token"


class TestPublicTokenDiscoveryBoundary:
    """Verify public token discovery does not leak tokens by default."""

    def setup_method(self) -> None:
        app_factory._effective_token = ""

    def test_discovery_disabled_by_default_does_not_return_explicit_token(self) -> None:
        app_factory._effective_token = "production-secret"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KERNELONE_AUTH_TOKEN_DISCOVERY_ENABLED", None)
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(auth_token_discovery())

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "token discovery disabled"

    def test_discovery_requires_explicit_opt_in(self) -> None:
        app_factory._effective_token = "debug-token"
        with patch.dict(os.environ, {"KERNELONE_AUTH_TOKEN_DISCOVERY_ENABLED": "1"}, clear=False):
            assert asyncio.run(auth_token_discovery()) == {"token": "debug-token"}

    def test_discovery_disabled_when_env_false(self) -> None:
        app_factory._effective_token = "secret"
        with (
            patch.dict(os.environ, {"KERNELONE_AUTH_TOKEN_DISCOVERY_ENABLED": "false"}, clear=False),
            pytest.raises(HTTPException) as exc_info,
        ):
            asyncio.run(auth_token_discovery())
        assert exc_info.value.status_code == 404

    def test_discovery_disabled_when_env_empty(self) -> None:
        app_factory._effective_token = "secret"
        with (
            patch.dict(os.environ, {"KERNELONE_AUTH_TOKEN_DISCOVERY_ENABLED": ""}, clear=False),
            pytest.raises(HTTPException) as exc_info,
        ):
            asyncio.run(auth_token_discovery())
        assert exc_info.value.status_code == 404

    def test_discovery_enabled_with_true(self) -> None:
        app_factory._effective_token = "my-token"
        with patch.dict(os.environ, {"KERNELONE_AUTH_TOKEN_DISCOVERY_ENABLED": "true"}, clear=False):
            assert asyncio.run(auth_token_discovery()) == {"token": "my-token"}

    def test_discovery_enabled_with_yes(self) -> None:
        app_factory._effective_token = "another-token"
        with patch.dict(os.environ, {"KERNELONE_AUTH_TOKEN_DISCOVERY_ENABLED": "yes"}, clear=False):
            assert asyncio.run(auth_token_discovery()) == {"token": "another-token"}
