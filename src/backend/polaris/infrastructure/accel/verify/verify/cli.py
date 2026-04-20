"""CLI entry point for verify orchestrator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

from .core import run_verify


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Run verification commands for a project",
        prog="verify",
    )
    parser.add_argument(
        "workspace",
        type=str,
        help="Project workspace directory",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config file (JSON)",
    )
    parser.add_argument(
        "--changed-files",
        type=str,
        nargs="*",
        default=None,
        help="List of changed files for incremental verification",
    )
    parser.add_argument(
        "--output-format",
        type=str,
        choices=["json", "markdown", "html"],
        default="json",
        help="Output format for results",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first failure",
    )
    parser.add_argument(
        "--parallel-jobs",
        type=int,
        default=4,
        help="Number of parallel jobs",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1200,
        help="Per-command timeout in seconds",
    )
    return parser.parse_args(argv)


def build_config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Build runtime config from CLI arguments."""
    return {
        "runtime": {
            "accel_home": str(Path.home() / ".accel"),
            "verify_fail_fast": args.fail_fast,
            "max_workers": args.parallel_jobs,
            "per_command_timeout_seconds": args.timeout,
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    args = parse_args(argv)
    project_dir = Path(args.workspace).resolve()
    if not project_dir.exists():
        print(f"Error: workspace does not exist: {project_dir}", file=sys.stderr)
        return 1
    config = build_config_from_args(args)
    changed_files = args.changed_files if args.changed_files else None
    result = run_verify(project_dir, config, changed_files)
    print(f"Verification complete: {result['status']} (exit_code={result['exit_code']})")
    return cast("int", result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "build_config_from_args",
    "main",
    "parse_args",
]
