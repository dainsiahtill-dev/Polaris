"""Structured JSON logger for observability."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any


@dataclass
class StructuredLogger:
    """Structured JSON logger for observability."""

    def __init__(
        self,
        service_name: str,
        level: int = logging.INFO,
    ) -> None:
        self._service_name = service_name
        self._logger = logging.getLogger(f"polaris.{service_name}")
        self._logger.setLevel(level)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(message)s"),
            )
            self._logger.addHandler(handler)

    def _format_entry(
        self,
        level: str,
        message: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Format a structured log entry."""
        entry: dict[str, Any] = {
            "service": self._service_name,
            "level": level,
            "message": message,
        }
        entry.update(kwargs)
        return entry

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log a debug message."""
        entry = self._format_entry("DEBUG", message, **kwargs)
        self._logger.debug(json.dumps(entry))

    def info(self, message: str, **kwargs: Any) -> None:
        """Log an info message."""
        entry = self._format_entry("INFO", message, **kwargs)
        self._logger.info(json.dumps(entry))

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log a warning message."""
        entry = self._format_entry("WARNING", message, **kwargs)
        self._logger.warning(json.dumps(entry))

    def error(self, message: str, **kwargs: Any) -> None:
        """Log an error message."""
        entry = self._format_entry("ERROR", message, **kwargs)
        self._logger.error(json.dumps(entry))

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log an exception with traceback."""
        entry = self._format_entry("ERROR", message, **kwargs)
        self._logger.exception(json.dumps(entry))
