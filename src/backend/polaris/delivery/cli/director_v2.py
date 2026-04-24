"""Director v2 CLI - New Architecture Entry Point.

This is the new Clean Architecture Director that replaces the old monolithic Director.

Usage:
    python src/backend/polaris/delivery/cli/director_v2.py --workspace <path> [--iterations N]
    python src/backend/polaris/delivery/cli/director_v2.py serve [--host HOST] [--port PORT]
    python src/backend/polaris/delivery/cli/director_v2.py status
    python src/backend/polaris/delivery/cli/director_v2.py task create --subject "Task name" [--command "cmd"]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


def _bootstrap_backend_import_path():
    """Lazy import of polaris modules after path bootstrap."""
    if __package__:
        # Already in a package, imports should work
        pass
    else:
        # Running as script - ensure backend is in path
        backend_root = Path(__file__).resolve().parents[3]
        backend_root_str = str(backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)

    from polaris.cells.director.execution.public.service import (
        DirectorConfig,
        DirectorService,
        DirectorState,
    )
    from polaris.domain.entities import TaskPriority

    return DirectorConfig, DirectorService, DirectorState, TaskPriority


logger = logging.getLogger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser."""
    parser = argparse.ArgumentParser(
        prog="director-v2",
        description="Polaris Director v2 - Clean Architecture Task Orchestration",
    )

    parser.add_argument(
        "--workspace",
        type=str,
        default=".",
        help="Workspace directory",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of iterations (default: 1)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Maximum parallel workers (default: 1)",
    )
    parser.add_argument(
        "--state",
        type=str,
        choices=["idle", "running", "completed", "failed"],
        default="idle",
        help="Initial state (default: idle)",
    )
    parser.add_argument(
        "--command",
        type=str,
        default=None,
        help="Direct command to execute",
    )
    return parser


async def run_director(workspace: str, iterations: int, max_workers: int, state: str, command: str | None) -> None:
    """Run director in iterative mode."""
    DirectorConfig, DirectorService, DirectorState, _ = _bootstrap_backend_import_path()  # noqa: N806

    config = DirectorConfig(
        workspace=workspace,
        max_iterations=iterations,
        max_workers=max_workers,
    )
    service = DirectorService(config=config)

    if state != "idle":
        initial = DirectorState(status=state)
        service.set_state(initial)

    if command:
        result = await service.execute_command(command)
        logger.info("Command result: %s", result)
    else:
        for i in range(iterations):
            logger.info("Iteration %d/%d", i + 1, iterations)
            await service.run_iteration()


async def run_status(workspace: str) -> None:
    """Run director in status mode."""
    _, director_service_cls, _, _ = _bootstrap_backend_import_path()

    service = director_service_cls(workspace=workspace)
    status = await service.get_status()
    logger.info("Director status: %s", status)


def main() -> int:
    args = sys.argv[1:]
    parser = create_parser()
    parsed = parser.parse_args(args)

    workspace = str(parsed.workspace or os.getcwd())

    asyncio.run(
        run_director(
            workspace,
            parsed.iterations,
            parsed.max_workers,
            parsed.state,
            parsed.command,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
