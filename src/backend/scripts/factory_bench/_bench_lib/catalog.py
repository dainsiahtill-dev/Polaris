"""Project catalog loading and explicit project-id selection.

Private helper module for run_factory_bench.
"""

from __future__ import annotations

# ruff: noqa: F821, E402
# mypy: ignore-errors


def _pull_namespace(module: object) -> None:
    """Copy non-dunder attributes into this module (private helpers + imports)."""
    g = globals()
    for key, value in vars(module).items():
        if key.startswith("__"):
            continue
        g[key] = value


from scripts.factory_bench._bench_lib import workspace as _workspace

_pull_namespace(_workspace)
del _workspace

_RUNTIME_PROJECT_BASES = (
    Path("/dev/shm/.polaris/projects"),
    Path(os.path.expanduser("~/.cache/polaris")) / ".polaris" / "projects",
    Path(os.path.expanduser("~/.cache/kernelone")) / ".polaris" / "projects",
)

_RUNTIME_ARTIFACT_GLOBS: dict[str, tuple[str, ...]] = {
    "plan": ("contracts/pm_tasks.contract.json", "contracts/plan.md", "results/pm.report.md"),
    "blueprint": (
        "blueprints/*.json",
        "contracts/chief_engineer.blueprint.json",
        "runs/*/contracts/chief_engineer.blueprint.json",
    ),
    "verdict": (
        "runs/*/qa/integration_qa.result.json",
        "results/integration_qa.result.json",
        "qa/report.json",
        "workspace/qa/*.report.json",
        "workspace/roles/qa/*/report.json",
    ),
    "director_result": ("runs/*/results/director.result.json", "results/director.result.json"),
}

_WORKSPACE_ARTIFACT_GLOBS: dict[str, tuple[str, ...]] = {
    "plan": (".polaris/docs/product/plan.md", ".polaris/docs/product/requirements.md", ".polaris/docs/*.md"),
    "blueprint": (".polaris/blueprints/*",),
    "verdict": (
        ".polaris/qa/*.report.json",
        ".polaris/roles/qa/*/report.json",
        ".polaris/runtime/qa/report.json",
    ),
    "director_result": (),
}


def _resolve_catalog_path(path: str | Path, *, base_dir: Path | None = None) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (base_dir or _FACTORY_BENCH_DIR) / candidate
    return candidate.resolve()


def _load_project_catalog(path: Path, *, seen: set[Path] | None = None) -> list[dict[str, Any]]:
    seen = set(seen or set())
    if path in seen:
        raise ValueError(f"factory-bench catalog extends cycle: {path}")
    seen.add(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    projects: list[dict[str, Any]] = []
    extends = data.get("extends") or []
    if isinstance(extends, str):
        extends = [extends]
    if not isinstance(extends, list):
        raise ValueError(f"factory-bench catalog {path} has invalid extends")
    for parent in extends:
        if not isinstance(parent, str) or not parent.strip():
            raise ValueError(f"factory-bench catalog {path} has invalid extends entry")
        projects.extend(_load_project_catalog(_resolve_catalog_path(parent, base_dir=path.parent), seen=seen))
    raw_projects = data.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError(f"factory-bench catalog {path} missing projects[]")
    projects.extend(item for item in raw_projects if isinstance(item, dict))
    return projects


def load_projects(projects_file: str | Path | None = None) -> list[dict[str, Any]]:
    projects = _load_project_catalog(_resolve_catalog_path(projects_file or _FIXTURE))
    seen: set[str] = set()
    duplicates: list[str] = []
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        if not project_id:
            raise ValueError("factory-bench catalog contains a project without id")
        if project_id in seen:
            duplicates.append(project_id)
        seen.add(project_id)
    if duplicates:
        raise ValueError(
            "factory-bench catalog contains duplicate project id(s): " + ", ".join(sorted(set(duplicates)))
        )
    return projects


def _level_local_project_aliases(projects: list[dict[str, Any]]) -> dict[str, str]:
    """Map level-local ids like L2-01 to the catalog's actual project id."""

    aliases: dict[str, str] = {}
    by_level: dict[int, list[dict[str, Any]]] = {}
    for project in projects:
        try:
            level = int(project.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        by_level.setdefault(level, []).append(project)

    for level, level_projects in by_level.items():
        for index, project in enumerate(level_projects, start=1):
            project_id = str(project.get("id") or "").strip()
            if not project_id:
                continue
            alias = f"L{level}-{index:02d}"
            aliases.setdefault(alias, project_id)
    return aliases


def _resolve_explicit_project_selection(
    projects: list[dict[str, Any]],
    wanted_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str], dict[str, str]]:
    available_ids = {str(p["id"]) for p in projects}
    level_local_aliases = _level_local_project_aliases(projects)
    resolved_ids: list[str] = []
    alias_to_canonical: dict[str, str] = {}
    missing_ids: list[str] = []
    for project_id in wanted_ids:
        if project_id in available_ids:
            resolved_ids.append(project_id)
            continue
        canonical_id = level_local_aliases.get(project_id)
        if canonical_id and canonical_id in available_ids:
            resolved_ids.append(canonical_id)
            alias_to_canonical[project_id] = canonical_id
            continue
        missing_ids.append(project_id)
    if missing_ids:
        return [], missing_ids, alias_to_canonical
    if len(set(resolved_ids)) != len(resolved_ids):
        duplicates = sorted({item for item in resolved_ids if resolved_ids.count(item) > 1})
        return [], [f"duplicate resolved project id(s): {', '.join(duplicates)}"], alias_to_canonical

    wanted_id_by_canonical = {canonical: alias for alias, canonical in alias_to_canonical.items()}
    wanted_id_set = set(resolved_ids)
    selected: list[dict[str, Any]] = []
    for project in projects:
        canonical_id = str(project["id"])
        if canonical_id not in wanted_id_set:
            continue
        requested_id = wanted_id_by_canonical.get(canonical_id, canonical_id)
        item = dict(project)
        if requested_id != canonical_id:
            item["id"] = requested_id
            item["requested_project_id"] = requested_id
            item["canonical_catalog_project_id"] = canonical_id
        selected.append(item)
    return selected, [], alias_to_canonical
