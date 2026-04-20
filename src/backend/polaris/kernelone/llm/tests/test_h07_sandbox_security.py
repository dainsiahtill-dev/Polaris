"""
Security tests for H-07: Sandbox Configuration Security Hardening

These tests verify that:
1. 'danger-full-access' is PROHIBITED in ProviderCodexConfig
2. Codex adapter uses secure defaults
3. Validation properly rejects dangerous sandbox values
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestSandboxValidatorProhibitsDangerousMode:
    """Tests verifying 'danger-full-access' is prohibited."""

    def test_valid_sandbox_values_accepted(self) -> None:
        """Valid sandbox values (safe, browser, read-only) should be accepted."""
        from polaris.kernelone.llm.config_store import ProviderCodexConfig

        for sandbox_value in ("safe", "browser", "read-only"):
            config = ProviderCodexConfig(sandbox=sandbox_value)
            assert config.sandbox == sandbox_value

    def test_danger_full_access_rejected(self) -> None:
        """'danger-full-access' MUST be rejected by validator."""
        from polaris.kernelone.llm.config_store import ProviderCodexConfig

        with pytest.raises(ValidationError) as exc_info:
            ProviderCodexConfig(sandbox="danger-full-access")

        error_message = str(exc_info.value)
        assert "PROHIBITED" in error_message or "danger-full-access" in error_message

    def test_invalid_sandbox_raises_clear_error(self) -> None:
        """Invalid sandbox values should raise clear error messages."""
        from polaris.kernelone.llm.config_store import ProviderCodexConfig

        with pytest.raises(ValidationError) as exc_info:
            ProviderCodexConfig(sandbox="invalid-sandbox-mode")

        error_message = str(exc_info.value)
        # Should contain allowed values for user guidance
        assert "safe" in error_message
        assert "browser" in error_message
        assert "read-only" in error_message

    def test_default_sandbox_is_safe(self) -> None:
        """Default sandbox value should be 'safe'."""
        from polaris.kernelone.llm.config_store import ProviderCodexConfig

        config = ProviderCodexConfig()
        assert config.sandbox == "safe"


class TestCodexAdapterSecureDefaults:
    """Tests verifying codex_adapter.py uses secure defaults."""

    def test_codex_sandbox_default_is_safe(self) -> None:
        """Codex sandbox should default to 'safe', not 'danger-full-access'."""
        import re

        # Read the codex_adapter.py source
        codex_adapter_path = (
            "C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone/process/codex_adapter.py"
        )

        with open(codex_adapter_path, encoding="utf-8") as f:
            content = f.read()

        # Find the codex_sandbox line
        sandbox_pattern = r'codex_sandbox = str\(.*?\)\.strip\(\) or "([^"]+)"'
        match = re.search(sandbox_pattern, content)

        assert match is not None, "codex_sandbox configuration not found"

        default_value = match.group(1)
        assert default_value == "safe", f"codex_sandbox should default to 'safe', not '{default_value}'"
        assert default_value != "danger-full-access", (
            "SECURITY VIOLATION: codex_sandbox should NOT default to 'danger-full-access'"
        )

    def test_codex_skip_git_check_default_is_false(self) -> None:
        """Codex skip git check should default to False (0), not True (1)."""
        import re

        codex_adapter_path = (
            "C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone/process/codex_adapter.py"
        )

        with open(codex_adapter_path, encoding="utf-8") as f:
            content = f.read()

        # Find the codex_skip_git_check line
        git_check_pattern = r'codex_skip_git_check = _env_flag\([^,]+, os\.environ\.get\([^,]+, "([^"]+)"\)\)'
        match = re.search(git_check_pattern, content)

        assert match is not None, "codex_skip_git_check configuration not found"

        default_value = match.group(1)
        assert default_value == "0", f"codex_skip_git_check should default to '0' (False), not '{default_value}'"
        assert default_value != "1", "SECURITY VIOLATION: codex_skip_git_check should NOT default to '1' (True)"


class TestValidationErrorMessages:
    """Tests for clear security-focused error messages."""

    def test_prohibited_error_message_is_clear(self) -> None:
        """Error message should clearly state 'danger-full-access' is prohibited."""
        from polaris.kernelone.llm.config_store import ProviderCodexConfig
        from pydantic import ValidationError

        try:
            ProviderCodexConfig(sandbox="danger-full-access")
            pytest.fail("Expected ValidationError was not raised")
        except ValidationError as e:
            error_str = str(e)
            # Should mention the prohibited value
            assert "danger-full-access" in error_str
            # Should mention it's not allowed
            assert "PROHIBITED" in error_str or "not" in error_str.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
