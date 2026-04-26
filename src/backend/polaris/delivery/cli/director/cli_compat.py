"""Director CLI Compatibility Layer — canonical implementation.

This file is the canonical location for the Director thin CLI adapter.
The original ``cli_thin.py`` has been demoted to a deprecation shim.

Migration note:
    All callers should migrate from:
        ``polaris.delivery.cli.director.cli_thin``
    to the unified CLI entry point:
        ``python -m polaris.delivery.cli console``

Usage:
    python -m polaris.delivery.cli.director.cli_compat --workspace <path> [--iterations N]
    python -m polaris.delivery.cli.director.cli_compat serve [--host HOST] [--port PORT]
    python -m polaris.delivery.cli.director.cli_compat task create --subject "Task name"
    python -m polaris.delivery.cli.director.cli_compat console [--backend auto|plain]

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

    # Server options
    parser.add_argument(
        "serve",
        nargs="?",
        help="Start as long-running server",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Server bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=49978,
        help="Server port (default: 49978)",
    )

    # Task subcommand
    task_subparsers = parser.add_subparsers(dest="task_command", help="Task commands")
    task_create = task_subparsers.add_parser("create", help="Create a new task")
    task_create.add_argument("--subject", required=True, help="Task subject")
    task_create.add_argument("--description", help="Task description")
    task_create.add_argument("--priority", choices=["low", "medium", "high"], default="medium", help="Task priority")

    return parser


async def run_director_console(workspace: str, iterations: int, max_workers: int) -> None:
    """Run director in console mode."""
    _imports = _bootstrap_backend_import_path()
    run_mode = _imports[0]
    runtime_orchestrator = _imports[1]
    service_definition = _imports[2]

    orchestrator = runtime_orchestrator()
    service_def = service_definition(
        name="director",
        command=[
            sys.executable,
            "-m",
            "polaris.delivery.cli.director.cli_compat",
            "--workspace",
            workspace,
            "--iterations",
            str(iterations),
        ],
        workspace=Path(workspace),
        run_mode=run_mode.LOOP,
    )
    handle = await orchestrator.submit(service_def)
    try:
        await orchestrator.wait_for(handle, timeout=3600)
    finally:
        await orchestrator.terminate(handle)


async def run_director_server(workspace: str, host: str, port: int) -> None:
    """Run director in server mode."""
    _imports = _bootstrap_backend_import_path()
    run_mode = _imports[0]
    runtime_orchestrator = _imports[1]
    service_definition = _imports[2]
    enforce_utf8 = _imports[3]

    enforce_utf8()
    logger.info(f"Starting Director server on {host}:{port}...")
    logger.info("Workspace: %s", workspace)

    orchestrator = runtime_orchestrator()
    service_def = service_definition(
        name="director-server",
        command=[
            sys.executable,
            "-m",
            "polaris.delivery.cli.director.cli_compat",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
            "--workspace",
            workspace,
        ],
        workspace=Path(workspace),
        run_mode=run_mode.DAEMON,
    )
    handle = await orchestrator.submit(service_def)
    try:
        await orchestrator.wait_for(handle, timeout=None)
    except KeyboardInterrupt:
        logger.info("Shutting down Director server...")
    finally:
        await orchestrator.terminate(handle)


async def create_task(workspace: str, subject: str, description: str, priority: str) -> None:
    """Create a new task via director."""
    from polaris.cells.director.execution.public.service import DirectorService

    service = DirectorService(config=None)  # type: ignore[call-arg,arg-type]
    result = await service.create_task(
        subject=subject,
        description=description or "",
        priority=priority,
    )
    if result.ok:
        logger.info("Task created: %s", result.task_id)
    else:
        logger.error("Failed to create task: %s", result.error)


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

    if parsed.task_command == "create":
        asyncio.run(create_task(workspace, parsed.subject, parsed.description, parsed.priority))
        return 0

    if parsed.serve == "serve" or parsed.task_command == "serve":
        asyncio.run(run_director_server(workspace, parsed.host, parsed.port))
        return 0

    asyncio.run(run_director_console(workspace, parsed.iterations, parsed.max_workers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
