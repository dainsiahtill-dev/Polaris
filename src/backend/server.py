"""Server entry point shim for backward compatibility.

This module delegates to the migrated location to maintain backward
compatibility for any code launching from the old root path.

All new code should import from polaris.delivery.server.
"""

from polaris.delivery.server import main

if __name__ == "__main__":
    raise SystemExit(main())
