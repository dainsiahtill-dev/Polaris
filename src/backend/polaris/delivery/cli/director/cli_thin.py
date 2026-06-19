"""Director CLI Thin Adapter - Phase 4 Refactoring.

This is the refactored thin CLI adapter for Director that delegates all
business logic to the core orchestration layer.

Usage:
    python -m polaris.delivery.cli.director.cli_thin --workspace <path> [--iterations N]
    python -m polaris.delivery.cli.director.cli_thin serve [--host HOST] [--port PORT]
    python -m polaris.delivery.cli.director.cli_thin task create --subject "Task name"
    python -m polaris.delivery.cli.director.cli_thin console [--backend auto|plain]

Architecture:
    - CLI Layer: Argument parsing only (this file)
    - Core Layer: RuntimeOrchestrator, ProcessLauncher (orchestration/)
    - Domain Layer: ServiceDefinition, RunMode
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
        backend_root = Path(__file__).resolve().parents[4]
        backend_root_str = str(backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)

    from polaris.cells.orchestration.workflow_runtime.public.process_launch import RunMode
    from polaris.cells.orchestration.workflow_runtime.public.service import RuntimeOrchestrator, ServiceDefinition
    from polaris.kernelone.fs.encoding import enforce_utf8

    return RunMode, RuntimeOrchestrator, ServiceDefinition, enforce_utf8


logger = logging.getLogger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """Create thin argument parser."""
    parser = argparse.ArgumentParser(
        prog="director-thin",
        description="Polaris Director - Thin CLI Adapter",
    )

    # Workspace config
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=os.getcwd(),
        help="Workspace directory",
    )
    parser.add_argument(
        "--backend",
        type=str,
        choices=["auto", "plain"],
        default="auto",
        help="Backend type (default: auto)",
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

    # Subcommands
    task_subparsers = parser.add_subparsers(dest="task_command", help="Subcommands")

    # Server subcommand
    serve_parser = task_subparsers.add_parser("serve", help="Start as long-running server")
    serve_parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Server bind host (default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=49978,
        help="Server port (default: 49978)",
    )

    console_parser = task_subparsers.add_parser("console", help="Open the unified role terminal console")
    console_parser.add_argument("--role", default="director", help="Role to run in the terminal console")
    console_parser.add_argument(
        "--backend",
        dest="console_backend",
        choices=["auto", "plain"],
        default=None,
        help="Terminal rendering backend (default: inherit top-level --backend)",
    )
    console_parser.add_argument("--session-id", help="Existing role session id")
    console_parser.add_argument("--session-title", help="Title for a new role session")

    # Task command group: ``task create --subject ...`` (matches the docstring).
    task_parser = task_subparsers.add_parser("task", help="Task commands")
    task_action_subparsers = task_parser.add_subparsers(dest="task_action", help="Task actions")
    task_create = task_action_subparsers.add_parser("create", help="Create a new task")
    task_create.add_argument("--subject", required=True, help="Task subject")
    task_create.add_argument("--description", help="Task description")
    task_create.add_argument("--priority", choices=["low", "medium", "high"], default="medium", help="Task priority")

    return parser


async def run_director_console(workspace: str, iterations: int, max_workers: int) -> bool:
    """Run director in console mode.

    Returns:
        ``True`` only if every iteration reported success; ``False`` otherwise.
        Mirrors ``director_service.main`` fail-closed semantics (CLAUDE.md §5).
    """
    from polaris.delivery.cli.director.director_service import DirectorService

    service = DirectorService(
        workspace=Path(workspace).resolve(),
        max_workers=max_workers,
        execution_mode="parallel" if max_workers > 1 else "serial",
    )
    ok = True
    for iteration in range(1, max(1, iterations) + 1):
        result = await service.run_iteration(iteration=iteration)
        logger.info("Director iteration result: %s", result)
        ok = ok and bool(result.get("success", False))
    return ok


async def run_director_server(workspace: str, host: str, port: int) -> None:
    """Run director in server mode."""
    _imports = _bootstrap_backend_import_path()
    enforce_utf8 = _imports[3]
    from polaris.delivery.cli.director.director_service import DirectorService

    enforce_utf8()
    logger.info(f"Starting Director server on {host}:{port}...")
    logger.info("Workspace: %s", workspace)

    service = DirectorService(
        workspace=Path(workspace).resolve(),
        execution_mode="parallel",
    )
    try:
        while True:
            result = await service.run_iteration()
            logger.info("Director server iteration result: %s", result)
            await asyncio.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Shutting down Director server...")


async def create_task(workspace: str, subject: str, description: str, priority: str) -> None:
    """Create a new task via director."""
    from polaris.cells.director.execution.public.service import DirectorConfig, DirectorService
    from polaris.domain.entities import TaskPriority

    config = DirectorConfig(workspace=workspace)
    service = DirectorService(config=config)
    task_priority = TaskPriority(priority.lower())
    task = await service.submit_task(
        subject=subject,
        description=description or "",
        priority=task_priority,
    )
    logger.info("Task created: %s", task.id)


def main() -> int:
    # Enforce UTF-8 encoding before any console output (Chinese character support)
    try:
        from polaris.kernelone.fs.encoding import enforce_utf8

        enforce_utf8()
    except (RuntimeError, ValueError):
        pass  # Fallback: rely on environment defaults

    args = sys.argv[1:]
    parser = create_parser()
    parsed = parser.parse_args(args)

    workspace = str(parsed.workspace or os.getcwd())

    if parsed.task_command == "task":
        if getattr(parsed, "task_action", None) != "create":
            parser.error("usage: director-thin task create --subject SUBJECT")
        try:
            asyncio.run(create_task(workspace, parsed.subject, parsed.description, parsed.priority))
        except (RuntimeError, ValueError, OSError):
            logger.exception("Director task creation failed")
            return 1
        return 0

    if parsed.task_command == "console":
        from polaris.delivery.cli import terminal_console

        # The console subparser's --backend is opt-in (dest="console_backend");
        # fall back to the top-level --backend when it is not given explicitly.
        resolved_backend = parsed.console_backend if parsed.console_backend is not None else parsed.backend
        return int(
            terminal_console.run_director_console(
                workspace=str(Path(workspace).resolve()),
                role=str(parsed.role or "director"),
                backend=str(resolved_backend or "auto"),
                session_id=parsed.session_id,
                session_title=parsed.session_title,
            )
        )

    if parsed.task_command == "serve":
        try:
            asyncio.run(run_director_server(workspace, parsed.host, parsed.port))
        except (RuntimeError, ValueError, OSError):
            logger.exception("Director server terminated abnormally")
            return 1
        return 0

    try:
        ok = asyncio.run(run_director_console(workspace, parsed.iterations, parsed.max_workers))
    except (RuntimeError, ValueError, OSError):
        logger.exception("Director console run failed")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
