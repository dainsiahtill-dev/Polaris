"""Gate checking and preflight validation for verify orchestrator."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.kernelone.process.command_executor import CommandExecutionService

from .formatters import (
    command_binary,
    command_workdir,
    extract_node_script,
    extract_pytest_targets,
    extract_python_module,
    split_pytest_target,
)


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Result of a gate check decision."""

    passed: bool
    reason: str
    severity: str  # "none" | "warning" | "error"
    skip_commands: list[str] = field(default_factory=list)


class GateChecker:
    """Gate checker for verify commands."""

    __slots__ = ("_import_probe_cache", "_project_dir", "_timeout_seconds")

    def __init__(
        self,
        project_dir: Path,
        timeout_seconds: int = 5,
    ) -> None:
        self._project_dir = project_dir
        self._timeout_seconds = timeout_seconds
        self._import_probe_cache: dict[tuple[str, str], bool] = {}

    def check_command(self, command: str) -> GateDecision:
        """Check if a command passes preflight validation."""
        warnings = preflight_warnings_for_command(
            project_dir=self._project_dir,
            command=command,
            timeout_seconds=self._timeout_seconds,
            import_probe_cache=self._import_probe_cache,
        )
        skip_reason = ""
        for warning in warnings:
            if not skip_reason and should_skip_for_preflight(warning):
                skip_reason = warning
        if skip_reason:
            return GateDecision(
                passed=False,
                reason=skip_reason,
                severity="warning",
                skip_commands=[command],
            )
        binary = command_binary(command).strip().lower()
        if binary and shutil.which(binary) is None:
            return GateDecision(
                passed=False,
                reason=f"missing command binary: {binary}",
                severity="error",
                skip_commands=[command],
            )
        if warnings:
            return GateDecision(
                passed=True,
                reason="; ".join(warnings),
                severity="warning",
                skip_commands=[],
            )
        return GateDecision(
            passed=True,
            reason="",
            severity="none",
            skip_commands=[],
        )


def should_skip_for_preflight(warning: str) -> bool:
    """Determine if a warning should cause command to be skipped."""
    token = str(warning or "").strip().lower()
    if not token:
        return False
    skip_prefixes = (
        "node workspace missing package.json:",
        "node workspace missing script:",
        "python module unavailable for verify preflight:",
        "pytest target missing:",
        "pytest target only exists at project root:",
    )
    return any(token.startswith(prefix) for prefix in skip_prefixes)


def preflight_warnings_for_command(
    *,
    project_dir: Path,
    command: str,
    timeout_seconds: int,
    import_probe_cache: dict[tuple[str, str], bool],
) -> list[str]:
    """Generate preflight warnings for a command."""
    warnings: list[str] = []
    binary = command_binary(command).strip().lower()
    if not binary:
        return warnings
    workdir = command_workdir(project_dir, command)
    if binary in {"npm", "pnpm", "yarn"}:
        package_json_path = workdir / "package.json"
        if not package_json_path.exists():
            warnings.append(f"node workspace missing package.json: {workdir}")
        else:
            script = extract_node_script(command)
            if script:
                try:
                    payload = json.loads(package_json_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = {}
                scripts_payload = payload.get("scripts", {})
                scripts = (
                    {str(key).strip().lower() for key in scripts_payload if str(key).strip()}
                    if isinstance(scripts_payload, dict)
                    else set()
                )
                if script not in scripts:
                    warnings.append(f"node workspace missing script: {script} ({workdir})")
    module = extract_python_module(command).strip().lower()
    if module in {"pytest", "ruff", "mypy"} and str(workdir).strip():
        cache_key = (str(workdir), module)
        cached_ok = import_probe_cache.get(cache_key)
        if cached_ok is None:
            try:
                cmd_svc = CommandExecutionService(str(workdir))
                req = cmd_svc.parse_command(
                    f"python -m {module}",
                    cwd=str(workdir),
                    timeout_seconds=max(1, int(timeout_seconds)),
                )
                result = cmd_svc.run(req)
                import_probe_cache[cache_key] = bool(result.get("ok", False))
            except ValueError:
                import_probe_cache[cache_key] = False
            except (OSError, RuntimeError, TypeError):
                import_probe_cache[cache_key] = False
        if not import_probe_cache.get(cache_key, False):
            warnings.append(f"python module unavailable for verify preflight: {module}")
    if module == "pytest":
        missing_targets, root_only_targets = _missing_or_root_only_pytest_targets(
            project_dir=project_dir,
            command=command,
        )
        if missing_targets:
            warnings.append(f"pytest target missing: {missing_targets[0]}")
        if root_only_targets:
            warnings.append(f"pytest target only exists at project root: {root_only_targets[0]}")
    return warnings


def _missing_or_root_only_pytest_targets(
    *,
    project_dir: Path,
    command: str,
) -> tuple[list[str], list[str]]:
    """Check for missing or root-only pytest targets."""
    missing: list[str] = []
    root_only: list[str] = []
    workdir = command_workdir(project_dir, command)
    for raw_target in extract_pytest_targets(command):
        target = split_pytest_target(raw_target)
        if not target:
            continue
        token_path = Path(target)
        if token_path.is_absolute():
            if not token_path.exists():
                missing.append(target)
            continue
        workdir_candidate = (workdir / token_path).resolve()
        if workdir_candidate.exists():
            continue
        project_candidate = (project_dir / token_path).resolve()
        if project_candidate.exists():
            root_only.append(target)
        else:
            missing.append(target)
    return missing, root_only


def detect_missing_python_deps(results: list[dict[str, Any]]) -> list[str]:
    """Detect missing Python dependencies from results."""
    missing: set[str] = set()
    pattern = re.compile(r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]")
    for row in results:
        stderr = str(row.get("stderr", ""))
        if not stderr:
            continue
        for match in pattern.findall(stderr):
            token = str(match).strip()
            if token:
                missing.add(token)
    return sorted(missing)


__all__ = [
    "GateChecker",
    "GateDecision",
    "detect_missing_python_deps",
    "preflight_warnings_for_command",
    "should_skip_for_preflight",
]
