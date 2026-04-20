"""Director CLI compatibility exports."""

from .console_host import DirectorConsoleHost

# Import from polaris.delivery.cli.terminal_console using __import__ to bypass
# that module's own import of this package (avoids circular import).
_console_module = __import__(
    "polaris.delivery.cli.terminal_console",
    fromlist=["PolarisLazyClaude", "run_director_console"],
)
PolarisLazyClaude = _console_module.PolarisLazyClaude
run_director_console = _console_module.run_director_console

__all__ = [
    "DirectorConsoleHost",
    "PolarisLazyClaude",
    "run_director_console",
]
