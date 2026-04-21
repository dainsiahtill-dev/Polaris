from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

POLICY_URI = "policy://polaris/agent-spec/v3.0"
LEGACY_POLICY_URI = "policy://polaris/agent-spec/v2.11"
ACTIVE_RULES_URI = "polaris://policy/active_rules"


def normalize_project_root(project_dir: Path | str | None = None) -> Path:
    raw = Path(project_dir) if project_dir is not None else Path(".")
    return Path(os.path.abspath(str(raw)))


@dataclass(frozen=True)
class PolarisPaths:
    project_root: Path
    policy_path: Path
    legacy_policy_path: Path
    hp_root: Path
    blueprints_dir: Path
    logs_dir: Path
    runtime_dir: Path
    events_log: Path
    runs_dir: Path
    sentinels_dir: Path
    snapshots_dir: Path
    accel_home: Path


def resolve_polaris_paths(
    project_dir: Path | str | None = None,
) -> PolarisPaths:
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    project_root = normalize_project_root(project_dir)
    metadata_dir = get_workspace_metadata_dir_name()
    hp_root = project_root / metadata_dir
    runtime_dir = hp_root / "runtime"
    runs_dir = runtime_dir / "policy_runs"
    return PolarisPaths(
        project_root=project_root,
        policy_path=project_root / "policy" / "polaris-agent-spec-v3.0.md",
        legacy_policy_path=project_root / "policy" / "polaris-agent-spec-v2.11.md",
        hp_root=hp_root,
        blueprints_dir=hp_root / "docs" / "blueprints",
        logs_dir=hp_root / "logs",
        runtime_dir=runtime_dir,
        events_log=runtime_dir / "events.jsonl",
        runs_dir=runs_dir,
        sentinels_dir=runs_dir / "sentinels",
        snapshots_dir=hp_root / "snapshots",
        accel_home=runtime_dir / "agent-accel",
    )


def default_accel_runtime_home(project_dir: Path | str | None = None) -> Path:
    return resolve_polaris_paths(project_dir).accel_home


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_artifact_path(
    project_dir: Path | str,
    path_value: Any,
    *,
    default_subdir: str = "logs",
    default_name: str = "artifact.json",
) -> Path:
    paths = resolve_polaris_paths(project_dir)
    base_dir = paths.logs_dir if default_subdir == "logs" else paths.runtime_dir

    raw = str(path_value or "").strip()
    if not raw:
        target = base_dir / default_name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.resolve()

    given = Path(raw)
    if given.is_absolute():
        candidate = given.resolve()
        if not _is_within(candidate, paths.hp_root.resolve()):
            candidate = (paths.logs_dir / candidate.name).resolve()
    else:
        candidate = (paths.project_root / given).resolve()
        if not _is_within(candidate, paths.hp_root.resolve()):
            candidate = (paths.logs_dir / given).resolve()

    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate
