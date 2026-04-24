"""Director Interface shim for backward compatibility.

This module re-exports all public symbols from director_interface_core
to maintain backward compatibility for any code importing from this path.

All new code should import directly from polaris.delivery.cli.pm.director_interface_core.
"""

from polaris.delivery.cli.pm.director_interface_core import (
    DirectorFactory,
    DirectorInterface,
    DirectorResult,
    DirectorTask,
    NoDirectorAdapter,
    ScriptDirectorAdapter,
    create_director,
)

__all__ = [
    "DirectorFactory",
    "DirectorInterface",
    "DirectorResult",
    "DirectorTask",
    "NoDirectorAdapter",
    "ScriptDirectorAdapter",
    "create_director",
]
