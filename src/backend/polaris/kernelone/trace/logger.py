"""统一结构化日志器

提供JSON格式的结构化日志，自动包含trace_id等上下文信息。

解决现有日志系统的问题：
- 153个独立logger，无统一格式
- 无结构化JSON输出
- trace_id未注入日志
- 敏感信息脱敏不完整
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

from .context import get_context


class JSONFormatter(logging.Formatter):
    """JSON格式日志格式化器

    将日志记录格式化为JSON，自动注入上下文信息（trace_id等）。
    """

    def __init__(
        self,
        *,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
        validate: bool = True,
        ensure_ascii: bool = False,
        indent: int | None = None,
    ) -> None:
        # Note: validate parameter is supported in Python 3.8+
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)  # type: ignore[arg-type]
        self.ensure_ascii = ensure_ascii
        self.indent = indent

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录为JSON"""
        # 获取当前上下文
        # NOTE: we intentionally catch Exception here rather than RuntimeError
        # because get_context() may raise other exceptions in edge cases.
        # A bare fallback is correct: no context means no context fields in the log.
        # We deliberately do NOT log here to avoid infinite recursion when the
        # formatter itself triggers a log event (e.g., during configure_logging).
        try:
            ctx = get_context()
        except (RuntimeError, ValueError):
            ctx = None

        # 构建日志对象
        log_obj: dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "source": {
                "pathname": record.pathname,
                "filename": record.filename,
                "line": record.lineno,
                "function": record.funcName,
                "module": record.module,
            },
        }

        # 添加上下文信息
        if ctx:
            log_obj["context"] = {
                "trace_id": ctx.trace_id,
                "run_id": ctx.run_id,
                "request_id": ctx.request_id,
                "workflow_id": ctx.workflow_id,
                "task_id": ctx.task_id,
                "workspace": ctx.workspace,
                "span_depth": len(ctx.span_stack),
            }
            if ctx.span_stack:
                log_obj["context"]["span_id"] = ctx.span_stack[-1].get("span_id")
                log_obj["context"]["span_name"] = ctx.span_stack[-1].get("name")

        # 添加extra字段
        for key, value in record.__dict__.items():
            if (
                key
                not in (
                    "name",
                    "msg",
                    "args",
                    "levelname",
                    "levelno",
                    "pathname",
                    "filename",
                    "module",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                    "getMessage",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                )
                and key not in log_obj
                and key not in ("context",)
            ):  # 避免覆盖
                log_obj[key] = value

        # 添加异常信息
        if record.exc_info:
            log_obj["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info),
            }

        # 添加堆栈信息
        if record.stack_info:
            log_obj["stack_info"] = record.stack_info

        # 序列化为JSON
        if self.indent:
            return json.dumps(log_obj, ensure_ascii=self.ensure_ascii, indent=self.indent)
        else:
            return json.dumps(log_obj, ensure_ascii=self.ensure_ascii, separators=(",", ":"))

    def _format_timestamp(self, timestamp: float) -> str:
        """格式化时间戳为ISO 8601格式"""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.isoformat()


class TextFormatter(logging.Formatter):
    """文本格式日志格式化器（向后兼容）

    提供类似传统的文本格式，但包含trace_id。
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
        validate: bool = True,
    ) -> None:
        if fmt is None:
            fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        # Note: validate parameter is supported in Python 3.8+
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)  # type: ignore[arg-type]

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录"""
        # 获取trace_id
        # NOTE: intentionally bare catch here — no context is a normal condition
        # (e.g., log events outside any active Polaris span).
        # Deliberately no logger.warning() to avoid recursion in the formatter.
        try:
            ctx = get_context()
            trace_id = ctx.trace_id if ctx else None
        except (RuntimeError, ValueError):
            trace_id = None

        # 添加trace_id到消息
        if trace_id and not hasattr(record, "_trace_id_added"):
            record.msg = f"[{trace_id}] {record.msg}"
            record._trace_id_added = True

        return super().format(record)


