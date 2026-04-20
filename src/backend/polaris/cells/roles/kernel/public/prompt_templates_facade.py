"""Public facade for role prompt templates.

This module provides access to role prompt templates through a public interface,
allowing other cells to import from here instead of internal modules.
"""

from polaris.cells.roles.kernel.internal.prompt_templates import (
    ROLE_PROMPT_TEMPLATES,
    SHARED_SECURITY_BOUNDARY,
)

__all__ = ["ROLE_PROMPT_TEMPLATES", "SHARED_SECURITY_BOUNDARY"]
