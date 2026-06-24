"""Platform verifier execution service.

Only explicitly enabled verifier providers run. User scripts are additionally
guarded by ``KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED=1`` and must be
workspace-relative paths.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.verifier_execution.public.contracts import (
    RunVerifierPolicyCommandV1,
    VerifierExecutionResultV1,
)

STDOUT_TAIL_LIMIT = 4000


def _bool_env(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _tail(value: str | bytes | None, limit: int = STDOUT_TAIL_LIMIT) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    return text[-limit:] if len(text) > limit else text


def _script_path(workspace: Path, raw_path: Any) -> Path:
    raw = str(raw_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("../") or "/../" in raw or raw == "..":
        raise ValueError("custom script path must be workspace-relative")
    rel = raw.lstrip("./")
    path = (workspace / rel).resolve()
    workspace_root = workspace.resolve()
    if path != workspace_root and workspace_root not in path.parents:
        raise ValueError("custom script path must stay inside workspace")
    return path


def _command_for_script(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return [sys.executable, str(path)]
    if suffix in {".js", ".mjs", ".cjs"}:
        node = shutil.which("node")
        return [node or "node", str(path)]
    if suffix in {".sh", ".bash"}:
        bash = shutil.which("bash") or shutil.which("sh") or "sh"
        return [bash, str(path)]
    if os.access(path, os.X_OK):
        return [str(path)]
    return [sys.executable, str(path)]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _disabled_result(script: dict[str, Any]) -> dict[str, Any]:
    script_path = str(script.get("path") or script.get("script") or "").strip()
    return {
        "id": str(script.get("id") or Path(script_path).stem or "custom_script"),
        "name": str(script.get("id") or script_path or "custom_script"),
        "modality": str(script.get("modality") or "custom_script"),
        "script": script_path,
        "required": bool(script.get("required")),
        "ok": False,
        "passed": False,
        "exit_code": None,
        "detail": "custom verifier scripts are disabled; set KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED=1",
    }


def _run_custom_script(workspace: Path, script: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    script_path = str(script.get("path") or script.get("script") or "").strip()
    result: dict[str, Any] = {
        "id": str(script.get("id") or Path(script_path).stem or "custom_script"),
        "name": str(script.get("id") or script_path or "custom_script"),
        "modality": str(script.get("modality") or "custom_script"),
        "script": script_path,
        "required": bool(script.get("required")),
        "ok": False,
        "passed": False,
        "exit_code": None,
        "detail": "",
    }
    try:
        path = _script_path(workspace, script_path)
    except ValueError as exc:
        result["detail"] = str(exc)
        return result
    if not path.is_file():
        result["detail"] = "custom verifier script not found"
        return result
    result["hash"] = _file_hash(path)
    command = _command_for_script(path)
    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result.update(
            {
                "detail": f"custom verifier timed out after {timeout_seconds}s",
                "stdout_tail": _tail(exc.stdout or ""),
                "stderr_tail": _tail(exc.stderr or ""),
                "timeout": True,
            }
        )
        return result
    result.update(
        {
            "ok": completed.returncode == 0,
            "passed": completed.returncode == 0,
            "exit_code": completed.returncode,
            "detail": "custom verifier passed" if completed.returncode == 0 else "custom verifier failed",
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
        }
    )
    return result


def _enabled_custom_scripts(policy: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = policy.get("capabilities") if isinstance(policy.get("capabilities"), dict) else {}
    custom_status = capabilities.get("custom_script") if isinstance(capabilities, dict) else {}
    raw_enabled_modalities = policy.get("enabled_modalities") or policy.get("enabled_evidence_modalities")
    enabled_items = raw_enabled_modalities if isinstance(raw_enabled_modalities, (list, tuple, set)) else []
    enabled_modalities = {str(item or "").strip().lower() for item in enabled_items if str(item or "").strip()}
    custom_enabled = (
        bool(custom_status.get("enabled"))
        if isinstance(custom_status, dict)
        else "custom_script" in enabled_modalities
    )
    if not custom_enabled:
        return []
    raw_required_modalities = policy.get("required_modalities") or policy.get("required_evidence_modalities")
    required_items = raw_required_modalities if isinstance(raw_required_modalities, (list, tuple, set)) else []
    required_modalities = {str(item or "").strip().lower() for item in required_items if str(item or "").strip()}
    scripts = policy.get("custom_scripts")
    if not isinstance(scripts, list):
        return []
    enabled_scripts: list[dict[str, Any]] = []
    for script in scripts:
        if not isinstance(script, dict) or not bool(script.get("enabled", True)):
            continue
        normalized = dict(script)
        modality = str(normalized.get("modality") or "custom_script").strip().lower() or "custom_script"
        normalized["modality"] = modality
        normalized["required"] = bool(normalized.get("required")) or modality in required_modalities
        enabled_scripts.append(normalized)
    return enabled_scripts


def run_verifier_policy(command: RunVerifierPolicyCommandV1) -> VerifierExecutionResultV1:
    """Run enabled verifier providers and return a Run Ledger gate patch."""

    workspace = Path(command.workspace).expanduser().resolve()
    custom_scripts = _enabled_custom_scripts(command.policy)
    if not custom_scripts:
        return VerifierExecutionResultV1(gate_patch={})
    allow_scripts = _bool_env("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED")
    user_verifiers = [
        _run_custom_script(workspace, script, command.timeout_seconds) if allow_scripts else _disabled_result(script)
        for script in custom_scripts
    ]
    return VerifierExecutionResultV1(gate_patch={"user_verifiers": user_verifiers})


__all__ = ["run_verifier_policy"]
