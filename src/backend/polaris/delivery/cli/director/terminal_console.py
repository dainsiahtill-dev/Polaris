"""Director compatibility shim for unified terminal console host."""

from polaris.delivery.cli.terminal_console import (
    PolarisLazyClaude,
    PolarisRoleConsole,
    run_director_console,
    run_role_console,
)

__all__ = [
    "PolarisLazyClaude",
    "PolarisRoleConsole",
    "run_director_console",
    "run_role_console",
]
