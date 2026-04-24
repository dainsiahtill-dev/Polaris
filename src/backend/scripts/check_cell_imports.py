#!/usr/bin/env python3
"""DEPRECATED: Use polaris.delivery.cli.tools.check_cell_imports instead.

This shim maintains backward compatibility for workflows that call
scripts/check_cell_imports.py directly. It prints a deprecation
warning and forwards to the new location.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path


def _forward_to_new_location() -> int:
    """Forward to the new check_cell_imports location."""
    # Ensure backend is in path
    backend_root = Path(__file__).resolve().parents[1]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)

    from polaris.delivery.cli.tools.check_cell_imports import main

    return main()


def main() -> int:
    """Main entry point with deprecation warning."""
    warnings.warn(
        "scripts/check_cell_imports.py is deprecated. "
        "Use 'python -m polaris.delivery.cli.tools.check_cell_imports' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _forward_to_new_location()


if __name__ == "__main__":
    sys.exit(main())
