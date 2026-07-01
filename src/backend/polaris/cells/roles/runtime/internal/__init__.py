"""Internal implementation namespace for the roles.runtime Cell.

The public boundary for role execution is
``polaris.cells.roles.runtime.public``. This package remains an internal owner
for active runtime components such as session orchestration, capability
handlers, and process services. Do not add compatibility re-exports here:
external callers must use the public Cell contracts.
"""

from __future__ import annotations

__all__: list[str] = []
