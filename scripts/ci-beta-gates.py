import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List


class GateCommand:
    def __init__(self, name: str, command: List[str]) -> None:
        self.name = name
        self.command = command


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


def _sanitize_name(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    return "-".join(filter(None, safe.split("-"))) or "gate"


def default_output_path(workspace: Path) -> Path:
    return workspace / ".polaris" / "reports" / "beta-gates.json"


def build_beta_gates(include_full_electron: bool = False) -> List[GateCommand]:
    python_cmd = sys.executable
    npm_cmd = _npm_executable()
    npx_cmd = _npx_executable()

    gates = [
        GateCommand("typecheck", [npm_cmd, "run", "typecheck"]),
        GateCommand("build", [npm_cmd, "run", "build"]),
        GateCommand(
            "frontend-vitest",
            [
                npx_cmd,
                "vitest",
                "run",
                "src/frontend/src/services/factoryService.contract.test.ts",
                "src/frontend/src/hooks/useFactory.test.ts",
                "src/frontend/src/app/components/factory/FactoryWorkspace.test.tsx",
                "src/frontend/src/runtime/projectionCompat.test.ts",
            ],
        ),
        GateCommand(
            "factory-backend",
            [
                python_cmd,
                "-B",
                "-m",
                "pytest",
                "src/backend/tests/test_factory_run_service.py",
                "src/backend/tests/test_factory_router.py",
                "src/backend/tests/test_factory_contract_snapshot.py",
                "-q",
            ],
        ),
        GateCommand(
            "functional-flow",
            [
                python_cmd,
                "-B",
                "-m",
                "pytest",
                "tests/functional/test_pm_loop.py",
                "tests/functional/test_director_flow.py",
                "-q",
            ],
        ),
        GateCommand("electron-panel", [npm_cmd, "run", "test:e2e:panel"]),
        GateCommand(
            "factory-smoke",
            [
                python_cmd,
                "scripts/run_factory_e2e_smoke.py",
                "--workspace",
                ".",
            ],
        ),
    ]

    if include_full_electron:
        gates.append(GateCommand("electron-full", [npm_cmd, "run", "test:e2e"]))

    return gates


def run_beta_gates(
    gates: List[GateCommand],
    workspace: Path,
    output_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    reports_dir = output_path.parent
    logs_dir = reports_dir / "beta-gates-logs"
    reports_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    overall_status = "PASS"

    for index, gate in enumerate(gates, start=1):
        log_file = logs_dir / f"{index:02d}-{_sanitize_name(gate.name)}.log"
        command_display = " ".join(gate.command)
        started_at = time.time()

        print(f"[beta-gate] {gate.name}: {command_display}", flush=True)

        if dry_run:
            log_file.write_text(f"[dry-run] {command_display}\n", encoding="utf-8")
            results.append(
                {
                    "name": gate.name,
                    "command": gate.command,
                    "status": "SKIPPED",
                    "exit_code": 0,
                    "duration_seconds": 0.0,
                    "log_path": str(log_file.relative_to(workspace)),
                }
            )
            continue

        completed = subprocess.run(
            gate.command,
            cwd=workspace,
            env=_build_utf8_env(),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        log_content = []
        log_content.append(f"$ {command_display}")
        log_content.append("")
        log_content.append("=== STDOUT ===")
        log_content.append(completed.stdout or "")
        log_content.append("")
        log_content.append("=== STDERR ===")
        log_content.append(completed.stderr or "")
        log_file.write_text("\n".join(log_content), encoding="utf-8")

        duration_seconds = round(time.time() - started_at, 2)
        gate_status = "PASS" if completed.returncode == 0 else "FAIL"
        results.append(
            {
                "name": gate.name,
                "command": gate.command,
                "status": gate_status,
                "exit_code": int(completed.returncode),
                "duration_seconds": duration_seconds,
                "log_path": str(log_file.relative_to(workspace)),
            }
        )

        if completed.returncode != 0:
            overall_status = "FAIL"
            break

    report = {
        "status": overall_status,
        "workspace": str(workspace),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dry_run": dry_run,
        "gates": results,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Polaris Beta gate suite.")
    parser.add_argument("--workspace", default=".", help="Repository root.")
    parser.add_argument("--output", default=None, help="JSON report output path.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands and write a skipped report.")
    parser.add_argument("--full-electron", action="store_true", help="Include the full Electron E2E suite.")
    args = parser.parse_args(argv)

    workspace = _normalize_path(args.workspace)
    if not workspace.is_dir():
        print(f"Workspace not found: {workspace}", flush=True)
        return 2

    output_path = _normalize_path(args.output, workspace) if args.output else default_output_path(workspace)
    gates = build_beta_gates(include_full_electron=bool(args.full_electron))
    report = run_beta_gates(gates, workspace, output_path, dry_run=bool(args.dry_run))
    return 0 if report["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
