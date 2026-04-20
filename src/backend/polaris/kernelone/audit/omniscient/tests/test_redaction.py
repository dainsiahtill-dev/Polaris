"""Tests for SensitiveFieldRedactor."""

from __future__ import annotations

from polaris.kernelone.audit.omniscient.redaction import (
    REDACTED_PLACEHOLDER,
    SensitiveFieldRedactor,
    get_default_redactor,
    redact_sensitive_data,
    reset_default_redactor,
)


class TestSensitiveFieldRedactor:
    """Tests for SensitiveFieldRedactor."""

    def test_redact_password(self) -> None:
        """Test password field redaction."""
        redactor = SensitiveFieldRedactor()
        result = redactor.redact({"username": "john", "password": "secret123"})
        assert result["username"] == "john"
        assert result["password"] == REDACTED_PLACEHOLDER

    def test_redact_nested_password(self) -> None:
        """Test redaction in nested dictionaries."""
        redactor = SensitiveFieldRedactor()
        result = redactor.redact(
            {
                "config": {
                    "api_key": "abc123",
                    "endpoint": "https://api.example.com",
                }
            }
        )
        assert result["config"]["api_key"] == REDACTED_PLACEHOLDER
        assert result["config"]["endpoint"] == "https://api.example.com"

    def test_redact_list_of_dicts(self) -> None:
        """Test redaction in lists of dictionaries."""
        redactor = SensitiveFieldRedactor()
        result = redactor.redact(
            [
                {"name": "item1", "token": "tok1"},
                {"name": "item2", "token": "tok2"},
            ]
        )
        assert result[0]["name"] == "item1"
        assert result[0]["token"] == REDACTED_PLACEHOLDER
        assert result[1]["name"] == "item2"
        assert result[1]["token"] == REDACTED_PLACEHOLDER

    def test_redact_case_insensitive(self) -> None:
        """Test redaction is case-insensitive."""
        redactor = SensitiveFieldRedactor()
        result = redactor.redact(
            {
                "PASSWORD": "secret",
                "API_KEY": "key123",
                "Authorization": "bearer xyz",
            }
        )
        assert result["PASSWORD"] == REDACTED_PLACEHOLDER
        assert result["API_KEY"] == REDACTED_PLACEHOLDER
        assert result["Authorization"] == REDACTED_PLACEHOLDER

    def test_preserves_non_sensitive_fields(self) -> None:
        """Test that non-sensitive fields are preserved."""
        redactor = SensitiveFieldRedactor()
        result = redactor.redact(
            {
                "name": "John",
                "age": 30,
                "active": True,
                "score": 98.5,
                "tags": ["admin", "user"],
            }
        )
        assert result["name"] == "John"
        assert result["age"] == 30
        assert result["active"] is True
        assert result["score"] == 98.5
        assert result["tags"] == ["admin", "user"]

    def test_preserves_structure(self) -> None:
        """Test that structure is preserved (same keys, same indices)."""
        redactor = SensitiveFieldRedactor()
        original = {
            "safe1": "value1",
            "secret": "hidden",
            "safe2": "value2",
        }
        result = redactor.redact(original)
        assert set(result.keys()) == {"safe1", "secret", "safe2"}
        # Original unchanged
        assert original["secret"] == "hidden"
        assert result["secret"] == REDACTED_PLACEHOLDER

    def test_preserves_primitives(self) -> None:
        """Test that primitive values are returned as-is."""
        redactor = SensitiveFieldRedactor()
        assert redactor.redact("plain string") == "plain string"
        assert redactor.redact(42) == 42
        assert redactor.redact(3.14) == 3.14
        assert redactor.redact(True) is True
        assert redactor.redact(None) is None

    def test_redact_token_string_values(self) -> None:
        """Test that strings that look like tokens are redacted."""
        redactor = SensitiveFieldRedactor()

        # Bearer token
        assert redactor.redact("bearer eyJhbGciOiJIUzI1NiJ9") == REDACTED_PLACEHOLDER

        # Token prefix
        assert redactor.redact("token abc123def456") == REDACTED_PLACEHOLDER

        # Authorization header
        assert redactor.redact("authorization: Bearer xyz") == REDACTED_PLACEHOLDER

    def test_redact_long_hex_strings(self) -> None:
        """Test that long hex strings are treated as tokens."""
        redactor = SensitiveFieldRedactor()

        # SHA256 hash (64 chars hex)
        result = redactor.redact("a" * 64)
        assert result == REDACTED_PLACEHOLDER

        # Short hex is NOT redacted
        result = redactor.redact("deadbeef")
        assert result == "deadbeef"

    def test_redact_jwt_tokens(self) -> None:
        """Test JWT token detection and redaction."""
        redactor = SensitiveFieldRedactor()

        # Valid JWT format (three base64 sections)
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = redactor.redact(jwt)
        assert result == REDACTED_PLACEHOLDER

        # Invalid JWT (only 2 sections) not redacted
        result = redactor.redact("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0")
        assert result == "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"

    def test_redact_base64_strings(self) -> None:
        """Test base64 string detection."""
        redactor = SensitiveFieldRedactor()

        # Long base64 string (>32 chars with valid padding)
        result = redactor.redact("SGVsbG8gV29ybGQhIFRoaXMgaXMgYSB0ZXN0")
        assert result == REDACTED_PLACEHOLDER

        # Short base64 not redacted
        result = redactor.redact("SGVsbG8=")
        assert result == "SGVsbG8="

    def test_extra_sensitive_patterns(self) -> None:
        """Test extra sensitive patterns can be added."""
        redactor = SensitiveFieldRedactor(extra_sensitive_patterns=["my_secret", "custom_field"])
        result = redactor.redact(
            {
                "my_secret": "hidden",
                "custom_field": "also hidden",
                "name": "John",
            }
        )
        assert result["my_secret"] == REDACTED_PLACEHOLDER
        assert result["custom_field"] == REDACTED_PLACEHOLDER
        assert result["name"] == "John"

    def test_custom_placeholder(self) -> None:
        """Test custom placeholder text."""
        redactor = SensitiveFieldRedactor(placeholder="[HIDDEN]")
        result = redactor.redact({"password": "secret"})
        assert result["password"] == "[HIDDEN]"
        assert redactor.placeholder == "[HIDDEN]"


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def test_redact_sensitive_data(self) -> None:
        """Test convenience function."""
        result = redact_sensitive_data({"password": "secret", "name": "John"})
        assert result["password"] == REDACTED_PLACEHOLDER
        assert result["name"] == "John"

    def test_get_default_redactor(self) -> None:
        """Test default redactor is shared."""
        redactor1 = get_default_redactor()
        redactor2 = get_default_redactor()
        assert redactor1 is redactor2

    def test_reset_default_redactor(self) -> None:
        """Test reset clears the singleton."""
        r1 = get_default_redactor()
        reset_default_redactor()
        r2 = get_default_redactor()
        assert r1 is not r2


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_dict(self) -> None:
        """Test empty dict handling."""
        redactor = SensitiveFieldRedactor()
        assert redactor.redact({}) == {}

    def test_empty_list(self) -> None:
        """Test empty list handling."""
        redactor = SensitiveFieldRedactor()
        assert redactor.redact([]) == []

    def test_empty_string(self) -> None:
        """Test empty string handling."""
        redactor = SensitiveFieldRedactor()
        assert redactor.redact("") == ""

    def test_deeply_nested(self) -> None:
        """Test deeply nested structures."""
        redactor = SensitiveFieldRedactor()
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "api_key": "secret",
                        "data": {
                            "token": "tok123",
                        },
                    }
                }
            }
        }
        result = redactor.redact(data)
        assert result["level1"]["level2"]["level3"]["api_key"] == REDACTED_PLACEHOLDER
        assert result["level1"]["level2"]["level3"]["data"]["token"] == REDACTED_PLACEHOLDER

    def test_mixed_nested_list_dict(self) -> None:
        """Test mixed list and dict nesting."""
        redactor = SensitiveFieldRedactor()
        data = {
            "users": [
                {
                    "credentials": {
                        "key": "secret1",
                    },
                    "name": "alice",
                },
                {
                    "credentials": {
                        "key": "secret2",
                    },
                    "name": "bob",
                },
            ]
        }
        result = redactor.redact(data)
        # "credentials" is in the sensitive list, so the whole value is redacted
        assert result["users"][0]["credentials"] == REDACTED_PLACEHOLDER
        assert result["users"][0]["name"] == "alice"
        assert result["users"][1]["credentials"] == REDACTED_PLACEHOLDER
