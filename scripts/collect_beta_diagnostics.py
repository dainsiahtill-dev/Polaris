import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def _normalize_path(value: str, base: Path | None = None) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        anchor = base if base is not None else Path.cwd()
        candidate = anchor / candidate
    return Path(candidate.absolute())


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _find_latest_beta_report(reports_dir: Path) -> tuple[Path | None, dict[str, Any] | None]:
    """
    Find the latest beta gates report across all known prefixes.
    Scopes: beta-gates, local-beta-gates, reaudit-beta-gates, ci-beta-gates, manual-beta-gates
    Returns the most recently modified (by mtime) valid report.
    """
    # All known prefixes for beta gates reports
    prefixes = ["beta-gates", "local-beta-gates", "reaudit-beta-gates", "ci-beta-gates", "manual-beta-gates"]

    # Collect all candidates with their mtime
    candidates: list[tuple[Path, float]] = []
    for prefix in prefixes:
        for candidate in reports_dir.glob(f"{prefix}*.json"):
            try:
                mtime = candidate.stat().st_mtime
                candidates.append((candidate, mtime))
            except OSError:
                continue

    # Sort by mtime descending (most recent first)
    candidates.sort(key=lambda x: x[1], reverse=True)

    # Return the first valid JSON
    for path, _ in candidates:
        payload = _read_json(path)
        if payload is not None:
            return path, payload
    return None, None


def _find_latest_smoke_report(reports_dir: Path) -> tuple[Path | None, dict[str, Any] | None]:
    """
    Find the latest factory smoke report across all known prefixes.
    """
    prefixes = ["factory-e2e-smoke", "local-factory-smoke", "ci-factory-smoke", "manual-factory-smoke", "factory-smoke"]

    candidates: list[tuple[Path, float]] = []
    for prefix in prefixes:
        for candidate in reports_dir.glob(f"{prefix}*.json"):
            try:
                mtime = candidate.stat().st_mtime
                candidates.append((candidate, mtime))
            except OSError:
                continue

    candidates.sort(key=lambda x: x[1], reverse=True)

    for path, _ in candidates:
        payload = _read_json(path)
        if payload is not None:
            return path, payload
    return None, None


def _collect_git_status(workspace: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "status", "--short"],
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _collect_recent_traces(workspace: Path, limit: int = 10) -> list[str]:
    test_results_dir = workspace / "test-results"
    if not test_results_dir.is_dir():
        return []
    traces = sorted(test_results_dir.rglob("trace.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    return [str(path.relative_to(workspace)) for path in traces[:limit]]


def _collect_recent_logs(reports_dir: Path, workspace: Path, limit: int = 20) -> list[str]:
    if not reports_dir.is_dir():
        return []
    logs = sorted(reports_dir.rglob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    return [str(path.relative_to(workspace)) for path in logs[:limit]]


def build_diagnostics(workspace: Path) -> dict[str, Any]:
    reports_dir = workspace / ".polaris" / "reports"
    beta_report_path, beta_report = _find_latest_beta_report(reports_dir)
    smoke_report_path, smoke_report = _find_latest_smoke_report(reports_dir)

    beta_status = str((beta_report or {}).get("status") or "UNKNOWN")
    smoke_status = str((smoke_report or {}).get("status") or "UNKNOWN")
    overall_status = "PASS" if beta_status == "PASS" and smoke_status in {"PASS", "UNKNOWN"} else beta_status

    failed_gates = []
    for gate in (beta_report or {}).get("gates", []):
        if not isinstance(gate, dict):
            continue
        if str(gate.get("status") or "") == "FAIL":
            failed_gates.append(
                {
                    "name": gate.get("name"),
                    "command": gate.get("command"),
                    "log_path": gate.get("log_path"),
                }
            )

    diagnostics = {
        "status": overall_status,
        "workspace": str(workspace),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_status": _collect_git_status(workspace),
        "reports": {
            "beta_gates": {
                "path": str(beta_report_path.relative_to(workspace)) if beta_report_path else None,
                "status": beta_status,
                "generated_at": (beta_report or {}).get("generated_at"),
            },
            "factory_smoke": {
                "path": str(smoke_report_path.relative_to(workspace)) if smoke_report_path else None,
                "status": smoke_status,
            },
        },
        "failed_gates": failed_gates,
        "evidence_paths": {
            "logs": _collect_recent_logs(reports_dir, workspace),
            "traces": _collect_recent_traces(workspace),
        },
    }
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Polaris beta diagnostics.")
    parser.add_argument("--workspace", default=".", help="Repository workspace root.")
    parser.add_argument(
        "--output",
        default=None,
        help="Diagnostic JSON output path. Defaults to .polaris/reports/beta-diagnostics.json",
    )
    args = parser.parse_args(argv)

    workspace = _normalize_path(args.workspace)
    if not workspace.is_dir():
        print(f"Workspace not found: {workspace}", flush=True)
        return 2

    output_path = _normalize_path(args.output, workspace) if args.output else workspace / ".polaris" / "reports" / "beta-diagnostics.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = build_diagnostics(workspace)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
