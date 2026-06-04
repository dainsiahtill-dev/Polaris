"""Director Interface shim for PM compatibility imports.

This module re-exports all public symbols from director_interface_core
for code importing from this historical path. Execution still routes through
the canonical Director adapter.

All new code should import directly from polaris.delivery.cli.pm.director_interface_core.
"""

from polaris.delivery.cli.pm.director_interface_core import (
    CanonicalDirectorAdapter,
    DirectorFactory,
    DirectorInterface,
    DirectorResult,
    DirectorTask,
    NoDirectorAdapter,
    create_director,
)

__all__ = [
    "CanonicalDirectorAdapter",
    "DirectorFactory",
    "DirectorInterface",
    "DirectorResult",
    "DirectorTask",
    "NoDirectorAdapter",
    "create_director",
]
