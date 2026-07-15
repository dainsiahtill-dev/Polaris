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
    )
    from polaris.delivery.cli.director.director_service import DirectorService as IterationDirectorService
    from polaris.domain.entities import TaskPriority

    return DirectorConfig, DirectorService, IterationDirectorService, TaskPriority


logger = logging.getLogger(__name__)


def _bootstrap_director_fact_stream(workspace: str, maintenance_reason: str) -> None:
    """Delegate one formal Director entrypoint to FactStream bootstrap."""

    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        bootstrap_fact_stream_workspace,
        fact_stream_bootstrap_streams,
    )

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason=maintenance_reason,
        )
    )


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
        "--command",
        type=str,
        default=None,
        help="Direct command to execute",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="Director model override",
    )
    parser.add_argument(
        "--prompt-profile",
        type=str,
        default="",
        help="Prompt profile override (accepted for PM compatibility)",
    )
    parser.add_argument(
        "--ramdisk-root",
        type=str,
        default="",
        help="Runtime ramdisk root (accepted for PM compatibility)",
    )
    parser.add_argument(
        "--forever",
        action="store_true",
        help="Run Director iterations until stopped",
    )
    parser.add_argument(
        "--show-output",
        action="store_true",
        help="Accepted for PM compatibility; logging controls output",
    )
    parser.add_argument(
        "--slm-enabled",
        action="store_true",
        help="Accepted for PM compatibility; SLM is controlled by runtime config",
    )
    return parser


async def run_director(
    workspace: str,
    iterations: int,
    max_workers: int,
    command: str | None,
    *,
    model: str = "",
    forever: bool = False,
) -> None:
    """Run director in iterative mode."""
    _bootstrap_director_fact_stream(workspace, "director_v2_cli_startup")
    director_config_cls, director_service_cls, iteration_service_cls, task_priority_cls = (
        _bootstrap_backend_import_path()
    )

    if command:
        service = director_service_cls(
            config=director_config_cls(
                workspace=workspace,
                max_workers=max_workers,
            )
        )
        await service.submit_task(
            subject=command,
            description="Director v2 command task",
            command=command,
            priority=task_priority_cls.MEDIUM,
        )
        await service.start()
        while True:
            status = await service.get_status()
            if str(status.get("state") or "").upper() != "RUNNING":
                logger.info("Command task result: %s", status)
                return
            await asyncio.sleep(0.25)
    iteration_service = iteration_service_cls(
        workspace=Path(workspace).resolve(),
        model=str(model or ""),
        max_workers=max_workers,
        execution_mode="parallel" if max_workers > 1 else "serial",
    )
    iteration = 0
    while forever or iteration < max(1, int(iterations or 1)):
        iteration += 1
        total_label = "forever" if forever else str(max(1, int(iterations or 1)))
        logger.info("Iteration %d/%s", iteration, total_label)
        result = await iteration_service.run_iteration(iteration=iteration)
        logger.info("Director iteration result: %s", result)
        if forever:
            await asyncio.sleep(1.0)


async def run_status(workspace: str) -> None:
    """Run director in status mode."""
    _bootstrap_director_fact_stream(workspace, "director_v2_cli_status")
    director_config_cls, director_service_cls, _, _ = _bootstrap_backend_import_path()

    service = director_service_cls(config=director_config_cls(workspace=workspace))
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
            parsed.command,
            model=parsed.model,
            forever=bool(parsed.forever),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
