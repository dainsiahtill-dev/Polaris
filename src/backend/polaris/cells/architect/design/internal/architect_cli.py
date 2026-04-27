#!/usr/bin/env python3
"""Architect (Architect) Standalone CLI.

Migration note (Task #48 Phase 2):
    This CLI now routes through RoleRuntimeService instead of the deprecated
    ArchitectStandaloneAgent. All execution goes through RoleExecutionKernel.

Usage:
    python -m polaris.cells.architect.design.internal.architect_cli [options]
    python architect_cli.py --mode interactive --workspace ./myproject
    python architect_cli.py --mode autonomous --goal "Design a microservice architecture"
    python architect_cli.py --mode server --port 50001
"""

from __future__ import annotations

import asyncio
import sys

from polaris.cells.roles.runtime.public.service import (
    RoleRuntimeService,
    create_role_cli_parser,
)

__frozen__ = True


async def main() -> int:
    """Main entry point for Architect role agent."""
    parser = create_role_cli_parser("architect")
    args = parser.parse_args()

    # Build runtime service
    service = RoleRuntimeService()

    if args.mode == "tui":
        try:
            from polaris.cells.roles.runtime.public.service import run_tui

            run_tui(role="architect", workspace=args.workspace)
        except ImportError as e:
            print("Error: TUI mode requires textual")
            print("Install: pip install textual")
            print(f"Details: {e}")
            return 1

    elif args.mode == "interactive":
        await service.run_interactive(
            role="architect",
            workspace=args.workspace,
        )

    elif args.mode == "oneshot":
        if not args.goal:
            print("Error: --goal required for oneshot mode")
            return 1
        result = await service.run_oneshot(
            role="architect",
            workspace=args.workspace,
            goal=args.goal,
        )
        r = result.get("result", {})
        print(f"\nResult: {r.get('status', 'unknown')}")

    elif args.mode == "autonomous":
        if not args.goal:
            print("Error: --goal required for autonomous mode")
            return 1
        result = await service.run_autonomous(
            role="architect",
            workspace=args.workspace,
            goal=args.goal,
            max_iterations=args.max_iterations,
        )
        print(f"\nCompleted {result['iterations']} iterations")

    elif args.mode == "server":
        await service.run_server(
            role="architect",
            workspace=args.workspace,
            host=args.host,
            port=args.port,
        )

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
