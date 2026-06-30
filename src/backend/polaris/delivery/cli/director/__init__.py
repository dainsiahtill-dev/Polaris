"""Director CLI exports backed by the canonical terminal host."""

from .console_host import DirectorConsoleHost

_console_module = __import__(
    "polaris.delivery.cli.terminal",
    fromlist=["PolarisLazyClaude", "run_director_console"],
)
PolarisLazyClaude = _console_module.PolarisLazyClaude
run_director_console = _console_module.run_director_console

__all__ = [
    "DirectorConsoleHost",
    "PolarisLazyClaude",
    "run_director_console",
]
