"""Platform package.json script quality checks.

This module is intentionally named as a KernelOne quality gate. Internal stress
harnesses may reuse it, but production QA paths must not import benchmark or
factory harness modules for package script validation.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any

_SCRIPT_INTERPRETERS = {"node", "python", "python3", "bash", "sh"}
_SCRIPT_PATH_EXTENSIONS = {".cjs", ".js", ".mjs", ".py", ".sh", ".ts", ".tsx"}
_SHELL_OPERATORS = {"&&", "||", ";", "|"}
_BUILD_OUTPUT_DIR_NAMES = {"dist", "build", "out", "bin"}
_PLACEHOLDER_SCRIPT_COMMANDS = {"echo", "printf"}
_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE = re.compile(
    r"(?:npm\s+run\s+(?:build|compile)|pnpm\s+(?:build|compile)|yarn\s+(?:build|compile)|\btsc\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PackageScriptsCheckResult:
    ok: bool
    detail: str


def _is_local_script_reference(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    normalized = token.replace("\\", "/")
    if "://" in normalized:
        return False
    _, ext = os.path.splitext(normalized)
    return "/" in normalized or ext.lower() in _SCRIPT_PATH_EXTENSIONS


def _is_local_script_option_reference(token: str) -> bool:
    normalized = token.replace("\\", "/")
    _, ext = os.path.splitext(normalized)
    return normalized.startswith(("./", "../", "/")) or ext.lower() in _SCRIPT_PATH_EXTENSIONS


def _script_reference_exists(workspace: str, token: str) -> bool:
    normalized = token.replace("\\", "/")
    if os.path.isabs(normalized):
        return os.path.exists(normalized)
    exact = os.path.join(workspace, normalized)
    if os.path.exists(exact):
        return True
    base, ext = os.path.splitext(exact)
    if ext:
        return False
    return any(os.path.exists(base + suffix) for suffix in _SCRIPT_PATH_EXTENSIONS)


def _script_builds_before_interpreter(tokens: list[str], interpreter_index: int) -> bool:
    return bool(_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(" ".join(tokens[:interpreter_index])))


def _is_build_output_reference(candidate: str) -> bool:
    normalized = candidate.replace("\\", "/").lstrip("./")
    first_segment = normalized.split("/", 1)[0]
    return first_segment in _BUILD_OUTPUT_DIR_NAMES


def _script_lifecycle_can_build_output(scripts: dict[str, Any], script_name: str, tokens: list[str]) -> bool:
    pre_script = scripts.get(f"pre{script_name}")
    command = " ".join(tokens)
    if isinstance(pre_script, str) and _SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(pre_script):
        return True
    if _SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(command):
        return True
    return script_name in {"start", "serve", "preview"} and isinstance(scripts.get("build"), str)


def _missing_package_script_entrypoints(
    workspace: str,
    script_name: str,
    command: str,
    *,
    scripts: dict[str, Any] | None = None,
) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return [f"script {script_name!r} has invalid shell syntax: {exc}"]
    all_scripts = scripts or {}
    missing: list[str] = []
    index = 0
    while index < len(tokens):
        interpreter_index = index
        token = os.path.basename(tokens[index])
        if token not in _SCRIPT_INTERPRETERS:
            index += 1
            continue
        index += 1
        while index < len(tokens):
            candidate = tokens[index]
            if candidate in _SHELL_OPERATORS:
                break
            if token == "node" and candidate in {"-e", "--eval", "-p", "--print"}:
                index += 2
                continue
            if token == "node" and candidate in {"-r", "--require", "--import", "--loader"}:
                if index + 1 < len(tokens):
                    option_value = tokens[index + 1]
                    if _is_local_script_option_reference(option_value) and not _script_reference_exists(
                        workspace, option_value
                    ):
                        missing.append(f"script {script_name!r} references missing local entrypoint: {option_value}")
                index += 2
                continue
            if candidate.startswith("-"):
                index += 1
                continue
            if _is_local_script_reference(candidate) and not _script_reference_exists(workspace, candidate):
                if _script_builds_before_interpreter(tokens, interpreter_index):
                    break
                if _is_build_output_reference(candidate) and _script_lifecycle_can_build_output(
                    all_scripts,
                    script_name,
                    tokens,
                ):
                    break
                missing.append(f"script {script_name!r} references missing local entrypoint: {candidate}")
            break
    return missing


def _placeholder_package_script_reason(script_name: str, command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if not tokens:
        return f"script {script_name!r} is empty"
    first_command = os.path.basename(tokens[0])
    if first_command not in _PLACEHOLDER_SCRIPT_COMMANDS:
        return ""
    for index, token in enumerate(tokens):
        if token not in _SHELL_OPERATORS or index + 1 >= len(tokens):
            continue
        next_command = os.path.basename(tokens[index + 1])
        if next_command not in {*_PLACEHOLDER_SCRIPT_COMMANDS, "exit", "true"}:
            return ""
    return f"script {script_name!r} is a placeholder command: {command}"


def check_package_scripts(workspace: str) -> PackageScriptsCheckResult:
    """Validate package.json scripts through a platform quality gate."""

    package_path = os.path.join(workspace, "package.json")
    if not os.path.exists(package_path):
        return PackageScriptsCheckResult(False, "package.json not found")
    try:
        with open(package_path, encoding="utf-8") as handle:
            package = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return PackageScriptsCheckResult(False, f"package.json unreadable or invalid: {exc}")
    scripts = package.get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        return PackageScriptsCheckResult(False, "package.json has no scripts to validate")
    failures: list[str] = []
    for script_name, command in scripts.items():
        if not isinstance(command, str):
            failures.append(f"script {script_name!r} is not a string")
            continue
        placeholder_reason = _placeholder_package_script_reason(str(script_name), command)
        if placeholder_reason:
            failures.append(placeholder_reason)
            continue
        failures.extend(_missing_package_script_entrypoints(workspace, str(script_name), command, scripts=scripts))
    if failures:
        return PackageScriptsCheckResult(False, "; ".join(failures[:3]))
    return PackageScriptsCheckResult(True, f"{len(scripts)} package scripts have valid local entrypoint references")


__all__ = ["PackageScriptsCheckResult", "check_package_scripts"]
