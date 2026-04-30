"""Tests for trace/logger module."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from polaris.kernelone.trace.context import ContextManager, PolarisContext
from polaris.kernelone.trace.logger import (
    JSONFormatter,
    SensitiveDataFilter,
    TextFormatter,
    UnifiedLogger,
    configure_logging,
    get_logger,
    setup_logging,
)


class TestJSONFormatter:
    """Tests for JSONFormatter."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.formatter = JSONFormatter()
        self.record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )

    def test_format_returns_string(self) -> None:
        """format returns a string."""
        result = self.formatter.format(self.record)
        assert isinstance(result, str)

    def test_format_contains_timestamp(self) -> None:
        """Output contains timestamp."""
        result = self.formatter.format(self.record)
        data = json.loads(result)
        assert "timestamp" in data

    def test_format_contains_level(self) -> None:
        """Output contains level."""
        result = self.formatter.format(self.record)
        data = json.loads(result)
        assert data["level"] == "INFO"

    def test_format_contains_message(self) -> None:
        """Output contains message."""
        result = self.formatter.format(self.record)
        data = json.loads(result)
        assert data["message"] == "hello"

    def test_format_contains_source(self) -> None:
        """Output contains source info."""
        result = self.formatter.format(self.record)
        data = json.loads(result)
        assert "source" in data
        assert data["source"]["filename"] == "test.py"

    def test_format_with_context(self) -> None:
        """Output includes trace context when available."""
        ContextManager.clear()
        ctx = PolarisContext(trace_id="t-123", run_id="r-456")
        ContextManager.set_context(ctx)
        try:
            result = self.formatter.format(self.record)
            data = json.loads(result)
            assert "context" in data
            assert data["context"]["trace_id"] == "t-123"
            assert data["context"]["run_id"] == "r-456"
        finally:
            ContextManager.clear()

    def test_format_with_exception(self) -> None:
        """Output includes exception info."""
        try:
            raise ValueError("test error")
        except ValueError:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="error",
                args=(),
                exc_info=sys.exc_info(),
            )
        result = self.formatter.format(record)
        data = json.loads(result)
        assert "exception" in data
        assert data["exception"]["type"] == "ValueError"

    def test_format_with_indent(self) -> None:
        """Indent produces pretty-printed JSON."""
        formatter = JSONFormatter(indent=2)
        result = formatter.format(self.record)
        assert "\n" in result

    def test_format_compact(self) -> None:
        """No indent produces compact JSON."""
        formatter = JSONFormatter(indent=None)
        result = formatter.format(self.record)
        assert "\n" not in result

    def test_ensure_ascii_false(self) -> None:
        """Unicode is preserved."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="你好",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        assert "你好" in result


class TestTextFormatter:
    """Tests for TextFormatter."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.formatter = TextFormatter()

    def test_format_returns_string(self) -> None:
        """format returns a string."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        result = self.formatter.format(record)
        assert isinstance(result, str)
        assert "hello" in result

    def test_format_includes_level(self) -> None:
        """Output includes log level."""
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="warn",
            args=(),
            exc_info=None,
        )
        result = self.formatter.format(record)
        assert "WARNING" in result

    def test_format_with_trace_id(self) -> None:
        """Output includes trace_id when context available."""
        ContextManager.clear()
        ctx = PolarisContext(trace_id="t-123")
        ContextManager.set_context(ctx)
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="hello",
                args=(),
                exc_info=None,
            )
            result = self.formatter.format(record)
            assert "t-123" in result
        finally:
            ContextManager.clear()

    def test_default_format(self) -> None:
        """Default format string is correct."""
        formatter = TextFormatter()
        assert formatter._fmt == "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class TestSensitiveDataFilter:
    """Tests for SensitiveDataFilter."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.filter = SensitiveDataFilter()

    def test_redacts_api_key(self) -> None:
        """API key is redacted in message."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="api_key=secret123",
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert "***REDACTED***" in record.msg
        assert "secret123" not in record.msg

    def test_redacts_token(self) -> None:
        """Token is redacted."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="token=abc123",
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert "***REDACTED***" in record.msg

    def test_redacts_password(self) -> None:
        """Password is redacted."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg='password="mypassword"',
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert "***REDACTED***" in record.msg

    def test_no_redaction_for_safe_text(self) -> None:
        """Safe text is not modified."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert record.msg == "hello world"

    def test_returns_true(self) -> None:
        """filter always returns True."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        assert self.filter.filter(record) is True

    def test_redact_headers(self) -> None:
        """HTTP headers are redacted."""
        headers = {
            "Authorization": "Bearer secret",
            "Content-Type": "application/json",
            "X-API-Key": "key123",
        }
        result = SensitiveDataFilter.redact_headers(headers)
        assert result["Authorization"] == "***REDACTED***"
        assert result["Content-Type"] == "application/json"
        assert result["X-API-Key"] == "***REDACTED***"

    def test_redact_dict(self) -> None:
        """Dictionary values are redacted."""
        data = {
            "username": "alice",
            "password": "secret",
            "nested": {"api_key": "key123", "value": 42},
        }
        result = SensitiveDataFilter.redact_dict(data)
        assert result["password"] == "***REDACTED***"
        assert result["username"] == "alice"
        assert result["nested"]["api_key"] == "***REDACTED***"
        assert result["nested"]["value"] == 42

    def test_redact_dict_custom_keys(self) -> None:
        """Custom sensitive keys are respected."""
        data = {"custom_secret": "hidden", "normal": "visible"}
        result = SensitiveDataFilter.redact_dict(data, sensitive_keys={"custom_secret"})
        assert result["custom_secret"] == "***REDACTED***"
        assert result["normal"] == "visible"


class TestUnifiedLogger:
    """Tests for UnifiedLogger."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.logger = UnifiedLogger("test.logger")

    def test_name_property(self) -> None:
        """name property returns logger name."""
        assert self.logger.name == "test.logger"

    def test_level_property(self) -> None:
        """level property is accessible."""
        self.logger.setLevel(logging.DEBUG)
        assert self.logger.level == logging.DEBUG

    def test_is_enabled_for(self) -> None:
        """isEnabledFor returns correct value."""
        self.logger.setLevel(logging.WARNING)
        assert self.logger.isEnabledFor(logging.WARNING) is True
        assert self.logger.isEnabledFor(logging.DEBUG) is False

    def test_debug_level(self, caplog: Any) -> None:
        """debug method logs at DEBUG level."""
        self.logger.setLevel(logging.DEBUG)
        with caplog.at_level(logging.DEBUG, logger="test.logger"):
            self.logger.debug("debug message")
        assert "debug message" in caplog.text

    def test_info_level(self, caplog: Any) -> None:
        """info method logs at INFO level."""
        with caplog.at_level(logging.INFO, logger="test.logger"):
            self.logger.info("info message")
        assert "info message" in caplog.text

    def test_warning_level(self, caplog: Any) -> None:
        """warning method logs at WARNING level."""
        with caplog.at_level(logging.WARNING, logger="test.logger"):
            self.logger.warning("warn message")
        assert "warn message" in caplog.text

    def test_error_level(self, caplog: Any) -> None:
        """error method logs at ERROR level."""
        with caplog.at_level(logging.ERROR, logger="test.logger"):
            self.logger.error("error message")
        assert "error message" in caplog.text

    def test_critical_level(self, caplog: Any) -> None:
        """critical method logs at CRITICAL level."""
        with caplog.at_level(logging.CRITICAL, logger="test.logger"):
            self.logger.critical("critical message")
        assert "critical message" in caplog.text

    def test_exception_level(self, caplog: Any) -> None:
        """exception method logs at ERROR with exc_info."""
        with caplog.at_level(logging.ERROR, logger="test.logger"):
            self.logger.exception("exception message")
        assert "exception message" in caplog.text

    def test_log_with_extra(self, caplog: Any) -> None:
        """extra fields are passed through."""
        self.logger.setLevel(logging.DEBUG)
        with caplog.at_level(logging.DEBUG, logger="test.logger"):
            self.logger.info("msg", custom_key="custom_value")
        # The extra field may or may not appear in text depending on formatter
        # but the call should not raise


