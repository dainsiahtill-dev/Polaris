#!/usr/bin/env python3
"""Chief Engineer (Chief Engineer) Standalone CLI.

Use --engine engineering_v2 for task-free discussion or explicit engineering
execution. Legacy modes retain their pre-switch behavior until acceptance.

Usage:
    python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli [options]
    python chief_engineer_cli.py --mode interactive --workspace ./myproject
    python chief_engineer_cli.py --mode autonomous --goal "Create implementation blueprint for auth module"
    python chief_engineer_cli.py --mode server --port 50002
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any


def create_role_cli_parser(role: str) -> argparse.ArgumentParser:
    """Parse the explicit new entry without importing the legacy role runtime."""
    from polaris.delivery.cli.engineering import add_engineering_arguments

    parser = argparse.ArgumentParser(description=f"Polaris {role} CLI")
    parser.add_argument("--workspace", default=".")
    parser.add_argument(
        "--mode", choices=("tui", "interactive", "oneshot", "autonomous", "server"), default="interactive"
    )
    parser.add_argument("--goal", default="")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50000)
    parser.add_argument("--model", default=None)
    add_engineering_arguments(parser)
    return parser


def _legacy_role_runtime_service() -> Any:
    """Lazy compatibility construction, used only by pre-switch legacy modes."""
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService as LegacyRoleRuntimeService

    return LegacyRoleRuntimeService()


RoleRuntimeService = _legacy_role_runtime_service

__frozen__ = True


async def main() -> int:
    """Main entry point for Chief Engineer role agent."""
    parser = create_role_cli_parser("chief_engineer")
    args = parser.parse_args()

    if args.engine == "engineering_v2":
        from polaris.delivery.cli.engineering import run_engineering_action

        return await run_engineering_action(args, role="chief_engineer")

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
