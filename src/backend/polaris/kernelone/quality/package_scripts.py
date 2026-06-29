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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_SCRIPT_INTERPRETERS = {"node", "python", "python3", "bash", "sh"}
_SCRIPT_PATH_EXTENSIONS = {".cjs", ".js", ".mjs", ".py", ".sh", ".ts", ".tsx"}
_SHELL_OPERATORS = {"&&", "||", ";", "|"}
_BUILD_OUTPUT_DIR_NAMES = {"dist", "build", "out", "bin"}
_PLACEHOLDER_SCRIPT_COMMANDS = {"echo", "printf"}
_NPM_SCRIPT_ALIAS_COMMANDS = {
    "install": "install",
    "restart": "restart",
    "start": "start",
    "stop": "stop",
    "test": "test",
}
_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE = re.compile(
    r"(?:npm\s+run\s+(?:build|compile)|pnpm\s+(?:build|compile)|yarn\s+(?:build|compile)|\btsc\b)",
    re.IGNORECASE,
)
_NODE_MODULE_EXTENSIONS = (".js", ".cjs", ".mjs", ".json", ".node")
_LOCAL_REQUIRE_RE = re.compile(r"""require\(\s*["'](?P<ref>\.{1,2}/[^"']+|/[^"']+)["']\s*\)""")
_LOCAL_DYNAMIC_IMPORT_RE = re.compile(r"""import\(\s*["'](?P<ref>\.{1,2}/[^"']+|/[^"']+)["']\s*\)""")
_LOCAL_STATIC_IMPORT_RE = re.compile(
    r"""\bimport\s+(?:(?:[\w*{}\s,]+)\s+from\s+)?["'](?P<ref>\.{1,2}/[^"']+|/[^"']+)["']"""
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


def _resolve_script_reference(workspace: str, token: str) -> str | None:
    normalized = token.replace("\\", "/")
    if os.path.isabs(normalized):
        return normalized if os.path.exists(normalized) else None
    exact = os.path.join(workspace, normalized)
    if os.path.exists(exact):
        return exact
    base, ext = os.path.splitext(exact)
    if ext:
        return None
    for suffix in _SCRIPT_PATH_EXTENSIONS:
        candidate = base + suffix
        if os.path.exists(candidate):
            return candidate
    return None


def _script_reference_exists(workspace: str, token: str) -> bool:
    return _resolve_script_reference(workspace, token) is not None


def _resolve_node_module_reference(importer_dir: str, module_ref: str) -> str | None:
    normalized = module_ref.replace("\\", "/")
    exact = normalized if os.path.isabs(normalized) else os.path.join(importer_dir, normalized)
    if os.path.isfile(exact):
        return exact
    base, ext = os.path.splitext(exact)
    if ext:
        return None
    for suffix in _NODE_MODULE_EXTENSIONS:
        candidate = base + suffix
        if os.path.isfile(candidate):
            return candidate
    if os.path.isdir(exact):
        for suffix in _NODE_MODULE_EXTENSIONS:
            candidate = os.path.join(exact, "index" + suffix)
            if os.path.isfile(candidate):
                return candidate
    return None


def _local_node_module_references(source: str) -> list[str]:
    refs: list[str] = []
    for pattern in (_LOCAL_REQUIRE_RE, _LOCAL_DYNAMIC_IMPORT_RE, _LOCAL_STATIC_IMPORT_RE):
        for match in pattern.finditer(source):
            module_ref = str(match.group("ref") or "").strip()
            if module_ref:
                refs.append(module_ref)
    return list(dict.fromkeys(refs))


def _missing_local_node_module_references(workspace: str, script_name: str, entrypoint_path: str) -> list[str]:
    try:
        with open(entrypoint_path, encoding="utf-8") as handle:
            source = handle.read()
    except OSError as exc:
        rel_entrypoint = os.path.relpath(entrypoint_path, workspace)
        return [f"script {script_name!r} local entrypoint {rel_entrypoint!r} is unreadable: {exc}"]

    importer_dir = os.path.dirname(entrypoint_path)
    rel_entrypoint = os.path.relpath(entrypoint_path, workspace)
    missing: list[str] = []
    for module_ref in _local_node_module_references(source):
        if _resolve_node_module_reference(importer_dir, module_ref) is None:
            missing.append(
                f"script {script_name!r} local entrypoint {rel_entrypoint!r} "
                f"requires missing local module: {module_ref}"
            )
    return missing


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
                    if _is_local_script_option_reference(option_value):
                        resolved_option = _resolve_script_reference(workspace, option_value)
                        if resolved_option is None:
                            missing.append(
                                f"script {script_name!r} references missing local entrypoint: {option_value}"
                            )
                        else:
                            missing.extend(
                                _missing_local_node_module_references(workspace, script_name, resolved_option)
                            )
                index += 2
                continue
            if candidate.startswith("-"):
                index += 1
                continue
            if _is_local_script_reference(candidate):
                resolved_candidate = _resolve_script_reference(workspace, candidate)
                if resolved_candidate is None:
                    if _script_builds_before_interpreter(tokens, interpreter_index):
                        break
                    if _is_build_output_reference(candidate) and _script_lifecycle_can_build_output(
                        all_scripts,
                        script_name,
                        tokens,
                    ):
                        break
                    missing.append(f"script {script_name!r} references missing local entrypoint: {candidate}")
                elif token == "node":
                    missing.extend(_missing_local_node_module_references(workspace, script_name, resolved_candidate))
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


def package_script_cycle_reasons(scripts: Mapping[str, Any]) -> list[str]:
    """Return package script dependency-cycle reasons for npm-compatible scripts."""

    script_commands = {
        str(name): command
        for name, command in scripts.items()
        if isinstance(name, str) and isinstance(command, str) and name.strip()
    }
    if not script_commands:
        return []
    graph = {
        name: tuple(dep for dep in _npm_script_dependencies(command) if dep in script_commands)
        for name, command in script_commands.items()
    }
    reasons: list[str] = []
    seen_cycles: set[tuple[str, ...]] = set()
    for script_name in sorted(graph):
        cycle = _script_cycle_from(graph, start=script_name, current=script_name, path=(script_name,))
        if cycle is None:
            continue
        canonical = _canonical_cycle(cycle)
        if canonical in seen_cycles:
            continue
        seen_cycles.add(canonical)
        chain = " -> ".join(cycle)
        reasons.append(f"npm package manifest script {script_name!r} recursively invokes itself via {chain}")
    return reasons


def _npm_script_dependencies(command: str) -> tuple[str, ...]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ()
    dependencies: list[str] = []
    index = 0
    while index < len(tokens):
        token = os.path.basename(str(tokens[index] or "")).lower()
        if token in {"npm", "npm.cmd"}:
            dependency, consumed = _npm_dependency_after_command(tokens, index)
        elif token in {"pnpm", "pnpm.cmd"}:
            dependency, consumed = _pnpm_dependency_after_command(tokens, index)
        elif token in {"yarn", "yarnpkg", "yarn.cmd", "yarnpkg.cmd"}:
            dependency, consumed = _yarn_dependency_after_command(tokens, index)
        else:
            dependency, consumed = "", 1
        if dependency:
            dependencies.append(dependency)
        index += max(consumed, 1)
    return tuple(dict.fromkeys(dependencies))


def _npm_dependency_after_command(tokens: list[str], command_index: int) -> tuple[str, int]:
    next_index = command_index + 1
    if next_index >= len(tokens):
        return "", 1
    subcommand = str(tokens[next_index] or "").strip().lower()
    if subcommand in {"run", "run-script"}:
        dependency = _first_script_name_argument(tokens, next_index + 1)
        return dependency, 3 if dependency else 2
    return _NPM_SCRIPT_ALIAS_COMMANDS.get(subcommand, ""), 2


def _pnpm_dependency_after_command(tokens: list[str], command_index: int) -> tuple[str, int]:
    next_index = command_index + 1
    if next_index >= len(tokens):
        return "", 1
    subcommand = str(tokens[next_index] or "").strip().lower()
    if subcommand == "run":
        dependency = _first_script_name_argument(tokens, next_index + 1)
        return dependency, 3 if dependency else 2
    return _NPM_SCRIPT_ALIAS_COMMANDS.get(subcommand, ""), 2


def _yarn_dependency_after_command(tokens: list[str], command_index: int) -> tuple[str, int]:
    next_index = command_index + 1
    if next_index >= len(tokens):
        return "", 1
    subcommand = str(tokens[next_index] or "").strip().lower()
    if subcommand == "run":
        dependency = _first_script_name_argument(tokens, next_index + 1)
        return dependency, 3 if dependency else 2
    return "", 1


def _first_script_name_argument(tokens: list[str], start_index: int) -> str:
    for index in range(start_index, len(tokens)):
        token = str(tokens[index] or "").strip()
        if not token:
            continue
        if token in _SHELL_OPERATORS:
            return ""
        if token.startswith("-"):
            continue
        return token
    return ""


def _script_cycle_from(
    graph: Mapping[str, tuple[str, ...]],
    *,
    start: str,
    current: str,
    path: tuple[str, ...],
) -> tuple[str, ...] | None:
    for dependency in graph.get(current, ()):
        if dependency == start:
            return (*path, dependency)
        if dependency in path:
            continue
        cycle = _script_cycle_from(graph, start=start, current=dependency, path=(*path, dependency))
        if cycle is not None:
            return cycle
    return None


def _canonical_cycle(cycle: tuple[str, ...]) -> tuple[str, ...]:
    nodes = cycle[:-1] if len(cycle) > 1 and cycle[0] == cycle[-1] else cycle
    if not nodes:
        return cycle
    rotations = [(*nodes[index:], *nodes[:index]) for index in range(len(nodes))]
    return min(rotations)


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
    failures.extend(package_script_cycle_reasons(scripts))
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


__all__ = ["PackageScriptsCheckResult", "check_package_scripts", "package_script_cycle_reasons"]
