#!/usr/bin/env python3
"""Chief Engineer (Chief Engineer) Standalone CLI.

Migration note (Task #48 Phase 2):
    This CLI now routes through RoleRuntimeService instead of the deprecated
    ChiefEngineerStandaloneAgent. All execution goes through RoleExecutionKernel.

Usage:
    python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli [options]
    python chief_engineer_cli.py --mode interactive --workspace ./myproject
    python chief_engineer_cli.py --mode autonomous --goal "Create implementation blueprint for auth module"
    python chief_engineer_cli.py --mode server --port 50002
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
    """Main entry point for Chief Engineer role agent."""
    parser = create_role_cli_parser("chief_engineer")
    args = parser.parse_args()

    service = RoleRuntimeService()

    if args.mode == "tui":
        try:
            from polaris.cells.roles.runtime.public.service import run_tui

            run_tui(role="chief_engineer", workspace=args.workspace)
        except ImportError as e:
            print("Error: TUI mode requires textual")
            print("Install: pip install textual")
            print(f"Details: {e}")
            return 1

    elif args.mode == "interactive":
        welcome = """
╔══════════════════════════════════════════════════════════════╗
║  Chief Engineer (Chief Engineer) - Implementation Planning Agent     ║
╠══════════════════════════════════════════════════════════════╣
║  Responsibilities:                                          ║
║    • Analyze codebase structure                              ║
║    • Create implementation blueprints                        ║
║    • Define file organization                              ║
║    • Generate construction plans                            ║
╚══════════════════════════════════════════════════════════════╝

Type /quit to exit, /help for commands
"""
        await service.run_interactive(
            role="chief_engineer",
            workspace=args.workspace,
            welcome_message=welcome,
        )

    elif args.mode == "oneshot":
        if not args.goal:
            print("Error: --goal required for oneshot mode")
            return 1
        result = await service.run_oneshot(
            role="chief_engineer",
            workspace=args.workspace,
            goal=args.goal,
        )
        r = result.get("result", {})
        print("\nBlueprint generated:")
        print(f"  Status: {r.get('status', 'unknown')}")

    elif args.mode == "autonomous":
        if not args.goal:
            print("Error: --goal required for autonomous mode")
            return 1
        result = await service.run_autonomous(
            role="chief_engineer",
            workspace=args.workspace,
            goal=args.goal,
            max_iterations=args.max_iterations,
        )
        print(f"\nCompleted {result['iterations']} planning iterations")

    elif args.mode == "server":
        await service.run_server(
            role="chief_engineer",
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
