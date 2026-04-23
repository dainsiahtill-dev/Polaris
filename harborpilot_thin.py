#!/usr/bin/env python3
"""Polaris Thin CLI Adapter - Phase 4 Refactoring.

Unified CLI entry point using thin adapter pattern.
All business logic is delegated to core orchestration layer.

Usage:
    python polaris_thin.py <command> [options]
    hp-thin <command> [options]  (if installed)

Commands:
    pm          PM 项目管理 (thin adapter)
    director    Director 任务执行 (thin adapter)
    backend     启动 FastAPI 后端
    status      查看项目状态

Architecture:
    - CLI Layer: Argument parsing only (this file)
    - Core Layer: RuntimeOrchestrator, BackendBootstrapper
    - Domain Layer: ServiceDefinition, BackendLaunchRequest
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# Project paths
PROJECT_ROOT = Path(__file__).parent.absolute()
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
FRONTEND_DIR = PROJECT_ROOT / "src" / "frontend"


def setup_pythonpath():
    """Setup Python path for imports."""
    paths = [
        str(BACKEND_DIR),
        str(BACKEND_DIR / "scripts"),
        str(PROJECT_ROOT),
    ]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)


setup_pythonpath()


# Import core orchestration (new architecture)
from core.orchestration import RuntimeOrchestrator, ServiceDefinition
from application.dto.process_launch import RunMode


class PolarisThinCLI:
    """Thin CLI adapter for Polaris unified interface."""

    def __init__(self):
        self.orchestrator: Optional[RuntimeOrchestrator] = None

    async def __aenter__(self) -> PolarisThinCLI:
        self.orchestrator = RuntimeOrchestrator()
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def run_pm(self, args: List[str]) -> int:
        """Run PM via thin adapter."""
        # Delegate to pm.cli_thin
        cmd = [
            sys.executable, "-m",
            "scripts.pm.cli_thin",
        ] + args

        # If using new architecture, use orchestrator
        if os.environ.get("KERNELONE_USE_THIN_CLI", "1") == "1":
            definition = ServiceDefinition(
                name="pm-thin",
                command=cmd,
                working_dir=PROJECT_ROOT,
                run_mode=RunMode.ONE_SHOT,
                env_vars=self._build_env(),
            )

            handle = await self.orchestrator.submit(definition)
            completed = await self.orchestrator.wait_for_completion(handle)
            return 0 if completed.is_completed else 1
        else:
            # Legacy path
            return subprocess.run(cmd).returncode

    async def run_director(self, args: List[str]) -> int:
        """Run Director via thin adapter."""
        # Delegate to director.cli_thin
        cmd = [
            sys.executable, "-m",
            "scripts.director.cli_thin",
        ] + args

        if os.environ.get("KERNELONE_USE_THIN_CLI", "1") == "1":
            definition = ServiceDefinition(
                name="director-thin",
                command=cmd,
                working_dir=PROJECT_ROOT,
                run_mode=RunMode.ONE_SHOT,
                env_vars=self._build_env(),
            )

            handle = await self.orchestrator.submit(definition)
            completed = await self.orchestrator.wait_for_completion(handle)
            return 0 if completed.is_completed else 1
        else:
            return subprocess.run(cmd).returncode

    async def run_backend(self, host: str = "127.0.0.1", port: int = 49977, reload: bool = False) -> int:
        """Run FastAPI backend using BackendBootstrapper."""
        cmd = [
            sys.executable, "-m",
            "backend.server",
            "--host", host,
            "--port", str(port),
        ]
        if reload:
            cmd.append("--reload")

        definition = ServiceDefinition(
            name="polaris-backend",
            command=cmd,
            working_dir=PROJECT_ROOT,
            run_mode=RunMode.DAEMON,
            env_vars=self._build_env(),
        )

        print(f"[polaris-thin] Starting backend on {host}:{port}")

        handle = await self.orchestrator.submit(definition)

        print(f"[polaris-thin] Backend started (handle: {handle.id})")
        print(f"[polaris-thin] API: http://{host}:{port}")
        print("Press Ctrl+C to stop...")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n[polaris-thin] Stopping backend...")
            await self.orchestrator.terminate(handle, timeout=10.0)
            print("[polaris-thin] Backend stopped")

        return 0

    async def run_dev(self) -> int:
        """Run development mode."""
        print("=== Polaris Development Mode ===")
        print()

        try:
            cmd = ["npm", "run", "dev"]
            print(f"Running: {' '.join(cmd)}")
            return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode
        except FileNotFoundError:
            print("npm not found. Please ensure Node.js is installed.")
            return 1

    async def run_status(self) -> int:
        """Show project status via orchestrator."""
        print("=== Polaris Project Status ===")
        print()

        active = self.orchestrator.list_active()

        print(f"Active services: {len(active)}")
        for svc in active:
            print(f"  - {svc.id}: {svc.definition.name} ({svc.state.value})")

        return 0

    def _build_env(self) -> dict[str, str]:
        """Build environment variables."""
        return {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "KERNELONE_WORKSPACE": str(PROJECT_ROOT),
            "KERNELONE_USE_THIN_CLI": os.environ.get("KERNELONE_USE_THIN_CLI", "1"),
        }


def create_parser() -> argparse.ArgumentParser:
    """Create thin argument parser."""
    parser = argparse.ArgumentParser(
        prog="polaris-thin",
        description="Polaris - Thin CLI Adapter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # PM management
  python polaris_thin.py pm --workspace . --loop
  python polaris_thin.py pm --start-from architect --directive "Create API"

  # Director execution
  python polaris_thin.py director --workspace . --iterations 3

  # Backend
  python polaris_thin.py backend --port 49977

  # Development mode
  python polaris_thin.py dev
        """
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0 (thin)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # PM command
    pm_parser = subparsers.add_parser("pm", help="PM project management")
    pm_parser.add_argument("remainder", nargs=argparse.REMAINDER,
                          help="Arguments passed to PM CLI")

    # Director command
    director_parser = subparsers.add_parser("director", help="Director task execution")
    director_parser.add_argument("remainder", nargs=argparse.REMAINDER,
                                help="Arguments passed to Director CLI")

    # Backend command
    backend_parser = subparsers.add_parser("backend", help="Start FastAPI backend")
    backend_parser.add_argument("--host", "-H", default="127.0.0.1")
    backend_parser.add_argument("--port", "-p", type=int, default=49977)
    backend_parser.add_argument("--reload", action="store_true")

    # Dev command
    dev_parser = subparsers.add_parser("dev", help="Development mode")

    # Status command
    status_parser = subparsers.add_parser("status", help="Show project status")

    return parser


async def main_async(argv: Optional[List[str]] = None) -> int:
    """Async main entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    async with PolarisThinCLI() as cli:
        if args.command == "pm":
            return await cli.run_pm(args.remainder)
        elif args.command == "director":
            return await cli.run_director(args.remainder)
        elif args.command == "backend":
            return await cli.run_backend(args.host, args.port, args.reload)
        elif args.command == "dev":
            return await cli.run_dev()
        elif args.command == "status":
            return await cli.run_status()
        else:
            parser.print_help()
            return 1


def main(argv: Optional[List[str]] = None) -> int:
    """Synchronous entry point."""
    try:
        return asyncio.run(main_async(argv))
    except KeyboardInterrupt:
        print("\n[polaris-thin] Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
