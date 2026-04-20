"""Tests for structured logger."""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.kernelone.observability.logger import StructuredLogger


class TestStructuredLogger:
    """Test cases for StructuredLogger."""

    def test_init(self) -> None:
        """Test logger initialization."""
        logger = StructuredLogger("test-service")
        assert logger._service_name == "test-service"

    def test_format_entry(self) -> None:
        """Test log entry formatting."""
        logger = StructuredLogger("test-service")
        entry = logger._format_entry("INFO", "test message", key="value")
        assert entry["service"] == "test-service"
        assert entry["level"] == "INFO"
        assert entry["message"] == "test message"
        assert entry["key"] == "value"

    def test_debug(self) -> None:
        """Test debug logging."""
        logger = StructuredLogger("test-service", level=logging.DEBUG)
        logger.debug("debug message", extra_field="test")
        # Should not raise

    def test_info(self) -> None:
        """Test info logging."""
        logger = StructuredLogger("test-service")
        logger.info("info message", extra_field="test")
        # Should not raise

    def test_warning(self) -> None:
        """Test warning logging."""
        logger = StructuredLogger("test-service")
        logger.warning("warning message", extra_field="test")
        # Should not raise

    def test_error(self) -> None:
        """Test error logging."""
        logger = StructuredLogger("test-service")
        logger.error("error message", extra_field="test")
        # Should not raise

    def test_exception(self) -> None:
        """Test exception logging."""
        logger = StructuredLogger("test-service")
        try:
            raise ValueError("test error")
        except ValueError:
            logger.exception("exception occurred", extra_field="test")
        # Should not raise

    def test_log_entry_structure(self) -> None:
        """Test that log entries have correct structure."""
        logger = StructuredLogger("test-service")
        entries: list[dict[str, Any]] = []

        class CaptureHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.msg:
                    entries.append(json.loads(record.msg))

        handler = CaptureHandler()
        logger._logger.addHandler(handler)
        logger.info("test message", request_id="123", user="test")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["service"] == "test-service"
        assert entry["level"] == "INFO"
        assert entry["message"] == "test message"
        assert entry["request_id"] == "123"
        assert entry["user"] == "test"
