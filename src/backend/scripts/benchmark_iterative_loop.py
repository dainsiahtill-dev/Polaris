"""DEPRECATED: Use polaris.delivery.cli.tools.benchmark_iterative_loop instead.

This shim maintains backward compatibility for workflows that call
scripts/benchmark_iterative_loop.py directly. It prints a deprecation
warning and forwards to the new location.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path


def _forward_to_new_location() -> int:
    """Forward to the new benchmark_iterative_loop location."""
    # Ensure backend is in path
    backend_root = Path(__file__).resolve().parents[1]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)

    from polaris.delivery.cli.tools.benchmark_iterative_loop import main

    return main()


def main() -> int:
    """Main entry point with deprecation warning."""
    warnings.warn(
        "scripts/benchmark_iterative_loop.py is deprecated. "
        "Use 'python -m polaris.delivery.cli.tools.benchmark_iterative_loop' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _forward_to_new_location()


if __name__ == "__main__":
    sys.exit(main())
