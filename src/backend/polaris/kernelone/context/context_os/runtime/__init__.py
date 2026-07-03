"""State-First Context OS runtime package root.

This package splits the monolithic runtime into focused modules:
- engine: Main runtime execution engine (StateFirstContextOS)
- state: State management and transitions
- ports: Text-to-structure port helpers
- scheduler: Task scheduling and queue management

The package root exposes only the public runtime engine. Internal helper
constants and parsing functions stay in their owning submodules.
"""

from __future__ import annotations

from .engine import StateFirstContextOS

__all__ = [
    "StateFirstContextOS",
]
