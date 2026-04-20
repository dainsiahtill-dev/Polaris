"""安全相关代码

包含命令注入阻断异常和安全加载函数。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class CommandInjectionBlocked(Exception):
    """Raised when a command string cannot be safely parsed and shell=True fallback is blocked."""

    def __init__(self, command: str, reason: str = "shlex.parse.failed") -> None:
        self.command = command
        self.reason = reason
        super().__init__(f"Command injection blocked: {reason} | command={command!r}")


# 类型别名
AllowedCommandFn = Callable[[str, set[str] | None], bool]
BlockedCommandFn = Callable[[str], bool]


def _always_reject_command(_command: str, _allowed: set[str] | None = None) -> bool:
    """Always reject command (fallback when security unavailable)."""
    return False


def _always_block_command(_command: str) -> bool:
    """Always block command (fallback when security unavailable)."""
    return True


def _load_tooling_security() -> tuple[set[str], AllowedCommandFn, BlockedCommandFn, bool]:
    """Load tooling security module.

    Returns:
        Tuple of (allowed_commands, is_command_allowed, is_command_blocked, security_available).

    Raises:
        CommandInjectionBlocked: If security module import fails.
    """
    try:
        from polaris.cells.director.execution.public.tools import (
            ALLOWED_EXECUTION_COMMANDS as allowed_commands_pkg,
            is_command_allowed as command_allowed_pkg,
            is_command_blocked as command_blocked_pkg,
        )

        return set(allowed_commands_pkg), command_allowed_pkg, command_blocked_pkg, True
    except ImportError as exc:
        logger.error(
            "Tooling security module unavailable: ImportError=%s",
            exc,
        )
        raise CommandInjectionBlocked(
            command="<module_init>",
            reason=f"tooling_security_import_failed:{exc}",
        ) from exc


# 模块级安全配置（延迟加载）
(
    ALLOWED_EXECUTION_COMMANDS,
    is_command_allowed,
    is_command_blocked,
    TOOLING_SECURITY_AVAILABLE,
) = _load_tooling_security()
