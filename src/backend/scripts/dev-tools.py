#!/usr/bin/env python3
"""DEPRECATED: Use polaris.delivery.cli.tools.dev_tools instead.

This shim maintains backward compatibility for workflows that call
scripts/dev-tools.py directly. It prints a deprecation warning and
forwards to the new location.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path


def _forward_to_new_location() -> int:
    """Forward to the new dev_tools location."""
    # Ensure backend is in path
    backend_root = Path(__file__).resolve().parents[1]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)

    from polaris.delivery.cli.tools.dev_tools import main

    return main()


def main() -> int:
    """Main entry point with deprecation warning."""
    warnings.warn(
        "scripts/dev-tools.py is deprecated. "
        "Use 'python -m polaris.delivery.cli.tools.dev_tools' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _forward_to_new_location()


if __name__ == "__main__":
    sys.exit(main())
