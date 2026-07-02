#!/usr/bin/env python3
"""Migrate historical Polaris artifact path aliases to canonical paths.

Default mode is dry-run. Pass ``--apply`` to copy UTF-8 text artifacts from
historical locations into their canonical runtime paths.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve()
_BACKEND_ROOT = _SCRIPT_PATH.parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from polaris.cells.audit.verdict.internal.artifact_service import migrate_historical_artifact_aliases  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Workspace whose historical artifacts should be migrated.")
    parser.add_argument("--cache-root", default="", help="Optional runtime cache root used by this workspace.")
    parser.add_argument("--apply", action="store_true", help="Copy files. Without this flag the command is dry-run.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing canonical files.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = migrate_historical_artifact_aliases(
        workspace=args.workspace,
        cache_root=args.cache_root,
        dry_run=not bool(args.apply),
        overwrite=bool(args.overwrite),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
