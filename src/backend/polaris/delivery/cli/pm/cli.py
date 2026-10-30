"""pm entry using the shared engineering service; old worker protocols are retired."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from polaris.delivery.cli.engineering import add_engineering_arguments, run_engineering_action
from polaris.kernelone.fs.encoding import enforce_utf8


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polaris-pm", description="Discuss a goal or explicitly execute/resume an engineering run."
    )
    add_engineering_arguments(parser)
    parser.add_argument("--workspace", default=str(Path.cwd()))
    return parser


def main() -> int:
    args = create_parser().parse_args(sys.argv[1:])
    enforce_utf8()
    return asyncio.run(run_engineering_action(args, role="pm"))


if __name__ == "__main__":
    raise SystemExit(main())
