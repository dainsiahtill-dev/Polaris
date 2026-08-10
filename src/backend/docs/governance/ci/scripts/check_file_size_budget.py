#!/usr/bin/env python3
"""Governance gate: non-test Python source must stay under a file-size budget.

Policy rationale
----------------
Large files are the leading cause of regressing seams in this codebase: the
2026-06-20 large-file refactor campaign (memory: large-file-refactor-campaign)
split 19/20 files over 2000 lines, but several regrew because nothing prevented
re-accumulation. This gate makes the budget durable.

Rules
-----
1. Every non-test ``*.py`` under the backend root (``src/backend``) must stay
   at or below ``--budget`` lines (default 2000). Tests (``tests/`` dirs,
   ``test_*`` filenames), generated code (``generated/``, ``*_pb2.py``), the
   ``__pycache__`` cache and governance meta-scripts under ``docs/`` are out of
   scope — the gate measures product source only.
2. Existing offenders are grandfathered through a frozen baseline
   (``docs/governance/ci/file_size_baseline.json``) that records each file's
   exact line count at adoption time. A grandfathered file is allowed to stay
   at its frozen size, or shrink. **Any growth beyond the frozen count fails.**
3. A NEW file over budget that is not in the baseline fails (no backsliding on
   new code).
4. Stale baseline entries (file shrunk below budget or removed) are reported
   as eligible for cleanup; they never fail the gate on their own — successful
   shrinks must be encouraged, not penalised.

Use ``--update-baseline`` to (re)snapshot the baseline after an intentional,
gate-verified shrink or the initial adoption.

Run::

    python docs/governance/ci/scripts/check_file_size_budget.py [--root SRC_BACKEND] \
        [--budget 2000] [--baseline docs/governance/ci/file_size_baseline.json] \
        [--update-baseline]

Exit codes: 0 = clean, 1 = violations, 2 = configuration / IO error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# Path segments whose subtrees are out of scope (product source only).
_DEFAULT_EXCLUDED_PARTS: frozenset[str] = frozenset(
    {"tests", "generated", "__pycache__", "docs", ".git", "node_modules", ".venv", "venv"}
)
# Filenames starting with these prefixes are treated as tests and excluded.
_DEFAULT_EXCLUDED_FILENAME_PREFIXES: tuple[str, ...] = ("test_",)
# Generated protobuf stubs etc.
_EXCLUDED_FILENAME_SUFFIXES: tuple[str, ...] = ("_pb2.py", "_pb2_grpc.py", ".pyi")

DEFAULT_BUDGET = 2000
DEFAULT_BASELINE_REL = "docs/governance/ci/file_size_baseline.json"


@dataclass(frozen=True, slots=True)
class FileSizeViolation:
    """A single file-size budget violation."""

    rel_path: str
    lines: int
    budget: int
    baseline_lines: int | None
    kind: str  # "new_oversized" | "regrowth"


def iter_source_files(
    root: Path,
    *,
    excluded_parts: frozenset[str] | set[str],
    excluded_filename_prefixes: Iterable[str],
) -> Iterator[Path]:
    """Yield every in-scope ``*.py`` file under ``root``.

    Excluded by path segment (``tests``, ``generated``, ``docs`` …), by filename
    prefix (``test_``) and by generated-stub suffix (``_pb2.py`` …).
    """

    prefix_tuple = tuple(excluded_filename_prefixes)
    for path in sorted(root.rglob("*.py")):
        if excluded_parts & set(path.parts):
            continue
        name = path.name
        if name.startswith(prefix_tuple):
            continue
        if name.endswith(_EXCLUDED_FILENAME_SUFFIXES):
            continue
        yield path


def count_lines(path: Path) -> int:
    """Return the number of lines in ``path`` (UTF-8, ``splitlines`` semantics)."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(text.splitlines())


def load_baseline(baseline_path: Path) -> dict[str, int]:
    """Load the frozen baseline map (repo-relative path -> line count)."""

    if not baseline_path.is_file():
        return {}
    try:
        raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, int):
            result[key] = value
    return result


def find_violations(
    root: Path,
    *,
    budget: int,
    baseline: dict[str, int],
    excluded_parts: frozenset[str] | set[str],
    excluded_filename_prefixes: Iterable[str],
) -> list[FileSizeViolation]:
    """Return every file-size budget violation under ``root``.

    - ``new_oversized``: file over budget and absent from the baseline.
    - ``regrowth``: grandfathered file that grew beyond its frozen baseline count.
    """

    violations: list[FileSizeViolation] = []
    for path in iter_source_files(
        root,
        excluded_parts=excluded_parts,
        excluded_filename_prefixes=excluded_filename_prefixes,
    ):
        rel_path = path.relative_to(root).as_posix()
        lines = count_lines(path)
        if lines <= budget:
            continue
        frozen = baseline.get(rel_path)
        if frozen is None:
            violations.append(FileSizeViolation(rel_path, lines, budget, None, "new_oversized"))
        elif lines > frozen:
            violations.append(FileSizeViolation(rel_path, lines, budget, frozen, "regrowth"))
    return violations


