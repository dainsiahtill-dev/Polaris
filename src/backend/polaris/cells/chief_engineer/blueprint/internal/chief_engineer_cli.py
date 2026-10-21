"""Chief Engineer advice through the same engineering service as other roles."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from polaris.delivery.cli.engineering import add_engineering_arguments, run_engineering_action
from polaris.kernelone.fs.encoding import enforce_utf8


def create_role_cli_parser(role: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"polaris-{role}", description="Discuss a goal or explicitly run the selected engineering role."
    )
    add_engineering_arguments(parser)
    parser.add_argument("--workspace", default=str(Path.cwd()))
    return parser


async def main() -> int:
    args = create_role_cli_parser("chief_engineer").parse_args()
    enforce_utf8()
    return await run_engineering_action(args, role="chief_engineer")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
