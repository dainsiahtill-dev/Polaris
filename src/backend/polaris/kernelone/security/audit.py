from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VulnerabilitySeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class VulnerabilityCategory(str, Enum):
    INJECTION = "injection"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    SENSITIVE_DATA = "sensitive_data"
    CRYPTOGRAPHY = "cryptography"
    CONFIGURATION = "configuration"


@dataclass(frozen=True)
class Vulnerability:
    """A discovered vulnerability."""

    id: str
    title: str
    description: str
    severity: VulnerabilitySeverity
    category: VulnerabilityCategory
    location: str
    remediation: str
    references: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SecurityAuditResult:
    """Result of a security audit."""

    timestamp: str
    duration_ms: float
    total_checks: int
    passed_checks: int
    summary: dict[str, int]  # severity -> count
    vulnerabilities: tuple[Vulnerability, ...] = field(default_factory=tuple)


class SecurityAuditor:
    """Security auditor for code and configuration."""

    def __init__(self) -> None:
        self._vulnerability_counter = 0

    async def audit_code(
        self,
        code: str,
        language: str = "python",
    ) -> SecurityAuditResult:
        """Audit code for security vulnerabilities."""
        import time

        start = time.perf_counter()

        all_vulnerabilities: list[Vulnerability] = []

        # Run all security checks
        all_vulnerabilities.extend(await self.check_injection(code))
        all_vulnerabilities.extend(await self.check_auth(code))
        all_vulnerabilities.extend(await self.check_sensitive_data(code))

        duration_ms = (time.perf_counter() - start) * 1000
        total_checks = 5
        passed_checks = total_checks - len(all_vulnerabilities)

        summary: dict[str, int] = {}
        for v in all_vulnerabilities:
            severity_key = v.severity.value
            summary[severity_key] = summary.get(severity_key, 0) + 1

        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat()

        return SecurityAuditResult(
            timestamp=timestamp,
            duration_ms=duration_ms,
            vulnerabilities=tuple(all_vulnerabilities),
            total_checks=total_checks,
            passed_checks=max(0, passed_checks),
            summary=summary,
        )

    async def check_injection(self, code: str) -> list[Vulnerability]:
        """Check for injection vulnerabilities (SQL, command, etc.)."""
        vulnerabilities: list[Vulnerability] = []
        lines = code.split("\n")

        # Check for SQL injection patterns
        sql_dangerous = [
            r'execute\s*\(\s*["\'].*\%s',
            r"cursor\.execute\s*\([^)]*\+[^)]*\)",
            r"SELECT.*\+.*FROM",
            r"INSERT.*\+.*VALUES",
            r"db\.execute\s*\([^)]*\+[^)]*\)",
        ]
        for i, line in enumerate(lines):
            for pattern in sql_dangerous:
                import re

                if re.search(pattern, line, re.IGNORECASE):
                    self._vulnerability_counter += 1
                    vulnerabilities.append(
                        Vulnerability(
                            id=f"VULN-{self._vulnerability_counter:04d}",
                            title="Potential SQL Injection",
                            description=f"Possible SQL injection vector detected: {pattern}",
                            severity=VulnerabilitySeverity.HIGH,
                            category=VulnerabilityCategory.INJECTION,
                            location=f"line {i + 1}",
                            remediation="Use parameterized queries instead of string concatenation",
                            references=("CWE-89", "OWASP-A1"),
                        )
                    )

        # Check for command injection
        cmd_dangerous = [
            r"os\.system\s*\(",
            r"subprocess\.\w+\s*\([^)]*shell\s*=\s*True",
            r"eval\s*\(",
            r"exec\s*\(",
        ]
        for i, line in enumerate(lines):
            for pattern in cmd_dangerous:
                import re

                if re.search(pattern, line):
                    self._vulnerability_counter += 1
                    vulnerabilities.append(
                        Vulnerability(
                            id=f"VULN-{self._vulnerability_counter:04d}",
                            title="Potential Command Injection",
                            description=f"Possible command injection vector detected: {pattern}",
                            severity=VulnerabilitySeverity.CRITICAL,
                            category=VulnerabilityCategory.INJECTION,
                            location=f"line {i + 1}",
                            remediation="Avoid shell=True, eval(), and exec(); use subprocess with list args",
                            references=("CWE-78", "OWASP-A1"),
                        )
                    )

        return vulnerabilities

    async def check_auth(self, code: str) -> list[Vulnerability]:
        """Check for authentication weaknesses."""
        vulnerabilities: list[Vulnerability] = []
        lines = code.split("\n")

        # Check for hardcoded credentials patterns
        auth_weak = [
            (r'password\s*=\s*["\'][^"\']{1,32}["\']', "Hardcoded password detected"),
            (r'api_key\s*=\s*["\'][^"\']+["\']', "Hardcoded API key detected"),
            (r'secret\s*=\s*["\'][^"\']+["\']', "Hardcoded secret detected"),
            (r'token\s*=\s*["\'][^"\']{1,20}["\']', "Hardcoded token detected"),
        ]
        for i, line in enumerate(lines):
            for pattern, desc in auth_weak:
                import re

                if re.search(pattern, line, re.IGNORECASE):
                    self._vulnerability_counter += 1
                    vulnerabilities.append(
                        Vulnerability(
                            id=f"VULN-{self._vulnerability_counter:04d}",
                            title="Hardcoded Credential",
                            description=desc,
                            severity=VulnerabilitySeverity.HIGH,
                            category=VulnerabilityCategory.AUTHENTICATION,
                            location=f"line {i + 1}",
                            remediation="Use environment variables or secure secrets management",
                            references=("CWE-798", "OWASP-A2"),
                        )
                    )

        return vulnerabilities

    async def check_sensitive_data(self, code: str) -> list[Vulnerability]:
        """Check for exposed sensitive data."""
        vulnerabilities: list[Vulnerability] = []
        lines = code.split("\n")

        # Check for logging sensitive data
        sensitive_patterns = [
            (r"log\.info\([^)]*password[^)]*\)", "Password being logged"),
            (r"log\.info\([^)]*secret[^)]*\)", "Secret being logged"),
            (r"log\.info\([^)]*token[^)]*\)", "Token being logged"),
            (r"log\.info\([^)]*api_key[^)]*\)", "API key being logged"),
        ]
        for i, line in enumerate(lines):
            for pattern, desc in sensitive_patterns:
                import re

                if re.search(pattern, line, re.IGNORECASE):
                    self._vulnerability_counter += 1
                    vulnerabilities.append(
                        Vulnerability(
                            id=f"VULN-{self._vulnerability_counter:04d}",
                            title="Sensitive Data Exposure",
                            description=desc,
                            severity=VulnerabilitySeverity.MEDIUM,
                            category=VulnerabilityCategory.SENSITIVE_DATA,
                            location=f"line {i + 1}",
                            remediation="Avoid logging sensitive data; sanitize before logging",
                            references=("CWE-532", "OWASP-A1"),
                        )
                    )

        return vulnerabilities
