"""Tests for polaris.kernelone.contracts.technical.auth.

Covers AuthStrength, AuthResult, and AuthCheckerPort protocol.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.contracts.technical.auth import (
    AuthCheckerPort,
    AuthResult,
    AuthStrength,
)


class TestAuthStrength:
    def test_enum_values(self) -> None:
        assert AuthStrength.NONE == "none"
        assert AuthStrength.WEAK == "weak"
        assert AuthStrength.STRONG == "strong"

    def test_all_are_strings(self) -> None:
        for member in AuthStrength:
            assert isinstance(member.value, str)

    def test_membership_check(self) -> None:
        assert "none" in [m.value for m in AuthStrength]
        assert "weak" in [m.value for m in AuthStrength]
        assert "strong" in [m.value for m in AuthStrength]


class TestAuthResult:
    def test_denied_factory(self) -> None:
        result = AuthResult.denied()
        assert result.authenticated is False
        assert result.strength == AuthStrength.NONE
        assert result.error == "Authentication denied"
        assert result.principal == ""

    def test_denied_with_custom_error(self) -> None:
        result = AuthResult.denied("Token expired")
        assert result.error == "Token expired"
        assert result.authenticated is False

    def test_anonymous_factory(self) -> None:
        result = AuthResult.anonymous()
        assert result.authenticated is False
        assert result.strength == AuthStrength.NONE
        assert result.error == ""
        assert result.principal == ""

    def test_successful_auth(self) -> None:
        result = AuthResult(
            authenticated=True,
            strength=AuthStrength.STRONG,
            principal="user_123",
        )
        assert result.authenticated is True
        assert result.strength == AuthStrength.STRONG
        assert result.principal == "user_123"
        assert result.error == ""

    def test_weak_auth(self) -> None:
        result = AuthResult(
            authenticated=False,
            strength=AuthStrength.WEAK,
            principal="",
            error="Weak token",
        )
        assert result.strength == AuthStrength.WEAK
        assert result.authenticated is False

    def test_to_dict(self) -> None:
        result = AuthResult(
            authenticated=True,
            strength=AuthStrength.STRONG,
            principal="admin",
            error="",
        )
        d = result.to_dict()
        assert d["authenticated"] == "True"
        assert d["strength"] == "strong"
        assert d["principal"] == "admin"
        assert d["error"] == ""

    def test_frozen_immutable(self) -> None:
        result = AuthResult.denied()
        with pytest.raises(AttributeError):
            result.authenticated = True  # type: ignore[misc]


class TestAuthCheckerPortProtocol:
    def test_is_protocol(self) -> None:
        assert hasattr(AuthCheckerPort, "check")
        assert hasattr(AuthCheckerPort, "strength")

    def test_check_signature(self) -> None:
        import inspect

        sig = inspect.signature(AuthCheckerPort.check)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "token" in params

    def test_strength_signature(self) -> None:
        import inspect

        sig = inspect.signature(AuthCheckerPort.strength)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "token" in params


class TestAuthResultEquality:
    def test_same_values_are_equal(self) -> None:
        a = AuthResult.denied("error")
        b = AuthResult.denied("error")
        assert a == b

    def test_different_errors_not_equal(self) -> None:
        a = AuthResult.denied("error1")
        b = AuthResult.denied("error2")
        assert a != b
