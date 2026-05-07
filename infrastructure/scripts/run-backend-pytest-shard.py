#!/usr/bin/env python3
"""Run Polaris backend pytest files in deterministic CI shards.

CRITICAL: All text file I/O must use UTF-8 encoding.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ShardBucket:
    """A test-file bucket assigned to one shard."""

    index: int
    files: list[str] = field(default_factory=list)
    test_count: int = 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _backend_root(repo_root: Path) -> Path:
    return repo_root / "src" / "backend"


def _normalize_tests_root(value: str, *, repo_root: Path, backend_root: Path) -> str:
    raw = Path(value)
    if raw.is_absolute():
        return raw.resolve().relative_to(backend_root.resolve()).as_posix()

    normalized = value.replace("\\", "/").strip()
    if normalized.startswith("src/backend/"):
        return normalized.removeprefix("src/backend/")
    if (repo_root / normalized).exists() and str((repo_root / normalized).resolve()).startswith(
        str(backend_root.resolve())
    ):
        return (repo_root / normalized).resolve().relative_to(backend_root.resolve()).as_posix()
    return normalized


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


def _collect_nodeids(
    *,
    python_executable: str,
    backend_root: Path,
    tests_root: str,
    timeout_seconds: int,
) -> list[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_root)
    result = _run_command(
        [
            python_executable,
            "-m",
            "pytest",
            tests_root,
            "--collect-only",
            "-q",
            "--disable-warnings",
            "-o",
            "addopts=--import-mode=importlib",
        ],
        cwd=backend_root,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pytest collection failed\ncommand={' '.join(result.args)}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return [line.strip() for line in result.stdout.splitlines() if "::" in line and not line.lstrip().startswith("<")]


def _count_by_file(nodeids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for nodeid in nodeids:
        file_path = nodeid.split("::", 1)[0]
        counts[file_path] = counts.get(file_path, 0) + 1
    return counts


def _build_shards(file_counts: dict[str, int], shard_count: int) -> list[ShardBucket]:
    shards = [ShardBucket(index=i + 1) for i in range(shard_count)]
    for file_path, count in sorted(file_counts.items(), key=lambda item: (-item[1], item[0])):
        target = min(shards, key=lambda shard: (shard.test_count, len(shard.files), shard.index))
        target.files.append(file_path)
        target.test_count += count
    return shards


def _batch_files(files: list[str], batch_size: int) -> list[list[str]]:
    return [files[index : index + batch_size] for index in range(0, len(files), batch_size)]


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_batches(
    *,
    python_executable: str,
    backend_root: Path,
    batches: list[list[str]],
    extra_pytest_args: list[str],
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_root)
    results: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(batches, start=1):
        command = [
            python_executable,
            "-m",
            "pytest",
            *batch,
            "--tb=short",
            "--strict-markers",
            "--import-mode=importlib",
            *extra_pytest_args,
        ]
        completed = subprocess.run(
            command,
            cwd=backend_root,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        results.append(
            {
                "batch_index": batch_index,
                "file_count": len(batch),
                "exit_code": completed.returncode,
            }
        )
        if completed.returncode != 0:
            break
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run backend pytest files in a deterministic shard.")
    parser.add_argument("--tests-root", default="polaris/tests", help="Backend-relative test root.")
    parser.add_argument("--shard-index", type=int, default=int(os.environ.get("PYTEST_SHARD_INDEX", "1")))
    parser.add_argument("--shard-count", type=int, default=int(os.environ.get("PYTEST_SHARD_COUNT", "1")))
    parser.add_argument("--batch-size", type=int, default=80, help="Number of test files per pytest process.")
    parser.add_argument("--collect-timeout", type=int, default=180)
    parser.add_argument("--batch-timeout", type=int, default=600)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--summary-path",
        default=None,
        help="Optional UTF-8 JSON summary path. Defaults to test-results/backend-pytest-shards/.",
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="Extra pytest args after --.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.shard_count < 1:
        raise SystemExit("--shard-count must be >= 1")
    if args.shard_index < 1 or args.shard_index > args.shard_count:
        raise SystemExit("--shard-index must be between 1 and --shard-count")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")

    repo_root = _repo_root()
    backend_root = _backend_root(repo_root)
    tests_root = _normalize_tests_root(args.tests_root, repo_root=repo_root, backend_root=backend_root)
    nodeids = _collect_nodeids(
        python_executable=args.python,
        backend_root=backend_root,
        tests_root=tests_root,
        timeout_seconds=args.collect_timeout,
    )
    file_counts = _count_by_file(nodeids)
    shards = _build_shards(file_counts, args.shard_count)
    selected = shards[args.shard_index - 1]
    batches = _batch_files(selected.files, args.batch_size)
    summary_path = (
        Path(args.summary_path)
        if args.summary_path
        else repo_root
        / "test-results"
        / "backend-pytest-shards"
        / f"shard-{args.shard_index}-of-{args.shard_count}.json"
    )

    extra_pytest_args = args.pytest_args
    if extra_pytest_args and extra_pytest_args[0] == "--":
        extra_pytest_args = extra_pytest_args[1:]

    payload: dict[str, Any] = {
        "status": "DRY_RUN" if args.dry_run else "RUNNING",
        "tests_root": tests_root,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "total_tests": len(nodeids),
        "total_files": len(file_counts),
        "selected_tests": selected.test_count,
        "selected_files": len(selected.files),
        "batch_size": args.batch_size,
        "batches": [{"batch_index": i + 1, "file_count": len(batch)} for i, batch in enumerate(batches)],
        "files": selected.files,
    }

    if args.dry_run:
        _write_summary(summary_path, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    batch_results = _run_batches(
        python_executable=args.python,
        backend_root=backend_root,
        batches=batches,
        extra_pytest_args=extra_pytest_args,
        timeout_seconds=args.batch_timeout,
    )
    failed = [result for result in batch_results if result["exit_code"] != 0]
    payload["status"] = "FAIL" if failed else "PASS"
    payload["batch_results"] = batch_results
    _write_summary(summary_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
