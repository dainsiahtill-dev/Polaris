"""Director console compatibility wrapper around the unified terminal host."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

__frozen__ = True


def run_director_agent_console(
    workspace: str | Path,
    session_id: str | None = None,
) -> None:
    """Launch the Director console using the unified terminal host."""
    try:
        from polaris.delivery.cli.terminal_console import run_director_console
    except (RuntimeError, ValueError) as e:
        logger.error("Failed to import Director components: %s", e)
        sys.exit(1)

    workspace_path = Path(workspace).resolve()

    try:
        run_director_console(workspace=workspace_path, session_id=session_id)
    except KeyboardInterrupt:
        logger.info("Director console interrupted by user")
    finally:
        logger.info("Director console shut down cleanly")
