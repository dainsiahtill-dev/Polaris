"""Polaris backend server entry point.

This is the thin CLI adapter layer for starting the Polaris backend.
All business logic is delegated to the BackendBootstrapper in polaris.bootstrap.

Usage:
    python server.py [--host HOST] [--port PORT] [--workspace PATH]

Environment Variables:
    KERNELONE_WORKSPACE: Default workspace path
    KERNELONE_BACKEND_PORT: Default port
    KERNELONE_LOG_LEVEL: Logging level
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# ─── Early env-var normalization (must run before any polaris.kernelone import)
from polaris._env_compat import normalize_env_prefix

normalize_env_prefix()
# ────────────────────────────────────────────────────────────────────────────

from polaris.bootstrap import BackendBootstrapper  # noqa: E402
from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest  # noqa: E402


def main() -> int:
    """Main entry point for backend server.

    Parses CLI arguments and delegates to BackendBootstrapper.

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = argparse.ArgumentParser(
        description="Polaris Backend Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Auto-select port, use current directory
  %(prog)s --port 8080               # Use specific port
  %(prog)s --workspace /path/to/proj  # Use specific workspace
        """,
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Server bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Server port (0 for auto-select, default: 0)",
    )
    parser.add_argument(
        "--cors-origins",
        default="",
        help="Comma-separated CORS origins",
    )
    parser.add_argument(
        "--token",
        default="",
        help="Authentication token (auto-generated if not provided)",
    )
    parser.add_argument(
        "--workspace",
        default="",
        help="Workspace path (default: current directory)",
    )
    parser.add_argument(
        "--ramdisk-root",
        default="",
        help="Ramdisk root path for runtime files",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: info)",
    )
    parser.add_argument(
        "--debug-tracing",
        action="store_true",
        help="Enable debug tracing",
    )
    parser.add_argument(
        "--self-upgrade-mode",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow Polaris meta-project to be used as the target workspace",
    )

    args = parser.parse_args()

    # Resolve workspace
    workspace = Path(args.workspace).resolve() if args.workspace else Path.cwd()

    # Parse CORS origins
    cors_origins = None
    if args.cors_origins:
        cors_origins = [o.strip() for o in args.cors_origins.split(",") if o.strip()]

    return asyncio.run(_run_bootstrap(args, workspace, cors_origins))


async def _run_bootstrap(
    args: argparse.Namespace,
    workspace: Path,
    cors_origins: list[str] | None,
) -> int:
    """Run using BackendBootstrapper.

    Args:
        args: Parsed CLI arguments
        workspace: Resolved workspace path
        cors_origins: Parsed CORS origins

    Returns:
        Exit code
    """
    # Build launch request
    request = BackendLaunchRequest(
        host=args.host,
        port=args.port,
        workspace=workspace,
        explicit_workspace=bool(args.workspace),
        cors_origins=cors_origins or [],
        token=args.token or None,
        ramdisk_root=Path(args.ramdisk_root) if args.ramdisk_root else None,
        log_level=args.log_level,
        debug_tracing=args.debug_tracing,
        self_upgrade_mode=args.self_upgrade_mode,
    )

    # Validate request
    validation = request.validate()
    if not validation.is_valid:
        print("Configuration validation failed:", file=sys.stderr)
        for error in validation.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    # Bootstrap
    bootstrapper = BackendBootstrapper()
    result = await bootstrapper.bootstrap(request)

    if not result.is_success():
        print(f"Bootstrap failed: {result.get_error()}", file=sys.stderr)
        return 1

    # Keep running until interrupted
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        await bootstrapper.shutdown(result.process_handle)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
