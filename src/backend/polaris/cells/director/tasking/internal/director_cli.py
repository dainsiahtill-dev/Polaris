#!/usr/bin/env python3
"""Director (engineer) Standalone CLI - Code Generation Agent.

Migration note (Task #48 Phase 2):
    This CLI now routes through RoleRuntimeService instead of the deprecated
    DirectorStandaloneAgent. All execution goes through RoleExecutionKernel.

Usage:
    python -m polaris.cells.director.execution.internal.director_cli [options]
    python director_cli.py --mode interactive --workspace ./myproject
    python director_cli.py --mode oneshot --goal "Implement a user authentication function"
    python director_cli.py --mode autonomous --goal "Create a REST API" --max-iterations 5
    python director_cli.py --mode server --port 50003

Examples:
    # Interactive coding session
    python director_cli.py --workspace ./src

    # Generate code for a specific task
    python director_cli.py --mode oneshot --goal "Create a Python class for User management" \
                           --context '{"tech_stack": "python", "target_file": "user.py"}'

    # Start API server for IDE integration
    python director_cli.py --mode server --port 50003
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from polaris.cells.roles.runtime.public.service import (
    RoleRuntimeService,
    create_role_cli_parser,
)

logger = logging.getLogger(__name__)

__frozen__ = True


async def main() -> int:
    """Main entry point for Director standalone agent."""
    parser = create_role_cli_parser("director")

    # Add Director-specific arguments
    parser.add_argument(
        "--context",
        type=str,
        help="JSON context for the task (files, tech_stack, etc.)",
    )

    args = parser.parse_args()

    # Parse context if provided
    context: dict[str, object] | None = None
    if args.context:
        try:
            context = json.loads(args.context)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON context: {e}")
            return 1

    service = RoleRuntimeService()

    if args.mode == "tui":
        try:
            from polaris.cells.roles.runtime.public.service import run_tui

            run_tui(role="director", workspace=args.workspace)
        except ImportError as e:
            print("Error: TUI mode requires textual")
            print("Install: pip install textual")
            print(f"Details: {e}")
            return 1

    elif args.mode == "interactive":
        welcome = """
╔══════════════════════════════════════════════════════════════╗
║  Director (engineer) - Code Generation Agent                      ║
╠══════════════════════════════════════════════════════════════╣
║  Responsibilities:                                          ║
║    • Write clean, working code                             ║
║    • Implement functions and classes                         ║
║    • Write tests                                            ║
║    • Fix bugs                                               ║
╠══════════════════════════════════════════════════════════════╣
║  Commands:                                                  ║
║    /quit  /help  /status                                   ║
╚══════════════════════════════════════════════════════════════╝
"""
        await service.run_interactive(
            role="director",
            workspace=args.workspace,
            welcome_message=welcome,
        )

    elif args.mode == "oneshot":
        if not args.goal:
            print("Error: --goal required for oneshot mode")
            print("Example: --goal 'Implement a function to calculate fibonacci'")
            return 1

        print(f"\nGoal: {args.goal}")
        if context:
            print(f"Context: {json.dumps(context, indent=2)}")

        result = await service.run_oneshot(
            role="director",
            workspace=args.workspace,
            goal=args.goal,
            context=context,
        )

        print("\nResult:")
        r = result.get("result", {})
        print(f"  Status: {r.get('status', 'unknown')}")
        output = r.get("output", "")
        if output:
            print(f"  Output: {output[:200]}")

    elif args.mode == "autonomous":
        if not args.goal:
            print("Error: --goal required for autonomous mode")
            return 1

        print("\nStarting autonomous mode")
        print(f"Goal: {args.goal}")
        print(f"Max iterations: {args.max_iterations}")

        result = await service.run_autonomous(
            role="director",
            workspace=args.workspace,
            goal=args.goal,
            max_iterations=args.max_iterations,
            context=context,
        )

        print(f"\nCompleted {result['iterations']} iterations")
        final = result.get("final_result")
        if final:
            print(f"Final status: {final.get('status', 'unknown')}")

    elif args.mode == "server":
        print(f"\nStarting Director API server on {args.host}:{args.port}")
        print("Endpoints:")
        print(f"  POST http://{args.host}:{args.port}/chat     - Chat with Director")
        print(f"  POST http://{args.host}:{args.port}/execute  - Execute task")
        print(f"  GET  http://{args.host}:{args.port}/status   - Get status")
        await service.run_server(
            role="director",
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
        print("\n\nGoodbye!")
        sys.exit(130)
