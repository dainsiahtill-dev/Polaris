"""Public facade for file I/O utilities.

This module provides a stable public interface to file I/O utilities,
allowing other cells to import from here instead of internal modules.
"""

from polaris.cells.runtime.projection.internal.file_io import (
    read_json,
    read_readme_title,
)

__all__ = ["read_json", "read_readme_title"]