class SensitiveDataFilter(logging.Filter):
    """敏感信息过滤器

    自动脱敏敏感信息，防止泄露到日志中。
    """

    # 敏感字段模式
    SENSITIVE_PATTERNS = [
        (re.compile(r'api[_-]?key["\s]*[:=]["\s]*([^\s&]+)', re.I), "api_key"),
        (re.compile(r'token["\s]*[:=]["\s]*([^\s&]+)', re.I), "token"),
        (re.compile(r'authorization["\s]*[:=]["\s]*([^\s&]+)', re.I), "authorization"),
        (re.compile(r'password["\s]*[:=]["\s]*([^\s&]+)', re.I), "password"),
        (re.compile(r'secret["\s]*[:=]["\s]*([^\s&]+)', re.I), "secret"),
        (re.compile(r'private[_-]?key["\s]*[:=]["\s]*([^\s&]+)', re.I), "private_key"),
    ]

    # 敏感HTTP头
    SENSITIVE_HEADERS = {
        "authorization",
        "x-api-key",
        "x-auth-token",
        "cookie",
        "set-cookie",
        "x-csrf-token",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤敏感信息"""
        # 处理消息
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)

        # 处理args
        if record.args:
            new_args: list[str | object] = []
            for arg in record.args:
                if isinstance(arg, str):
                    new_args.append(self._redact(arg))
                else:
                    new_args.append(arg)
            record.args = tuple(new_args)

        return True

    def _redact(self, message: str) -> str:
        """脱敏消息中的敏感信息"""
        for pattern, name in self.SENSITIVE_PATTERNS:
            message = pattern.sub(f"{name}=***REDACTED***", message)
        return message

    @classmethod
    def redact_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        """脱敏HTTP头"""
        result = {}
        for key, value in headers.items():
            if key.lower() in cls.SENSITIVE_HEADERS:
                result[key] = "***REDACTED***"
            else:
                result[key] = value
        return result

    @classmethod
    def redact_dict(
        cls,
        data: dict[str, Any],
        sensitive_keys: set | None = None,
    ) -> dict[str, Any]:
        """脱敏字典中的敏感字段"""
        if sensitive_keys is None:
            sensitive_keys = {"password", "secret", "token", "api_key", "private_key"}

        result: dict[str, Any] = {}
        for key, value in data.items():
            if any(sk in key.lower() for sk in sensitive_keys):
                result[key] = "***REDACTED***"
            elif isinstance(value, dict):
                result[key] = cls.redact_dict(value, sensitive_keys)
            elif isinstance(value, list):
                result[key] = [
                    cls.redact_dict(item, sensitive_keys) if isinstance(item, dict) else item for item in value
                ]
            else:
                result[key] = value
        return result


class UnifiedLogger:
    """统一日志器包装类

    提供更便捷的日志接口，支持extra字段的任意关键字参数。
    """

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(
        self,
        log_level: int,
        msg: str,
        *args: Any,
        exc_info: Any = None,
        stack_info: bool = False,
        **kwargs: Any,
    ) -> None:
        """内部日志方法"""
        # 合并kwargs到extra
        extra: dict[str, Any] = kwargs.pop("extra", {})
        extra.update(kwargs)

        # 获取trace_id
        try:
            ctx = get_context()
            if ctx:
                extra["trace_id"] = ctx.trace_id
        except RuntimeError:
            pass  # get_context() raises RuntimeError when no context is active

        self._logger.log(
            log_level,
            msg,
            *args,
            exc_info=exc_info,
            stack_info=stack_info,
            extra=extra,
        )

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs["exc_info"] = True
        self._log(logging.ERROR, msg, *args, **kwargs)

    def log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(level, msg, *args, **kwargs)

    # 属性访问委托给底层logger
    @property
    def name(self) -> str:
        return self._logger.name

    @property
    def level(self) -> int:
        return self._logger.level

    @level.setter
    def level(self, value: int) -> None:
        self._logger.level = value

    def isEnabledFor(self, level: int) -> bool:  # noqa: N802
        return self._logger.isEnabledFor(level)

    def setLevel(self, level: int) -> None:  # noqa: N802
        self._logger.setLevel(level)

    def addHandler(self, handler: logging.Handler) -> None:  # noqa: N802
        self._logger.addHandler(handler)

    def removeHandler(self, handler: logging.Handler) -> None:  # noqa: N802
        self._logger.removeHandler(handler)


def configure_logging(
    level: int | str = logging.INFO,
    json_output: bool = True,
    output_stream: TextIO | None = None,
    log_file: str | None = None,
    filter_sensitive: bool = True,
) -> None:
    """配置统一日志

    应在应用启动时调用一次。这会清除所有现有的handlers并重新配置。

    Args:
        level: 日志级别，可以是int或字符串（"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"）
        json_output: 是否输出JSON格式，False则使用文本格式
        output_stream: 输出流，默认为sys.stdout
        log_file: 可选的日志文件路径
        filter_sensitive: 是否启用敏感信息过滤

    Example:
        # 在应用启动时配置
        from polaris.kernelone.trace import configure_logging
        configure_logging(
            level="INFO",
            json_output=True,
        )
    """
    # 处理级别字符串
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # 清除现有的root handlers
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handlers: list[logging.Handler] = []

    # Console handler
    console = logging.StreamHandler(output_stream or sys.stdout)
    if json_output:
        console.setFormatter(JSONFormatter())
    else:
        console.setFormatter(TextFormatter())
    if filter_sensitive:
        console.addFilter(SensitiveDataFilter())
    handlers.append(console)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JSONFormatter())
        if filter_sensitive:
            file_handler.addFilter(SensitiveDataFilter())
        handlers.append(file_handler)

    # Root configuration
    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,  # 覆盖已有配置
    )

    # 设置常用第三方库的日志级别
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("charset_normalizer").setLevel(logging.WARNING)

    # 输出配置完成信息
    logger = UnifiedLogger("observability")
    logger.info(
        "Logging configured",
        level=logging.getLevelName(level),
        json_output=json_output,
        log_file=log_file,
    )


def get_logger(name: str) -> UnifiedLogger:
    """获取统一日志器实例

    Args:
        name: 日志器名称，通常使用__name__

    Returns:
        UnifiedLogger实例

    Example:
        from polaris.kernelone.trace import get_logger
        logger = get_logger(__name__)
        logger.info("Something happened", extra_key="extra_value")
    """
    return UnifiedLogger(name)


# 向后兼容：为旧代码提供简单的日志配置
def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
) -> None:
    """简单的日志配置（向后兼容）

    这个函数是为了兼容旧代码，新代码应该使用configure_logging()。
    """
    configure_logging(
        level=level,
        json_output=json_format,
    )
