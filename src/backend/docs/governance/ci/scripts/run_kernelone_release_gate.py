"""KernelOne release gate runner.

This script provides a stable CI entrypoint for KernelOne release gating:
1. collect-only sanity for the KernelOne-focused test suite
2. optional execution of the same suite
3. structured JSON report output for audit trails
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

BACKEND_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class GateRunResult:
    stage: str
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _build_utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def _discover_suite_paths() -> list[str]:
    candidates: list[Path] = []
    candidates.extend(sorted((BACKEND_ROOT / "tests").glob("test_kernelone_*.py")))
    candidates.extend(sorted((BACKEND_ROOT / "tests" / "architecture").glob("test_kernelone_*.py")))
    candidates.append(BACKEND_ROOT / "tests" / "architecture" / "test_polaris_kernel_fs_guard.py")

    suite_paths: list[str] = []
    for path in candidates:
        if path.exists():
            suite_paths.append(path.relative_to(BACKEND_ROOT).as_posix())
    if not suite_paths:
        raise RuntimeError("KernelOne release suite is empty; no test files discovered.")
    return suite_paths


def _run_pytest(stage: str, pytest_args: Iterable[str]) -> GateRunResult:
    command = [sys.executable, "-m", "pytest", *pytest_args]
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_build_utf8_env(),
        check=False,
    )
    duration_seconds = time.monotonic() - started
    return GateRunResult(
        stage=stage,
        command=command,
        returncode=int(completed.returncode),
        duration_seconds=float(round(duration_seconds, 3)),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KernelOne release CI gate suite.")
    parser.add_argument(
        "--mode",
        choices=("collect", "tests", "all"),
        default="all",
        help="collect: collect-only; tests: execute suite; all: collect then execute.",
    )
    parser.add_argument(
        "--report",
        default="workspace/meta/governance_reports/kernelone_release_gate_report.json",
        help="JSON report output path (relative to backend root).",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="Print JSON report to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    suite_paths = _discover_suite_paths()

    stage_results: list[GateRunResult] = []

    if args.mode in {"collect", "all"}:
        stage_results.append(_run_pytest("collect", ["--collect-only", "-q", *suite_paths]))
    if args.mode in {"tests", "all"}:
        stage_results.append(_run_pytest("tests", ["-q", *suite_paths]))

    ok = all(result.ok for result in stage_results)
    payload = {
        "ok": ok,
        "mode": args.mode,
        "suite_size": len(suite_paths),
        "suite_paths": suite_paths,
        "results": [
            {
                **asdict(result),
                "ok": result.ok,
            }
            for result in stage_results
        ],
    }

    report_path = Path(str(args.report)).expanduser()
    if not report_path.is_absolute():
        report_path = (BACKEND_ROOT / report_path).resolve()
    _write_report(report_path, payload)

    if args.print_report:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if ok:
        return 0

    for result in stage_results:
        if result.ok:
            continue
        print(
            f"[kernelone-release-gate] stage={result.stage} failed rc={result.returncode}",
            file=sys.stderr,
        )
        if result.stdout.strip():
            print(result.stdout, file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
