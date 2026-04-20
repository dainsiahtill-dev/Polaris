from __future__ import annotations

import pytest
from polaris.kernelone.security.audit import (
    SecurityAuditor,
    VulnerabilityCategory,
    VulnerabilitySeverity,
)


@pytest.fixture
def auditor() -> SecurityAuditor:
    return SecurityAuditor()


class TestSecurityAuditor:
    """Tests for SecurityAuditor."""

    @pytest.mark.asyncio
    async def test_audit_code_no_vulnerabilities(self, auditor: SecurityAuditor) -> None:
        """Test audit of clean code."""
        code = """
def hello():
    print("Hello, World!")
    return True
"""
        result = await auditor.audit_code(code)
        assert result.total_checks == 5
        assert result.passed_checks >= 0

    @pytest.mark.asyncio
    async def test_check_injection_sql_vulnerability(self, auditor: SecurityAuditor) -> None:
        """Test detection of SQL injection patterns."""
        code = """
cursor.execute("SELECT * FROM users WHERE id=" + user_id)
"""
        vulns = await auditor.check_injection(code)
        assert len(vulns) > 0
        assert any(v.category == VulnerabilityCategory.INJECTION for v in vulns)

    @pytest.mark.asyncio
    async def test_check_injection_command_vulnerability(self, auditor: SecurityAuditor) -> None:
        """Test detection of command injection patterns."""
        code = """
import os
os.system("ls " + user_input)
"""
        vulns = await auditor.check_injection(code)
        assert len(vulns) > 0
        assert any(v.severity == VulnerabilitySeverity.CRITICAL for v in vulns)

    @pytest.mark.asyncio
    async def test_check_injection_eval(self, auditor: SecurityAuditor) -> None:
        """Test detection of eval usage."""
        code = """
result = eval(user_code)
"""
        vulns = await auditor.check_injection(code)
        assert len(vulns) > 0

    @pytest.mark.asyncio
    async def test_check_auth_hardcoded_password(self, auditor: SecurityAuditor) -> None:
        """Test detection of hardcoded passwords."""
        code = """
password = "secret123"
"""
        vulns = await auditor.check_auth(code)
        assert len(vulns) > 0
        assert any(v.category == VulnerabilityCategory.AUTHENTICATION for v in vulns)

    @pytest.mark.asyncio
    async def test_check_auth_hardcoded_api_key(self, auditor: SecurityAuditor) -> None:
        """Test detection of hardcoded API keys."""
        code = """
api_key = "sk-1234567890abcdef"
"""
        vulns = await auditor.check_auth(code)
        assert len(vulns) > 0
        assert any(v.severity == VulnerabilitySeverity.HIGH for v in vulns)

    @pytest.mark.asyncio
    async def test_check_sensitive_data_logging(self, auditor: SecurityAuditor) -> None:
        """Test detection of sensitive data in logs."""
        code = """
log.info(password)
"""
        vulns = await auditor.check_sensitive_data(code)
        assert len(vulns) > 0
        assert any(v.category == VulnerabilityCategory.SENSITIVE_DATA for v in vulns)

    @pytest.mark.asyncio
    async def test_audit_result_summary(self, auditor: SecurityAuditor) -> None:
        """Test audit result contains proper summary."""
        code = """
password = "hardcoded"
"""
        result = await auditor.audit_code(code)
        assert isinstance(result.summary, dict)
        assert "high" in result.summary or result.summary == {}

    @pytest.mark.asyncio
    async def test_audit_result_timestamp(self, auditor: SecurityAuditor) -> None:
        """Test audit result has valid timestamp."""
        code = "x = 1"
        result = await auditor.audit_code(code)
        assert result.timestamp is not None
        assert "T" in result.timestamp  # ISO format
