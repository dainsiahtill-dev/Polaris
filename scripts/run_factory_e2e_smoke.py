from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List


def _normalize_path(value: str, base: Path | None = None) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        anchor = base if base is not None else Path(os.path.abspath(os.getcwd()))
        candidate = anchor / candidate
    return Path(os.path.normpath(os.path.abspath(str(candidate))))


def _build_utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def _npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _npx_executable() -> str:
    return "npx.cmd" if os.name == "nt" else "npx"


def default_output_path(workspace: Path) -> Path:
    return workspace / ".polaris" / "reports" / "factory-e2e-smoke.json"


def build_commands(full: bool = False) -> List[List[str]]:
    python_cmd = sys.executable
    npm_cmd = _npm_executable()
    npx_cmd = _npx_executable()

    commands: List[List[str]] = [
        [
            python_cmd,
            "-B",
            "-m",
            "pytest",
            "-q",
            "src/backend/tests/test_factory_run_service.py",
            "src/backend/tests/test_factory_router.py",
            "src/backend/tests/test_factory_contract_snapshot.py",
            "tests/functional/test_pm_loop.py",
            "tests/functional/test_director_flow.py",
            "tests/test_factory_e2e_smoke_entry.py",
            "src/backend/tests/test_history_factory_overview.py",
        ],
    ]

    if full:
        commands.extend(
            [
                [
                    npx_cmd,
                    "vitest",
                    "run",
                    "src/frontend/src/services/factoryService.contract.test.ts",
                    "src/frontend/src/hooks/useFactory.test.ts",
                    "src/frontend/src/app/components/factory/FactoryWorkspace.test.tsx",
                    "src/frontend/src/runtime/projectionCompat.test.ts",
                ],
                [npm_cmd, "run", "test:e2e:panel"],
                [npm_cmd, "run", "build"],
            ]
        )

    return commands


def run_commands(
    commands: List[List[str]],
    workspace: Path,
    output_path: Path,
    dry_run: bool = False,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logs_dir = output_path.parent / "factory-e2e-smoke-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []

    for index, cmd in enumerate(commands, start=1):
        command_display = " ".join(cmd)
        print("+ " + command_display, flush=True)
        log_path = logs_dir / f"{index:02d}.log"

        if dry_run:
            log_path.write_text(f"[dry-run] {command_display}\n", encoding="utf-8")
            results.append(
                {
                    "command": cmd,
                    "status": "SKIPPED",
                    "exit_code": 0,
                    "log_path": str(log_path.relative_to(workspace)),
                }
            )
            continue

        started_at = time.time()
        completed = subprocess.run(
            cmd,
            cwd=workspace,
            env=_build_utf8_env(),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        duration_seconds = round(time.time() - started_at, 2)

        log_lines = [
            f"$ {command_display}",
            "",
            "=== STDOUT ===",
            completed.stdout or "",
            "",
            "=== STDERR ===",
            completed.stderr or "",
        ]
        log_path.write_text("\n".join(log_lines), encoding="utf-8")

        results.append(
            {
                "command": cmd,
                "status": "PASS" if completed.returncode == 0 else "FAIL",
                "exit_code": int(completed.returncode),
                "duration_seconds": duration_seconds,
                "log_path": str(log_path.relative_to(workspace)),
            }
        )

        if completed.returncode != 0:
            output_path.write_text(
                json.dumps(
                    {
                        "status": "FAIL",
                        "workspace": str(workspace),
                        "results": results,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return int(completed.returncode)

    output_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "workspace": str(workspace),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Polaris factory E2E smoke suite.")
    parser.add_argument("--workspace", default=os.getcwd(), help="Repository workspace root.")
    parser.add_argument("--full", action="store_true", help="Run extended smoke checks.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands only.")
    parser.add_argument("--output", default=None, help="JSON report output path.")
    args = parser.parse_args(argv)

    workspace = _normalize_path(args.workspace)
    if not workspace.is_dir():
        print(f"Workspace not found: {workspace}", flush=True)
        return 2

    output_path = _normalize_path(args.output, workspace) if args.output else default_output_path(workspace)
    commands = build_commands(full=bool(args.full))
    return run_commands(commands, workspace, output_path, dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
