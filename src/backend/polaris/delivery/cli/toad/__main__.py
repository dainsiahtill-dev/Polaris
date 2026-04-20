"""Command-line entry point for the minimal Polaris toad host."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from polaris.kernelone.fs.encoding import enforce_utf8

from .app import run_toad


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toad",
        description="Minimal toad-compatible Polaris role console",
    )
    parser.add_argument("--workspace", "-w", default=os.getcwd(), help="Workspace directory")
    parser.add_argument("--role", default="director", help="Active role id")
    parser.add_argument(
        "--backend",
        choices=["auto", "plain"],
        default="auto",
        help="Console backend",
    )
    parser.add_argument("--session-id", default="", help="Existing role session id")
    parser.add_argument("--session-title", default="", help="New session title")
    parser.add_argument(
        "--prompt-style",
        choices=["plain", "omp"],
        default="plain",
        help="Prompt rendering style",
    )
    parser.add_argument("--omp-config", default="", help="Optional Oh My Posh config path")
    parser.add_argument(
        "--json-render",
        choices=["raw", "pretty", "pretty-color"],
        default="raw",
        help="Tool event JSON rendering mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    enforce_utf8()
    args = build_parser().parse_args(argv)
    return run_toad(
        workspace=Path(args.workspace).resolve(),
        role=args.role,
        backend=args.backend,
        session_id=str(args.session_id or "").strip() or None,
        session_title=str(args.session_title or "").strip() or None,
        prompt_style=args.prompt_style,
        omp_config=str(args.omp_config or "").strip() or None,
        json_render=args.json_render,
    )


if __name__ == "__main__":
    raise SystemExit(main())