def find_stale_baseline_entries(
    root: Path,
    *,
    budget: int,
    baseline: dict[str, int],
    excluded_parts: frozenset[str] | set[str],
    excluded_filename_prefixes: Iterable[str],
) -> list[str]:
    """Return baseline entries eligible for cleanup (file missing or now under budget)."""

    on_disk = {
        path.relative_to(root).as_posix(): count_lines(path)
        for path in iter_source_files(
            root,
            excluded_parts=excluded_parts,
            excluded_filename_prefixes=excluded_filename_prefixes,
        )
    }
    stale: list[str] = []
    for rel_path, _frozen_lines in baseline.items():
        current = on_disk.get(rel_path)
        if current is None or current <= budget:
            stale.append(rel_path)
    return sorted(stale)


def write_baseline(
    root: Path,
    baseline_path: Path,
    *,
    budget: int,
    excluded_parts: frozenset[str] | set[str],
    excluded_filename_prefixes: Iterable[str],
) -> dict[str, int]:
    """Snapshot every over-budget file at its current count into ``baseline_path``."""

    snapshot: dict[str, int] = {}
    for path in iter_source_files(
        root,
        excluded_parts=excluded_parts,
        excluded_filename_prefixes=excluded_filename_prefixes,
    ):
        rel_path = path.relative_to(root).as_posix()
        lines = count_lines(path)
        if lines > budget:
            snapshot[rel_path] = lines
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(dict(sorted(snapshot.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return snapshot


def _default_backend_root() -> Path:
    # docs/governance/ci/scripts/check_file_size_budget.py -> parents[4] == src/backend
    return Path(__file__).resolve().parents[4]


def _format_violation(v: FileSizeViolation) -> str:
    if v.kind == "regrowth":
        delta = v.lines - (v.baseline_lines or 0)
        return (
            f"  {v.rel_path}: {v.lines} lines (baseline {v.baseline_lines}, +{delta}) "
            f"— grandfathered file grew beyond its frozen count"
        )
    return f"  {v.rel_path}: {v.lines} lines (budget {v.budget}) — new oversized file, not in baseline"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enforce a per-file line budget on non-test backend Python source.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Backend root to scan (default: the src/backend containing this script).",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET,
        help=f"Maximum lines permitted per file (default {DEFAULT_BUDGET}).",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help=f"Baseline JSON path (default {DEFAULT_BASELINE_REL} relative to root).",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Re-snapshot the baseline at current counts instead of enforcing it.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _default_backend_root()
    if not root.is_dir():
        print(f"[check_file_size_budget] backend root not found: {root}", file=sys.stderr)
        return 2

    baseline_rel = args.baseline or DEFAULT_BASELINE_REL
    baseline_path = (root / baseline_rel).resolve()

    if args.update_baseline:
        snapshot = write_baseline(
            root,
            baseline_path,
            budget=args.budget,
            excluded_parts=_DEFAULT_EXCLUDED_PARTS,
            excluded_filename_prefixes=_DEFAULT_EXCLUDED_FILENAME_PREFIXES,
        )
        print(
            f"[check_file_size_budget] baseline written: {len(snapshot)} file(s) over "
            f"{args.budget} lines -> {baseline_path}"
        )
        return 0

    baseline = load_baseline(baseline_path)
    violations = find_violations(
        root,
        budget=args.budget,
        baseline=baseline,
        excluded_parts=_DEFAULT_EXCLUDED_PARTS,
        excluded_filename_prefixes=_DEFAULT_EXCLUDED_FILENAME_PREFIXES,
    )

    stale = find_stale_baseline_entries(
        root,
        budget=args.budget,
        baseline=baseline,
        excluded_parts=_DEFAULT_EXCLUDED_PARTS,
        excluded_filename_prefixes=_DEFAULT_EXCLUDED_FILENAME_PREFIXES,
    )
    if stale:
        print(
            "[check_file_size_budget] info: baseline cleanup available for "
            f"{len(stale)} file(s) now at/under budget or removed (non-blocking):"
        )
        for rel_path in stale:
            print(f"  - {rel_path}")

    if not violations:
        print(
            f"[check_file_size_budget] OK — no file over {args.budget} lines grew beyond "
            "its baseline and no new oversized file appeared."
        )
        return 0

    print(
        "[check_file_size_budget] FAIL — file-size budget violated. "
        "Split the file or, after an intentional shrink, re-run with --update-baseline:"
    )
    for violation in sorted(violations, key=lambda item: (-item.lines, item.rel_path)):
        print(_format_violation(violation))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
