"""Workspace quality command runner for the factory quality gate.

Holds the package.json parsing, npm command building, and subprocess execution
extracted verbatim from ``OrchestrationStageExecutor``. The runner owns a
``workspace`` root; ``OrchestrationStageExecutor`` keeps same-named delegating
shims so the test-overridden ``_run_workspace_quality_command`` and the
monkeypatched ``_resolve_workspace_quality_command`` entry points stay intact.

Behavior preservation notes:

* ``run_command`` references command resolution / output trimming through the
  shared ``factory_stage_helpers`` functions, which read ``shutil`` / ``os``
  through their module namespace at call time. The historical tests monkeypatch
  ``factory_run_service.shutil.which`` / ``factory_run_service.os.name``; because
  Python caches module objects those patches mutate the shared ``shutil`` / ``os``
  module objects, so resolution stays patchable here too.
* Section-8 business defaults (the npm ``test`` / ``build`` script mapping and
  the ``npm install --ignore-scripts`` prepare command) are reproduced verbatim.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import factory_stage_helpers as helpers
from .factory_run_models import _WORKSPACE_VALIDATION_TIMEOUT_SECONDS

_MASKED_WORKSPACE_FAILURE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\berror\s+TS\d+\s*:", re.IGNORECASE),
        "command exited 0 but output contains TypeScript compiler errors",
    ),
    (
        re.compile(r"\bTypeScript check skipped\b", re.IGNORECASE),
        "command exited 0 but output reports TypeScript check skipped",
    ),
)


class WorkspaceQualityRunner:
    """Builds and runs workspace quality (npm test/build) commands."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def workspace_package_has_external_dependencies(self) -> bool:
        package_path = (self.workspace / "package.json").resolve()
        if not package_path.exists() or not package_path.is_file():
            return False
        try:
            payload = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            value = payload.get(key)
            if isinstance(value, dict) and value:
                return True
        return False

    def workspace_quality_prepare_commands(
        self,
        commands: list[list[str]],
        context: dict[str, Any],
    ) -> list[list[str]]:
        if not helpers.bool_from_context_or_env(
            context,
            "workspace_validation_install_dependencies",
            "qa_workspace_validation_install_dependencies",
            env_var="POLARIS_FACTORY_WORKSPACE_VALIDATION_INSTALL_DEPENDENCIES",
            default=True,
        ):
            return []
        if not any(command and str(command[0]).strip().lower() == "npm" for command in commands):
            return []
        if (self.workspace / "node_modules").is_dir():
            return []
        if not self.workspace_package_has_external_dependencies():
            return []
        return [["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]]

    def load_package_scripts(self) -> dict[str, str]:
        package_path = (self.workspace / "package.json").resolve()
        if not package_path.exists() or not package_path.is_file():
            return {}
        try:
            payload = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        scripts = payload.get("scripts")
        if not isinstance(scripts, dict):
            return {}
        return {str(key): str(value) for key, value in scripts.items() if key and value}

    def workspace_quality_commands(self, context: dict[str, Any]) -> list[list[str]]:
        if not helpers.bool_from_context_or_env(
            context,
            "workspace_validation",
            "qa_workspace_validation",
            env_var="POLARIS_FACTORY_WORKSPACE_VALIDATION",
            default=True,
        ):
            return []

        configured = context.get("quality_commands") or context.get("workspace_quality_commands")
        if isinstance(configured, list):
            commands: list[list[str]] = []
            for item in configured:
                if isinstance(item, list) and all(isinstance(part, str) and part.strip() for part in item):
                    commands.append([part.strip() for part in item])
                elif isinstance(item, str) and item.strip():
                    commands.append([part for part in item.strip().split(" ") if part])
            return commands

        scripts = self.load_package_scripts()
        commands = []
        if "build" in scripts:
            commands.append(["npm", "run", "build"])
        if "test" in scripts:
            commands.append(["npm", "test"])
        if (
            helpers.bool_from_context_or_env(
                context,
                "workspace_validation_entrypoint_smoke",
                "qa_workspace_validation_entrypoint_smoke",
                env_var="POLARIS_FACTORY_WORKSPACE_VALIDATION_ENTRYPOINT_SMOKE",
                default=True,
            )
            and "start" in scripts
        ):
            commands.append(["npm", "run", "start"])
        return commands

    def run_command(self, command: list[str], timeout_seconds: float) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat()
        resolved_command = helpers.resolve_workspace_quality_command(command)
        if not resolved_command:
            executable = command[0] if command else ""
            return {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": None,
                "passed": False,
                "error": f"executable not found: {executable}",
                "stdout_tail": "",
                "stderr_tail": "",
            }
        try:
            completed = subprocess.run(
                resolved_command,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1.0, float(timeout_seconds or _WORKSPACE_VALIDATION_TIMEOUT_SECONDS)),
                env={**os.environ, "CI": os.environ.get("CI", "1")},
                check=False,
            )
            stdout = helpers.trim_command_output(completed.stdout)
            stderr = helpers.trim_command_output(completed.stderr)
            masked_failure_reason = ""
            if int(completed.returncode) == 0:
                masked_failure_reason = _masked_workspace_failure_reason(stdout, stderr)
            result: dict[str, Any] = {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": int(completed.returncode),
                "passed": int(completed.returncode) == 0 and not masked_failure_reason,
                "stdout_tail": stdout,
                "stderr_tail": stderr,
            }
            if masked_failure_reason:
                result["error"] = masked_failure_reason
            return result
        except subprocess.TimeoutExpired as exc:
            return {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": None,
                "passed": False,
                "error": f"timeout after {float(timeout_seconds):.1f}s",
                "stdout_tail": helpers.trim_command_output(str(exc.stdout or "")),
                "stderr_tail": helpers.trim_command_output(str(exc.stderr or "")),
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "command": command,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": None,
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "stdout_tail": "",
                "stderr_tail": "",
            }


def _masked_workspace_failure_reason(stdout: str, stderr: str) -> str:
    output = f"{stdout}\n{stderr}"
    for pattern, reason in _MASKED_WORKSPACE_FAILURE_PATTERNS:
        if pattern.search(output):
            return reason
    return ""
