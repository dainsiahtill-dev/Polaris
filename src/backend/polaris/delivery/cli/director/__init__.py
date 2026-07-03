"""Director CLI exports backed by the canonical terminal host."""

from .console_host import DirectorConsoleHost

_console_module = __import__(
    "polaris.delivery.cli.terminal",
    fromlist=["run_director_console"],
)
run_director_console = _console_module.run_director_console

__all__ = [
    "DirectorConsoleHost",
    "run_director_console",
]
