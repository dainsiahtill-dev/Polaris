#!/usr/bin/env python3
"""Run platform module solidification gates.

Usage examples:

  # Single sealed module full functional suite
  rtk proxy .venv/bin/python src/backend/scripts/platform_modules/run_module_gates.py --module M01_event_wait

  # All sealed modules only
  rtk proxy .venv/bin/python src/backend/scripts/platform_modules/run_module_gates.py --mode sealed

  # Cascade: sealed + hardening in dependency order
  rtk proxy .venv/bin/python src/backend/scripts/platform_modules/run_module_gates.py --mode cascade

  # List registry
  rtk proxy .venv/bin/python src/backend/scripts/platform_modules/run_module_gates.py --list

  # Full isolated L1-01 bench (optional, long)
  rtk proxy timeout 6000 .venv/bin/python src/backend/scripts/platform_modules/run_module_gates.py --mode bench \\
      --host-config-file "$HOST_CONFIG" --llm-config-file "$LLM_CONFIG" --registry-home "$REGISTRY_HOME"

This harness exists so agents stop re-proving entire L1–L12 after every one-line
fix. Seal a module once; re-run only that module gate on change; cascade before
claiming multi-module readiness; bench for four-pillars only when cascade is green.
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
from typing import Any, Sequence

# Ensure src/backend is importable when invoked as a script.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from polaris.kernelone.platform_modules.registry import (  # noqa: E402
    MODULE_CASCADE_ORDER,
    PlatformModuleRecord,
    PlatformModuleStatus,
    affected_modules,
    get_module,
    list_modules,
    modules_by_status,
)


@dataclass(slots=True)
class ModuleGateResult:
    module_id: str
    name: str
    status: str
    ok: bool
    duration_s: float
    command: list[str]
    exit_code: int
    detail: str = ""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _run_pytest(targets: Sequence[str], *, repo: Path, extra_args: Sequence[str] = ()) -> tuple[int, float, str]:
    if not targets:
        return 2, 0.0, "no_pytest_targets"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        "--tb=line",
        *extra_args,
    ]
    env = os.environ.copy()
    backend = str(repo / "src" / "backend")
    scripts_bench = str(repo / "src" / "backend" / "scripts" / "factory_bench")
    existing = env.get("PYTHONPATH", "")
    pieces = [backend, scripts_bench]
    if existing:
        pieces.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pieces)
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = round(time.time() - started, 2)
    tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-1000:]
    return proc.returncode, duration, tail.strip()


def run_module_gate(module: PlatformModuleRecord, *, repo: Path) -> ModuleGateResult:
    """Run one module's full functional pytest suite."""

    code, duration, detail = _run_pytest(module.pytest_targets, repo=repo)
    return ModuleGateResult(
        module_id=module.module_id,
        name=module.name,
        status=module.status.value,
        ok=code == 0,
        duration_s=duration,
        command=[sys.executable, "-m", "pytest", *module.pytest_targets],
        exit_code=code,
        detail=detail if code != 0 else "passed",
    )


def select_modules(mode: str, module_id: str | None) -> list[PlatformModuleRecord]:
    if module_id:
        return [get_module(module_id)]
    normalized = (mode or "sealed").strip().lower()
    if normalized == "sealed":
        return list(modules_by_status(PlatformModuleStatus.SEALED))
    if normalized == "cascade":
        return [
            module
            for module in list_modules()
            if module.status in {PlatformModuleStatus.SEALED, PlatformModuleStatus.HARDENING}
        ]
    if normalized == "all":
        return list(list_modules())
    if normalized == "list":
        return []
    if normalized == "bench":
        return []
    raise ValueError(f"unsupported mode: {mode!r}")


