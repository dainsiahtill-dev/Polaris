"""Adapters for external script/module access without sys.path manipulation.

This package provides clean adapters for accessing scripts and modules
that would otherwise require sys.path hacks.
"""

from .scripts_pm import ScriptsPMAdapter

__all__ = ["ScriptsPMAdapter"]
