"""Allow ``python -m polaris.tests.agent_stress.runner``."""

# mypy: ignore-errors

from __future__ import annotations

import asyncio
import sys

from . import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