def run_bench_gate(
    *,
    repo: Path,
    work_dir: Path,
    host_config: Path,
    llm_config: Path,
    registry_home: Path,
    project_id: str = "L1-01",
    permission_profile_id: str | None = None,
    workflow: str = "direct",
    timeout_s: int = 5400,
) -> dict[str, Any]:
    """Run the frozen catalog case through the isolated engineering entry point."""

    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    runner = repo / "src" / "backend" / "scripts" / "factory_bench" / "run_factory_bench.py"
    cmd = [
        sys.executable,
        str(runner),
        "--project-ids",
        project_id,
        "--work-dir",
        str(work_dir),
        "--timeout",
        str(timeout_s),
        "--max-failed",
        "0",
        "--overall-timeout",
        "6000",
        "--host-config-file",
        str(host_config.resolve(strict=True)),
        "--llm-config-file",
        str(llm_config.resolve(strict=True)),
        "--registry-home",
        str(registry_home.resolve(strict=True)),
        "--launcher-instance-mode",
        "isolated",
        "--bench-session-reporting",
        "off",
    ]
    cmd.extend(["--workflow", workflow])
    if permission_profile_id is not None:
        cmd.extend(["--permission-profile-id", permission_profile_id])
    env = os.environ.copy()
    for key in ("NO_PROXY", "no_proxy"):
        env[key] = ",".join(filter(None, (env.get(key), "localhost", "127.0.0.1", "::1")))
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = round(time.time() - started, 1)
    summary_path = None
    record: dict[str, Any] = {}
    selected = False
    output_lines = proc.stdout.strip().splitlines()
    if output_lines:
        path = Path(output_lines[-1]).resolve()
        if path.name == "summary.json" and path.parent.parent == work_dir / "reports":
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
                records = summary["records"]
                if isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict):
                    record = records[0]
                    selected = (
                        summary.get("selected_project_ids") == [project_id] and record.get("project_id") == project_id
                    )
                    summary_path = str(path)
            except (OSError, ValueError, KeyError, TypeError):
                pass
    observation = record.get("final_observation") or record.get("observation") or {}
    platform_ok = isinstance(observation, dict) and observation.get("harness_status") == "OBSERVED_ACCEPTED"
    ok = (
        proc.returncode == 0
        and selected
        and platform_ok
        and record.get("exit_code") == 0
        and record.get("catalog_quality") == "PASS"
        and record.get("resources_closed") is True
    )
    return {
        "ok": ok,
        "summary_path": summary_path,
        "platform_observed_accepted": platform_ok,
        "catalog_quality": record.get("catalog_quality", "UNKNOWN"),
        "resources_closed": record.get("resources_closed"),
        "process_exit_code": proc.returncode,
        "exit_code": proc.returncode or int(not ok),
        "duration_s": duration,
        "work_dir": str(work_dir),
        "command": cmd,
        "stdout_tail": (proc.stdout or "")[-3000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def print_registry() -> None:
    rows = []
    for module in list_modules():
        rows.append(
            {
                "module_id": module.module_id,
                "status": module.status.value,
                "name": module.name,
                "sealed_by": module.sealed_by_defect or "-",
                "pytest_targets": len(module.pytest_targets),
                "depends_on": list(module.depends_on),
            }
        )
    print(json.dumps({"cascade_order": list(MODULE_CASCADE_ORDER), "modules": rows}, indent=2, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Platform module solidification gates")
    parser.add_argument(
        "--mode",
        default="sealed",
        choices=("list", "module", "sealed", "cascade", "all", "bench"),
        help="Gate mode (default: sealed)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_registry",
        help="List the freeze registry (alias for --mode list)",
    )
    parser.add_argument(
        "--changed-path",
        action="append",
        default=[],
        help="Repo-relative changed path (repeatable). Run owners and their dependent gates.",
    )
    parser.add_argument("--module", default="", help="Single module_id (implies --mode module)")
    parser.add_argument(
        "--work-dir",
        default=str(Path.home() / ".polaris" / "internal-engineering-bench"),
        help="Bench work dir for --mode bench",
    )
    parser.add_argument("--permission-profile-id", help="Explicit host permission profile for the isolated case")
    parser.add_argument("--workflow", choices=("direct", "collaborative"), default="direct")
    parser.add_argument("--host-config-file", type=Path)
    parser.add_argument("--llm-config-file", type=Path)
    parser.add_argument("--registry-home", type=Path)
    parser.add_argument("--project-id", default="L1-01", help="Catalog case for the isolated bench gate")
    parser.add_argument("--timeout", type=int, default=5400, help="Bench timeout seconds")
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional path to write machine-readable gate report",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo = _repo_root()
    mode = args.mode
    module_id = str(args.module or "").strip() or None
    if getattr(args, "list_registry", False):
        mode = "list"
    if module_id:
        mode = "module"

    if args.changed_path:
        if module_id or mode not in {"cascade", "sealed"}:
            parser.error("--changed-path selects its own cascade and cannot be combined with this mode or module")
        mode = "affected"

    if mode == "list":
        print_registry()
        return 0

    if mode == "bench":
        if not all((args.host_config_file, args.llm_config_file, args.registry_home)):
            parser.error("bench requires --host-config-file, --llm-config-file and --registry-home")
        bench = run_bench_gate(
            repo=repo,
            work_dir=Path(args.work_dir),
            timeout_s=args.timeout,
            host_config=args.host_config_file,
            llm_config=args.llm_config_file,
            registry_home=args.registry_home,
            permission_profile_id=args.permission_profile_id,
            workflow=args.workflow,
            project_id=args.project_id,
        )
        bench_report = {"mode": "bench", "bench": bench}
        print(json.dumps(bench_report, indent=2, ensure_ascii=False)[:8000])
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(bench_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        return 0 if bench["ok"] else 1

    try:
        modules = list(affected_modules(args.changed_path)) if mode == "affected" else select_modules(mode, module_id)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not modules:
        print("error: no modules selected", file=sys.stderr)
        return 2

    results: list[ModuleGateResult] = []
    for module in modules:
        print(f"[module-gate] {module.module_id} ({module.status.value}) — {module.name}", flush=True)
        result = run_module_gate(module, repo=repo)
        results.append(result)
        flag = "PASS" if result.ok else "FAIL"
        print(
            f"[module-gate] {flag} {module.module_id} exit={result.exit_code} duration={result.duration_s}s", flush=True
        )
        if not result.ok:
            print(result.detail[-1500:], flush=True)
            # Fail-closed cascade: stop on first failure so residual is local to one module.
            if mode in {"cascade", "all", "sealed", "affected"}:
                break

    report: dict[str, Any] = {
        "mode": mode,
        "module_filter": module_id,
        "changed_paths": args.changed_path,
        "selected_modules": [m.module_id for m in modules],
        "results": [asdict(item) for item in results],
        "passed": sum(1 for item in results if item.ok),
        "failed": sum(1 for item in results if not item.ok),
        "ok": all(item.ok for item in results) and bool(results),
        "cascade_order": list(MODULE_CASCADE_ORDER),
        "sealed_count": len(modules_by_status(PlatformModuleStatus.SEALED)),
        "hardening_count": len(modules_by_status(PlatformModuleStatus.HARDENING)),
    }
    print(json.dumps({k: report[k] for k in ("mode", "passed", "failed", "ok", "sealed_count")}, indent=2))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
