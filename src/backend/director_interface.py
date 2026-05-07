"""Director Interface shim for backward compatibility.

MIGRATION-SHIM: pending removal after v2.0.
Canonical implementation: polaris.delivery.cli.pm.director_interface_core
(see AGENTS.md §5).

This module re-exports all public symbols from the migrated location
to maintain backward compatibility for any code importing from the old root.

All new code should import from polaris.delivery.cli.pm.director_interface_core.
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
