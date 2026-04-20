from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

from ..utils import normalize_path_str as _normalize_path


def _parse_command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return [part for part in str(command).strip().split(" ") if part]


def _command_binary(command: str) -> str:
    tokens = _parse_command_tokens(command)
    if not tokens:
        return ""
    return str(tokens[0]).strip().lower()


def _extract_node_script(command: str) -> str:
    tokens = _parse_command_tokens(command)
    if not tokens:
        return ""
    binary = str(tokens[0]).strip().lower()
    if binary not in {"npm", "pnpm", "yarn"}:
        return ""
    if len(tokens) <= 1:
        return ""
    if binary == "yarn":
        script = str(tokens[1]).strip().lower()
        return script if script and not script.startswith("-") else ""
    action = str(tokens[1]).strip().lower()
    if action in {"test", "lint", "typecheck", "build"}:
        return action
    if action in {"run", "run-script"} and len(tokens) >= 3:
        script = str(tokens[2]).strip().lower()
        return script if script else ""
    return ""


def _workspace_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    rel = str(item.get("rel", ".")).strip() or "."
    depth = 0 if rel == "." else len(Path(rel).parts)
    return (0 if rel == "." else 1, depth, rel)


def _discover_workspaces(project_dir: Path, marker_names: set[str], max_depth: int = 4) -> list[dict[str, Any]]:
    skip_dirs = {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "dist",
        "build",
        "target",
        ".next",
        ".turbo",
        ".venv",
        "venv",
        ".polaris",
        ".polaris",
        ".harborpitlot",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
    out: list[dict[str, Any]] = []
    root_resolved = project_dir.resolve()
    for current_root, dirnames, filenames in os.walk(project_dir):
        current_path = Path(current_root)
        rel = current_path.resolve().relative_to(root_resolved).as_posix()
        depth = 0 if rel == "." else len(Path(rel).parts)
        dirnames[:] = [name for name in dirnames if name not in skip_dirs]
        if depth > max_depth:
            dirnames[:] = []
            continue
        filename_set = {str(name).strip() for name in filenames}
        if not marker_names.intersection(filename_set):
            continue
        out.append({"rel": rel, "path": current_path})
    out.sort(key=_workspace_sort_key)
    return out


def _discover_node_workspaces(project_dir: Path) -> list[dict[str, Any]]:
    workspaces = _discover_workspaces(project_dir, {"package.json"}, max_depth=5)
    for workspace in workspaces:
        scripts: set[str] = set()
        package_json_path = Path(workspace["path"]) / "package.json"
        try:
            payload = json.loads(package_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        scripts_payload = payload.get("scripts", {})
        if isinstance(scripts_payload, dict):
            for key in scripts_payload:
                script = str(key).strip().lower()
                if script:
                    scripts.add(script)
        workspace["scripts"] = scripts
    return workspaces


def _discover_python_workspaces(project_dir: Path) -> list[dict[str, Any]]:
    return _discover_workspaces(
        project_dir,
        {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini"},
        max_depth=5,
    )


def _changed_overlap_score(workspace_rel: str, changed_files: list[str]) -> int:
    rel = str(workspace_rel).strip() or "."
    if rel == ".":
        return 0
    prefix = rel.rstrip("/") + "/"
    score = 0
    for changed in changed_files:
        normalized = _normalize_path(changed)
        if normalized.startswith(prefix):
            score += 1
    return score


def _choose_workspace(
    *,
    command: str,
    changed_files: list[str],
    workspaces: list[dict[str, Any]],
    kind: str,
) -> str:
    if not workspaces:
        return "."
    candidates = list(workspaces)
    if kind == "node":
        script = _extract_node_script(command)
        if script:
            with_script = [item for item in candidates if script in set(item.get("scripts", set()))]
            if with_script:
                candidates = with_script

    best: dict[str, Any] | None = None
    best_score = -1
    for item in candidates:
        rel = str(item.get("rel", ".")).strip() or "."
        score = _changed_overlap_score(rel, changed_files)
        if score > best_score:
            best = item
            best_score = score
        elif score == best_score and best is not None and _workspace_sort_key(item) < _workspace_sort_key(best):
            best = item
    if best is not None and best_score > 0:
        return str(best.get("rel", ".")).strip() or "."

    roots = [item for item in candidates if str(item.get("rel", ".")).strip() in {"", "."}]
    if roots:
        return "."
    candidates.sort(key=_workspace_sort_key)
    return str(candidates[0].get("rel", ".")).strip() or "."


def _wrap_command_with_workspace(command: str, workspace_rel: str) -> str:
    rel = str(workspace_rel).strip()
    if not rel or rel == ".":
        return command
    command_text = str(command or "").strip()
    if not command_text:
        return command
    if command_text.lower().startswith("cd "):
        return command
    safe_rel = rel.replace('"', '\\"')
    if os.name == "nt":
        return f'cd /d "{safe_rel}" && {command_text}'
    return f'cd "{safe_rel}" && {command_text}'


def _join_command_tokens(tokens: list[str]) -> str:
    out: list[str] = []
    for token in tokens:
        text = str(token)
        if os.name == "nt":
            if any(ch.isspace() for ch in text):
                escaped = text.replace('"', '\\"')
                out.append(f'"{escaped}"')
            else:
                out.append(text)
        else:
            out.append(shlex.quote(text))
    return " ".join(out).strip()


def _pytest_args_start_index(tokens: list[str]) -> int:
    if not tokens:
        return -1
    binary = str(tokens[0]).strip().lower()
    if binary == "pytest" or binary.endswith("/pytest") or binary.endswith("\\pytest"):
        return 1
    if binary in {"python", "python3", "py"}:
        for idx in range(1, len(tokens) - 1):
            if str(tokens[idx]).strip() != "-m":
                continue
            module = str(tokens[idx + 1]).strip().lower()
            if module == "pytest":
                return idx + 2
    return -1


def _split_pytest_node_target(token: str) -> tuple[str, str]:
    text = str(token or "").strip()
    if "::" not in text:
        return text, ""
    path_part, node_part = text.split("::", 1)
    return path_part, node_part


def _is_likely_pytest_path_token(token: str) -> bool:
    value = str(token or "").strip()
    if not value or value.startswith("-"):
        return False
    if value.lower().endswith(".py"):
        return True
    if "/" in value or "\\" in value:
        return True
    return bool(value.startswith("."))


def _rebase_pytest_target_for_workspace(
    token: str,
    *,
    workspace_rel: str,
    project_dir: Path,
) -> str:
    rel = str(workspace_rel).strip()
    if not rel or rel == ".":
        return token
    path_part, node_part = _split_pytest_node_target(token)
    if not _is_likely_pytest_path_token(path_part):
        return token

    project_root = project_dir.resolve()
    workspace_path = (project_root / rel).resolve()
    normalized = _normalize_path(path_part)
    if normalized.startswith("../"):
        return token

    try:
        if Path(normalized).is_absolute():
            candidate_abs = Path(normalized).resolve()
        else:
            candidate_abs = (project_root / Path(normalized)).resolve()
    except OSError:
        return token

    if not candidate_abs.exists():
        return token

    try:
        rebased = candidate_abs.relative_to(workspace_path).as_posix()
    except ValueError:
        return token

    rebuilt = rebased
    if node_part:
        rebuilt = f"{rebuilt}::{node_part}"
    return rebuilt


def _rewrite_pytest_targets_for_workspace(
    command: str,
    *,
    workspace_rel: str,
    project_dir: Path,
) -> str:
    tokens = _parse_command_tokens(command)
    args_start = _pytest_args_start_index(tokens)
    if args_start < 0:
        return command

    changed = False
    rewritten = list(tokens)
    for idx in range(args_start, len(rewritten)):
        token = str(rewritten[idx]).strip()
        if not token or token.startswith("-"):
            continue
        updated = _rebase_pytest_target_for_workspace(
            token,
            workspace_rel=workspace_rel,
            project_dir=project_dir,
        )
        if updated != token:
            rewritten[idx] = updated
            changed = True
    if not changed:
        return command
    return _join_command_tokens(rewritten)


def _pytest_targets_require_project_root(
    command: str,
    *,
    workspace_rel: str,
    project_dir: Path,
) -> bool:
    rel = str(workspace_rel).strip()
    if not rel or rel == ".":
        return False
    tokens = _parse_command_tokens(command)
    args_start = _pytest_args_start_index(tokens)
    if args_start < 0:
        return False

    workspace_path = (project_dir / rel).resolve()
    for idx in range(args_start, len(tokens)):
        token = str(tokens[idx]).strip()
        if not token or token.startswith("-"):
            continue
        path_part, _node_part = _split_pytest_node_target(token)
        if not _is_likely_pytest_path_token(path_part):
            continue
        normalized = _normalize_path(path_part)
        if not normalized or normalized.startswith("../"):
            continue
        candidate_root = (project_dir / normalized).resolve()
        if not candidate_root.exists():
            continue
        try:
            candidate_root.relative_to(workspace_path)
        except ValueError:
            return True
    return False


def _apply_workspace_routing(
    *,
    commands: list[str],
    config: dict[str, Any],
    changed_files: list[str],
) -> list[str]:
    runtime_cfg = dict(config.get("runtime", {}))
    if not bool(runtime_cfg.get("verify_workspace_routing_enabled", True)):
        return list(commands)

    meta_cfg = dict(config.get("meta", {}))
    project_dir_value = str(meta_cfg.get("project_dir", "")).strip()
    if not project_dir_value:
        return list(commands)
    project_dir = Path(project_dir_value)
    if not project_dir.exists():
        return list(commands)

    node_workspaces = _discover_node_workspaces(project_dir)
    python_workspaces = _discover_python_workspaces(project_dir)
    changed_normalized = [_normalize_path(item) for item in changed_files if str(item).strip()]

    routed: list[str] = []
    for command in commands:
        binary = _command_binary(command)
        workspace_rel = "."
        if binary in {"npm", "pnpm", "yarn"}:
            workspace_rel = _choose_workspace(
                command=command,
                changed_files=changed_normalized,
                workspaces=node_workspaces,
                kind="node",
            )
        elif binary in {"python", "pytest", "ruff", "mypy"}:
            workspace_rel = _choose_workspace(
                command=command,
                changed_files=changed_normalized,
                workspaces=python_workspaces,
                kind="python",
            )
            if _pytest_targets_require_project_root(
                command,
                workspace_rel=workspace_rel,
                project_dir=project_dir,
            ):
                workspace_rel = "."
        rewritten_command = _rewrite_pytest_targets_for_workspace(
            command,
            workspace_rel=workspace_rel,
            project_dir=project_dir,
        )
        routed.append(_wrap_command_with_workspace(rewritten_command, workspace_rel))
    return routed
