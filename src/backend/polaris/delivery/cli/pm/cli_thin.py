"""PM CLI Thin Adapter - Phase 4 Refactoring.

This is the refactored thin CLI adapter for PM that delegates all
business logic to the core orchestration layer.

Usage:
    python -m polaris.delivery.cli.pm.cli_thin --workspace <path> [--loop]
    python -m polaris.delivery.cli.pm.cli_thin --workspace <path> --start-from architect --directive "..."

Architecture:
    - CLI Layer: Argument parsing only (this file)
    - Core Layer: ProcessLauncher, RuntimeOrchestrator (orchestration/)
    - Domain Layer: ConfigSnapshot, ProcessLaunchRequest
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
    """Create thin argument parser - only essential CLI args."""
    parser = argparse.ArgumentParser(
        prog="pm-thin",
        description="Polaris PM - Thin CLI Adapter",
    )

    # Essential workspace config
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=os.getcwd(),
        help="Workspace directory",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run in loop mode",
    )
    parser.add_argument(
        "--directive",
        type=str,
        default=None,
        help="Directive to execute",
    )
    parser.add_argument(
        "--start-from",
        type=str,
        choices=["pm", "architect", "director", "qa"],
        default="pm",
        help="Starting role",
    )
    return parser


async def run_pm_console(workspace: str, loop: bool, directive: str | None) -> None:
    """Run PM in console mode."""
    RunMode, RuntimeOrchestrator, ServiceDefinition, _ = _bootstrap_backend_import_path()  # noqa: N806

    orchestrator = RuntimeOrchestrator()
    service_def = ServiceDefinition(
        name="pm",
        command=[
            sys.executable,
            "-m",
            "polaris.delivery.cli.pm.cli_thin",
            "--workspace",
            workspace,
        ],
        workspace=Path(workspace),
        run_mode=RunMode.LOOP if loop else RunMode.SINGLE,
    )
    handle = await orchestrator.submit(service_def)
    try:
        await orchestrator.wait_for(handle, timeout=3600 if not loop else None)
    finally:
        await orchestrator.terminate(handle)


def main() -> int:
    args = sys.argv[1:]
    parser = create_parser()
    parsed = parser.parse_args(args)

    workspace = str(parsed.workspace or os.getcwd())

    asyncio.run(run_pm_console(workspace, parsed.loop, parsed.directive))
    return 0


if __name__ == "__main__":
    sys.exit(main())
