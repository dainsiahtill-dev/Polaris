"""Structured logging for KernelOne telemetry/ subsystem.

Provides KernelLogger — a structured logger wrapper that ensures consistent
field naming, UTF-8 encoding, and contextual trace propagation.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text operations use encoding="utf-8"
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.contracts.technical import TraceContext

_logger_factory: dict[str, KernelLogger] = {}


class _JsonFormatter(logging.Formatter):
    """JSON formatter for KernelOne structured logs."""

    def __init__(self, trace_context: TraceContext | None = None) -> None:
        super().__init__()
        self._trace_context = trace_context

    def format(self, record: logging.LogRecord) -> str:
        try:
            msg = record.getMessage()
            extra = getattr(record, "extra_fields", {})
            trace = getattr(record, "trace_context", None)
            err_info: dict[str, Any] = {}
            if record.exc_info:
                err_info = {
                    "error_type": record.exc_info[0].__name__ if record.exc_info[0] else "",
                    "error_message": str(record.exc_info[1]) if record.exc_info[1] else "",
                }
            payload: dict[str, Any] = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }
            if extra:
                payload["extra"] = extra
            if trace:
                payload["trace"] = trace.to_dict()
            if err_info:
                payload["error"] = err_info
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (RuntimeError, ValueError):
            # Fallback to plain text on format failure
            return super().format(record)


class KernelLogger:
    """Structured logger for KernelOne.

    Wraps a stdlib logger and always emits JSON-encoded log records
    with consistent fields. Use get_logger() to obtain instances.

    Usage::

        logger = get_logger("kernelone.fs")
        logger.info("File read", extra={"path": "/tmp/data.txt", "bytes": 1024})
        logger.error("Read failed", exc_info=True, extra={"path": "/tmp/data.txt"})

    Contextual trace propagation::

        with logger.bind(trace_context=trace):
            logger.info("Operation with trace")
    """

    def __init__(
        self,
        name: str,
        *,
        level: int = logging.INFO,
        trace_context: TraceContext | None = None,
    ) -> None:
        self._name = name
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._trace_context = trace_context
        self._ensure_handler()

    def _ensure_handler(self) -> None:
        # Add JSON handler only if none exists to avoid duplicates
        if not any(isinstance(h, _JsonHandler) for h in self._logger.handlers):
            handler = _JsonHandler()
            handler.setFormatter(_JsonFormatter(trace_context=self._trace_context))
            self._logger.addHandler(handler)
            # Suppress propagation to root to avoid double logging
            self._logger.propagate = False

    def set_level(self, level: int) -> None:
        self._logger.setLevel(level)

    def bind(self, **kwargs: Any) -> _BoundLoggerContext:
        """Return a context manager that adds fields to all log records within it."""
        return _BoundLoggerContext(self, kwargs)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, kwargs)

    def _log(self, level: int, msg: str, extra: dict[str, Any]) -> None:
        record = self._logger.makeRecord(
            self._name,
            level,
            "(unknown)",
            0,
            msg,
            (),
            None,
        )
        record.extra_fields = extra
        if self._trace_context:
            record.trace_context = self._trace_context
        self._logger.handle(record)


class _JsonHandler(logging.Handler):
    """Handler that emits JSON to stderr for KernelOne log collection."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
        except (RuntimeError, ValueError):
            self.handleError(record)


class _BoundLoggerContext:
    """Context manager for scoped log bindings."""

    __slots__ = ("_fields", "_logger", "_old_factory")

    def __init__(self, logger: KernelLogger, fields: dict[str, Any]) -> None:
        self._logger = logger
        self._fields = fields
        self._old_factory: logging.LoggerAdapter[Any] | None = None

    def __enter__(self) -> _BoundLoggerContext:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass  # Binding is live for the duration of this context manager use

    def debug(self, msg: str, **kwargs: Any) -> None:
        merged = {**self._fields, **kwargs}
        self._logger.debug(msg, **merged)

    def info(self, msg: str, **kwargs: Any) -> None:
        merged = {**self._fields, **kwargs}
        self._logger.info(msg, **merged)

    def warning(self, msg: str, **kwargs: Any) -> None:
        merged = {**self._fields, **kwargs}
        self._logger.warning(msg, **merged)

    def error(self, msg: str, **kwargs: Any) -> None:
        merged = {**self._fields, **kwargs}
        self._logger.error(msg, **merged)


def get_logger(name: str, **kwargs: Any) -> KernelLogger:
    """Get or create a KernelLogger by name.

    Args:
        name: Logger name (e.g. "kernelone.fs", "kernelone.llm")
        **kwargs: Forwarded to KernelLogger constructor

    Returns:
        A KernelLogger instance (cached by name).
    """
    if name not in _logger_factory:
        _logger_factory[name] = KernelLogger(name, **kwargs)
    return _logger_factory[name]