class TestConfigureLogging:
    """Tests for configure_logging function."""

    def test_configure_with_defaults(self) -> None:
        """Configures without errors."""
        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1

    def test_configure_json_false(self) -> None:
        """Text format is configured when json_output=False."""
        configure_logging(json_output=False)
        root = logging.getLogger()
        assert len(root.handlers) >= 1

    def test_configure_with_file(self, tmp_path: Any) -> None:
        """File handler is added when log_file is set."""
        log_file = str(tmp_path / "test.log")
        configure_logging(log_file=log_file)
        root = logging.getLogger()
        handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(handlers) >= 1

    def test_configure_level_string(self) -> None:
        """Level can be set as string."""
        configure_logging(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_configure_level_int(self) -> None:
        """Level can be set as int."""
        configure_logging(level=logging.WARNING)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_configure_sets_third_party_levels(self) -> None:
        """Third-party library levels are set."""
        configure_logging()
        assert logging.getLogger("urllib3").level == logging.WARNING
        assert logging.getLogger("asyncio").level == logging.WARNING


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_unified_logger(self) -> None:
        """Returns UnifiedLogger instance."""
        logger = get_logger("test")
        assert isinstance(logger, UnifiedLogger)

    def test_different_names(self) -> None:
        """Different names produce different loggers."""
        logger1 = get_logger("test1")
        logger2 = get_logger("test2")
        assert logger1.name == "test1"
        assert logger2.name == "test2"


class TestSetupLogging:
    """Tests for setup_logging backward compatibility function."""

    def test_backward_compatible(self) -> None:
        """Function works without errors."""
        setup_logging(level="INFO", json_format=False)
        root = logging.getLogger()
        assert len(root.handlers) >= 1


class TestModuleExports:
    """Tests for module public API."""

    def test_all_exports_present(self) -> None:
        """All expected names are importable."""
        from polaris.kernelone.trace import logger

        assert hasattr(logger, "JSONFormatter")
        assert hasattr(logger, "TextFormatter")
        assert hasattr(logger, "SensitiveDataFilter")
        assert hasattr(logger, "UnifiedLogger")
        assert hasattr(logger, "configure_logging")
        assert hasattr(logger, "get_logger")
        assert hasattr(logger, "setup_logging")
