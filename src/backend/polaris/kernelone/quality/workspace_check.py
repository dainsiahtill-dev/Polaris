"""Deterministic workspace source check — honest verify for non-Python shapes.

``python -m polaris.kernelone.quality.workspace_check --workspace .`` walks the
workspace, runs the single-source-of-truth syntax/completeness checker over
every source file, and exits nonzero on the first class of defects found.

Motivation (live factory-bench L2-11 r4): a pure-web workspace has no Python
files, so the integration-QA fallback ``python -m compileall -q .`` passes
vacuously — a truncated, non-functional HTML app earned a
``real_command_passed`` QA verdict. This command gives such workspaces a real
deterministic gate without inventing per-project tooling.
"""

from __future__ import annotations

import argparse
import os
import sys

from polaris.kernelone.quality.artifact_quality import check_source_file_syntax

_SKIP_DIRS = {".git", ".polaris", "__pycache__", "node_modules", ".venv", "venv", "runtime", "dist", "build"}
_CHECK_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".json", ".html", ".htm")
_MAX_FILES = 5000


def run_workspace_check(workspace: str) -> tuple[int, list[str]]:
    """Return (checked_count, failures). Failures are 'path: error' lines."""
    failures: list[str] = []
    checked = 0
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in sorted(filenames):
            if not filename.lower().endswith(_CHECK_SUFFIXES):
                continue
            if checked >= _MAX_FILES:
                return checked, failures
            full_path = os.path.join(current_root, filename)
            result = check_source_file_syntax(full_path)
            if result is None:
                continue
            checked += 1
            if result.get("ok") is False:
                rel = os.path.relpath(full_path, workspace)
                failures.append(f"{rel}: {result.get('error') or 'syntax error'!s}")
    return checked, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polaris deterministic workspace source check")
    parser.add_argument("--workspace", default=".")
    args = parser.parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        print(f"workspace check failed: not a directory: {workspace}")
        return 2
    checked, failures = run_workspace_check(workspace)
    if failures:
        print(f"workspace check failed: {len(failures)} defective source file(s) of {checked} checked")
        for line in failures[:20]:
            print(f"  - {line}")
        return 1
    print(f"workspace check passed: {checked} source file(s) checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
