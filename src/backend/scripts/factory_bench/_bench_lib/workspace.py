"""Workspace identity, catalog metadata, director-resume rehydration.

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


from scripts.factory_bench._bench_lib import constants as _constants

_pull_namespace(_constants)
del _constants


def _sanitize_run_id(raw: str | None) -> str:
    """Return a filesystem-safe run_id.

    If *raw* is non-empty after stripping, replace any character outside
    ``[A-Za-z0-9._-]`` with ``-`` and collapse consecutive dashes.
    If *raw* is empty/None, generate a stable uuid4 hex.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw or "").strip()).strip("-")
    return cleaned if cleaned else _uuid.uuid4().hex[:12]


def _resolve_bench_work_dir(raw_work_dir: str) -> Path:
    """Resolve the bench output root before deriving project workspaces."""
    raw_value = str(raw_work_dir or "").strip()
    if not raw_value:
        raise ValueError("--work-dir must not be empty")
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    resolved = path.resolve()
    if resolved == _REPO_ROOT:
        raise ValueError("--work-dir must not resolve to the Polaris repository root")
    return resolved


def _bench_workspace_component(raw: str, *, fallback: str) -> str:
    """Return one bounded, non-traversing Bench workspace path component."""
    component = _sanitize_run_id(raw)[:96]
    return fallback if component in {"", ".", ".."} else component


def _identity_workspace_component(raw: str, *, fallback: str) -> str:
    """Return a readable path component bound to the complete raw identity."""
    normalized = str(raw or "").strip()
    slug = _bench_workspace_component(normalized or fallback, fallback=fallback)[:72]
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{slug}-{digest}"


def _workspace_physical_identity(workspace: Path) -> dict[str, int]:
    """Return one non-symlink directory identity or fail closed."""
    resolved = workspace.expanduser().absolute()
    snapshot = os.lstat(resolved)
    if stat.S_ISLNK(snapshot.st_mode) or not stat.S_ISDIR(snapshot.st_mode):
        raise RuntimeError(f"Bench workspace is not a physical directory: {resolved}")
    return {"device": int(snapshot.st_dev), "inode": int(snapshot.st_ino)}


def _workspace_relative_components(bench_workspace: Path, workspace: Path) -> tuple[Path, tuple[str, ...]]:
    """Return one lexical workspace locator rooted at the authorized Bench directory."""
    root = bench_workspace.expanduser().absolute()
    candidate = workspace.expanduser().absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Bench workspace escaped the authorized root") from exc
    components = tuple(relative.parts)
    if not components:
        raise RuntimeError("Bench workspace must not equal the authorized root")
    return root, components


def _workspace_catalog_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_workspace_catalog_meta_exclusive(
    bench_workspace: Path,
    workspace: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind immutable UTF-8 catalog metadata through a root-anchored capability."""
    root, components = _workspace_relative_components(bench_workspace, workspace)
    directory_fd = _open_bench_directory_hierarchy(root, components, create=False)
    try:
        held = os.fstat(directory_fd)
        bound_payload = {
            **dict(payload),
            "workspace_nonce": components[-1],
            "workspace_device": int(held.st_dev),
            "workspace_inode": int(held.st_ino),
        }
        encoded = (json.dumps(bound_payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(".catalog_meta.json", file_flags, 0o600, dir_fd=directory_fd)
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(file_fd, view)
                if written <= 0:
                    raise OSError("catalog metadata write made no progress")
                view = view[written:]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    verification_fd = _open_bench_directory_hierarchy(root, components, create=False)
    try:
        observed_after = os.fstat(verification_fd)
        if (held.st_dev, held.st_ino) != (observed_after.st_dev, observed_after.st_ino):
            raise RuntimeError("Bench workspace identity changed during catalog metadata write")
    finally:
        os.close(verification_fd)
    return bound_payload


def _read_workspace_catalog_meta_bound(
    bench_workspace: Path,
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, int], str]:
    """Read catalog metadata through a root-anchored no-follow capability."""
    root, components = _workspace_relative_components(bench_workspace, workspace)
    directory_fd = _open_bench_directory_hierarchy(root, components, create=False)
    try:
        held = os.fstat(directory_fd)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(".catalog_meta.json", file_flags, dir_fd=directory_fd)
        try:
            file_before = os.fstat(file_fd)
            if not stat.S_ISREG(file_before.st_mode):
                raise RuntimeError("Bench workspace catalog metadata is not a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(file_fd, 65536):
                chunks.append(chunk)
            file_after = os.fstat(file_fd)
            if (
                file_before.st_dev,
                file_before.st_ino,
                file_before.st_size,
                file_before.st_mtime_ns,
            ) != (
                file_after.st_dev,
                file_after.st_ino,
                file_after.st_size,
                file_after.st_mtime_ns,
            ):
                raise RuntimeError("Bench workspace catalog metadata changed during read")
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)
    verification_fd = _open_bench_directory_hierarchy(root, components, create=False)
    try:
        observed_after = os.fstat(verification_fd)
        if (held.st_dev, held.st_ino) != (observed_after.st_dev, observed_after.st_ino):
            raise RuntimeError("Bench workspace identity changed during catalog metadata read")
    finally:
        os.close(verification_fd)
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Bench workspace catalog metadata must be an object")
    return payload, {"device": int(held.st_dev), "inode": int(held.st_ino)}, _workspace_catalog_hash(payload)


def _workspace_catalog_meta_matches(
    bench_workspace: Path,
    workspace: Path,
    *,
    run_id: str,
    project_id: str,
) -> bool:
    """Check exact raw identity, nonce, and physical directory binding."""
    try:
        payload, identity, _catalog_hash = _read_workspace_catalog_meta_bound(bench_workspace, workspace)
    except (OSError, RuntimeError, ValueError):
        return False
    return bool(
        payload.get("run_id") == run_id
        and payload.get("project_id") == project_id
        and payload.get("workspace_nonce") == workspace.name
        and payload.get("workspace_device") == identity["device"]
        and payload.get("workspace_inode") == identity["inode"]
    )


def _workspace_catalog_meta_matches_project(
    bench_workspace: Path,
    workspace: Path,
    *,
    project_id: str,
) -> bool:
    """Match a resumable physical workspace independently of a new attempt id."""
    try:
        payload, identity, _catalog_hash = _read_workspace_catalog_meta_bound(bench_workspace, workspace)
    except (OSError, RuntimeError, ValueError):
        return False
    return bool(
        payload.get("project_id") == project_id
        and str(payload.get("run_id") or "").strip()
        and payload.get("workspace_nonce") == workspace.name
        and payload.get("workspace_device") == identity["device"]
        and payload.get("workspace_inode") == identity["inode"]
    )


def _require_workspace_catalog_meta(
    bench_workspace: Path,
    workspace: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    """Return exact immutable metadata or reject resume before launch."""
    try:
        payload, identity, _catalog_hash = _read_workspace_catalog_meta_bound(bench_workspace, workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("director-resume workspace metadata is missing or invalid") from exc
    mismatches = [key for key, value in expected.items() if payload.get(key) != value]
    if payload.get("workspace_nonce") != workspace.name:
        mismatches.append("workspace_nonce")
    if payload.get("workspace_device") != identity["device"]:
        mismatches.append("workspace_device")
    if payload.get("workspace_inode") != identity["inode"]:
        mismatches.append("workspace_inode")
    if mismatches:
        raise RuntimeError(f"director-resume workspace metadata mismatch: {sorted(set(mismatches))}")
    return payload


def _open_bench_directory_hierarchy(
    root: Path,
    components: tuple[str, ...],
    *,
    create: bool,
) -> int:
    """Open one physical directory hierarchy without following symlinks."""
    root_path = root.expanduser().absolute()
    root_identity = _workspace_physical_identity(root_path)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(root_path, directory_flags)
    try:
        root_held = os.fstat(current_fd)
        if (root_held.st_dev, root_held.st_ino) != (root_identity["device"], root_identity["inode"]):
            raise RuntimeError("authorized Bench root identity changed before traversal")
        for component in components:
            if component in {"", ".", ".."} or "/" in component or os.sep in component:
                raise RuntimeError("invalid Bench workspace hierarchy component")
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
            try:
                child_fd = os.open(component, directory_flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                raise RuntimeError("Bench workspace hierarchy disappeared during creation") from None
            except OSError as exc:
                raise RuntimeError("Bench workspace hierarchy is not a physical directory") from exc
            child_held = os.fstat(child_fd)
            child_observed = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(child_held.st_mode) or not stat.S_ISDIR(child_observed.st_mode):
                os.close(child_fd)
                raise RuntimeError("Bench workspace hierarchy component is not a directory")
            if (child_held.st_dev, child_held.st_ino) != (child_observed.st_dev, child_observed.st_ino):
                os.close(child_fd)
                raise RuntimeError("Bench workspace hierarchy identity changed during traversal")
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _allocate_fresh_project_workspace(
    bench_workspace: Path,
    *,
    project_id: str,
    run_id: str,
    max_attempts: int = 32,
) -> Path:
    """Allocate a never-reused physical workspace before isolated launch.

    KernelOne lock authority is permanently bound to the enrolled runtime-root
    inode. Freshness must therefore come from a new workspace identity, never
    from deleting and recreating an already-enrolled runtime directory.
    """
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    root = bench_workspace.expanduser().absolute()
    run_component = _identity_workspace_component(run_id, fallback="run")
    project_component = _identity_workspace_component(project_id, fallback="project")
    parent = root / "workspaces" / run_component / project_component
    parent_fd = _open_bench_directory_hierarchy(
        root,
        ("workspaces", run_component, project_component),
        create=True,
    )
    try:
        for _attempt in range(max_attempts):
            nonce = secrets.token_hex(12)
            try:
                os.mkdir(nonce, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            created = os.stat(nonce, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(created.st_mode):
                raise RuntimeError("fresh project workspace allocation was not a directory")
            candidate = parent / nonce
            observed = os.lstat(candidate)
            if (created.st_dev, created.st_ino) != (observed.st_dev, observed.st_ino):
                raise RuntimeError("fresh project workspace identity changed during allocation")
            return candidate
    finally:
        os.close(parent_fd)
    raise RuntimeError("unable to allocate a unique fresh project workspace")


def _project_workspace_for_run(
    bench_workspace: Path,
    *,
    project_id: str,
    run_id: str,
    resume_director: bool,
) -> Path:
    """Resolve the physical workspace identity before any backend is started."""
    if not resume_director:
        return _allocate_fresh_project_workspace(
            bench_workspace,
            project_id=project_id,
            run_id=run_id,
        )
    root = bench_workspace.expanduser().absolute()
    _workspace_physical_identity(root)
    legacy_component = _bench_workspace_component(project_id, fallback="project")
    legacy_workspace = root / legacy_component
    try:
        os.lstat(legacy_workspace)
    except FileNotFoundError:
        pass
    else:
        raise RuntimeError("legacy director-resume workspace is not identity-bound; explicit migration is required")
    candidates: list[Path] = []

    project_component = _identity_workspace_component(project_id, fallback="project")
    workspaces_root = root / "workspaces"
    try:
        workspaces_fd = _open_bench_directory_hierarchy(
            root,
            ("workspaces",),
            create=False,
        )
    except FileNotFoundError:
        workspaces_fd = -1
    if workspaces_fd >= 0:
        try:
            for run_component in sorted(os.listdir(workspaces_fd)):
                run_snapshot = os.stat(run_component, dir_fd=workspaces_fd, follow_symlinks=False)
                if stat.S_ISLNK(run_snapshot.st_mode):
                    raise RuntimeError("director-resume run directory must not be a symlink")
                if not stat.S_ISDIR(run_snapshot.st_mode):
                    continue
                fresh_parent = workspaces_root / run_component / project_component
                try:
                    fresh_parent_fd = _open_bench_directory_hierarchy(
                        root,
                        ("workspaces", run_component, project_component),
                        create=False,
                    )
                except FileNotFoundError:
                    continue
                try:
                    for entry_name in sorted(os.listdir(fresh_parent_fd)):
                        snapshot = os.stat(entry_name, dir_fd=fresh_parent_fd, follow_symlinks=False)
                        if stat.S_ISLNK(snapshot.st_mode):
                            raise RuntimeError("director-resume candidate must not be a symlink")
                        if not stat.S_ISDIR(snapshot.st_mode):
                            continue
                        candidate = (fresh_parent / entry_name).absolute()
                        if _workspace_catalog_meta_matches_project(root, candidate, project_id=project_id):
                            candidates.append(candidate)
                finally:
                    os.close(fresh_parent_fd)
        finally:
            os.close(workspaces_fd)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            f"director-resume workspace not found for attempt_run_id={run_id!r} project_id={project_component!r}"
        )
    raise RuntimeError(
        f"director-resume workspace is ambiguous for attempt_run_id={run_id!r} "
        f"project_id={project_component!r}: {len(candidates)} candidates"
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _director_resume_plan_tasks(workspace: Path) -> list[dict[str, Any]]:
    payload = _load_json_object(Path(resolve_runtime_path(str(workspace), "runtime/tasks/plan.json")))
    tasks = payload.get("tasks")
    return [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []


def _director_resume_task_files(task_dir: Path) -> list[Path]:
    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(task_dir)
    task_files = inspection.get("task_files")
    if not isinstance(task_files, list):
        return []
    return [task_dir / str(name) for name in task_files if str(name or "").strip()]


def _director_resume_task_payloads(task_dir: Path) -> list[dict[str, Any]]:
    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(task_dir)
    task_rows = inspection.get("task_rows")
    return [dict(row) for row in task_rows if isinstance(row, dict)] if isinstance(task_rows, list) else []


def _director_resume_task_rows_mtime(task_dir: Path) -> float:
    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(task_dir)
    try:
        return float(inspection.get("latest_mtime") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _director_resume_has_taskboard(workspace: Path) -> bool:
    task_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    return bool(_director_resume_task_files(task_dir))


def _director_resume_workspace_slug(workspace_key: str) -> str:
    match = re.match(r"^(?P<slug>.+)-[0-9a-f]{12}$", workspace_key)
    return str(match.group("slug")) if match else workspace_key


def _director_resume_source_task_dirs(workspace: Path) -> list[Path]:
    roots = resolve_storage_roots(str(workspace))
    current_task_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks")).resolve()
    slug = _director_resume_workspace_slug(str(roots.workspace_key))
    runtime_project_bases = [Path(roots.runtime_projects_root)]
    runtime_project_bases.extend(Path(path) for path in globals().get("_RUNTIME_PROJECT_BASES", ()))
    candidates: list[Path] = []
    with contextlib.suppress(OSError):
        for runtime_projects_root in dict.fromkeys(runtime_project_bases):
            if not runtime_projects_root.exists():
                continue
            for project_root in runtime_projects_root.glob(f"{slug}-*"):
                task_dir = project_root / "runtime" / "tasks"
                if task_dir.resolve() == current_task_dir:
                    continue
                if (task_dir / "plan.json").is_file() and _director_resume_task_files(task_dir):
                    candidates.append(task_dir)
    return sorted(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)


def _director_resume_taskboard_score(task_dir: Path) -> tuple[int, int, float]:
    task_payloads = _director_resume_task_payloads(task_dir)
    plan = _load_json_object(task_dir / "plan.json")
    tasks = plan.get("tasks")
    planned_count = len(tasks) if isinstance(tasks, list) else 0
    blueprint_dir = task_dir.parent / "blueprints"
    blueprint_count = 0
    with contextlib.suppress(OSError):
        blueprint_count = len([path for path in blueprint_dir.glob("ce_*.json") if path.is_file()])
    mtime = max(
        (path.stat().st_mtime for path in [task_dir / "plan.json"] if path.exists()),
        default=0.0,
    )
    mtime = max(mtime, _director_resume_task_rows_mtime(task_dir))
    return (blueprint_count, min(planned_count, len(task_payloads)), mtime)


def _raise_director_resume_task_runtime_failure(result: dict[str, Any]) -> None:
    raise RuntimeError(
        "Director resume task rows must be prepared through task_runtime execution evidence: "
        f"{json.dumps(result, ensure_ascii=False, sort_keys=True)}"
    )


def _rehydrate_director_resume_taskboard(workspace: Path) -> str:
    target_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    if _director_resume_plan_tasks(workspace) and _director_resume_has_taskboard(workspace):
        _reset_current_director_resume_taskboard(workspace, target_dir=target_dir)
        return ""
    candidates = sorted(
        _director_resume_source_task_dirs(workspace),
        key=_director_resume_taskboard_score,
        reverse=True,
    )
    for source_dir in candidates:
        plan_payload = _load_json_object(source_dir / "plan.json")
        task_payloads = _director_resume_task_payloads(source_dir)
        if not isinstance(plan_payload.get("tasks"), list) or not task_payloads:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_dir / "plan.json", target_dir / "plan.json")
        prepare_result = TaskRuntimeService(str(workspace)).import_task_rows_for_reexecution(
            task_payloads,
            source="factory_bench.director_resume.rehydration",
            source_task_dir=str(source_dir),
        )
        if not bool(prepare_result.get("success")):
            _raise_director_resume_task_runtime_failure(prepare_result)
        copied: list[str] = ["plan.json", *[str(path) for path in prepare_result.get("imported_files", [])]]
        evidence = {
            "schema_version": "factory.director_resume_taskboard_rehydration.v1",
            "source": "factory_bench",
            "source_task_dir": str(source_dir),
            "target_task_dir": str(target_dir),
            "copied_files": copied,
            "task_runtime_prepare_result": prepare_result,
            "reset_statuses": "all_task_records",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (target_dir / "director_resume_rehydration.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(source_dir)
    return ""


def _reset_current_director_resume_taskboard(workspace: Path, *, target_dir: Path | None = None) -> dict[str, Any]:
    """Reopen only unfinished PM tasks while preserving verified work."""
    task_dir = target_dir or Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    task_files = _director_resume_task_files(task_dir)
    if not task_files:
        return {}

    eligible_task_ids = tuple(
        task_id
        for task in _director_resume_plan_tasks(workspace)
        if (task_id := str(task.get("id") or task.get("task_id") or task.get("uid") or "").strip())
    )

    prepare_result = TaskRuntimeService(str(workspace)).reset_task_rows_for_reexecution(
        source="factory_bench.director_resume.reset",
        preserve_completed=True,
        eligible_external_task_ids=eligible_task_ids,
    )
    if not bool(prepare_result.get("success")):
        _raise_director_resume_task_runtime_failure(prepare_result)

    evidence = {
        "schema_version": "factory.director_resume_taskboard_reset.v1",
        "source": "factory_bench",
        "workspace": str(workspace),
        "target_task_dir": str(task_dir),
        "reset_files": prepare_result.get("reset_files", []),
        "preserved_files": prepare_result.get("preserved_files", []),
        "excluded_files": prepare_result.get("excluded_files", []),
        "skipped_files": prepare_result.get("skipped_files", []),
        "deleted_session_files": prepare_result.get("deleted_session_files", []),
        "task_runtime_prepare_result": prepare_result,
        "reset_statuses": "unfinished_pm_task_records_only",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "director_resume_reset.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def _director_resume_has_ce_blueprint(workspace: Path) -> bool:
    candidates = [workspace / ".polaris" / "blueprints" / "latest.review.json"]
    state_dir = Path(resolve_runtime_path(str(workspace), "runtime/state/blueprints"))
    with contextlib.suppress(OSError):
        candidates.extend(path for path in state_dir.glob("*.review.json") if path.is_file())
    for path in candidates:
        payload = _load_json_object(path)
        blueprints = payload.get("blueprints")
        try:
            generated_count = int(payload.get("generated_blueprints") or 0)
        except (TypeError, ValueError):
            generated_count = 0
        if generated_count > 0 or (isinstance(blueprints, list) and bool(blueprints)):
            return True
    return False


def _director_resume_snapshot_manifest(workspace: Path) -> Path:
    return workspace / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"


def _director_resume_snapshot_ready(workspace: Path) -> bool:
    payload = _load_json_object(_director_resume_snapshot_manifest(workspace))
    return str(payload.get("snapshot_kind") or "") == "pre_director_workspace"


def _director_resume_declared_delivery_paths(tasks: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for task in tasks:
        for key in ("target_files", "scope_paths"):
            raw = task.get(key)
            if isinstance(raw, str):
                values = [raw]
            elif isinstance(raw, list):
                values = [str(item) for item in raw if str(item).strip()]
            else:
                values = []
            for value in values:
                normalized = value.replace("\\", "/").strip().strip("/")
                if normalized and normalized not in paths:
                    paths.append(normalized)
    return paths


def _director_resume_delivery_files(workspace: Path, tasks: list[dict[str, Any]]) -> list[str]:
    allowed_pre_director_inputs = {
        ".catalog_meta.json",
        "requirements.md",
    }
    candidates = {
        "package.json",
        "tsconfig.json",
        "index.html",
        "README.md",
        "src",
        "tests",
    }
    candidates.update(_director_resume_declared_delivery_paths(tasks))
    candidates.difference_update(allowed_pre_director_inputs)
    existing: list[str] = []
    root = workspace.resolve()
    for candidate in sorted(candidates):
        path = (root / candidate).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            existing.append(candidate)
            continue
        if path.exists():
            existing.append(candidate)
    return existing


def _prepare_director_resume_workspace(workspace: Path) -> None:
    from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor

    executor = OrchestrationStageExecutor(workspace)
    # Modern TaskRuntime is FactStream-authoritative and may intentionally have
    # no ``runtime/tasks/task_*.json`` mirror after an isolated backend stops.
    # Restore the durable PM plan mirror first. Director dispatch will
    # materialize and bind canonical task rows to the NEW Factory run via
    # ``_materialize_pm_plan_taskboard``; the bench preflight must not require
    # obsolete file rows or mint unbound TaskRuntime facts itself.
    executor._ensure_pm_plan_contract_available()
    has_current_plan = bool(_director_resume_plan_tasks(workspace))
    has_current_taskboard = _director_resume_has_taskboard(workspace)
    if _director_resume_has_ce_blueprint(workspace) and (has_current_taskboard or not has_current_plan):
        _rehydrate_director_resume_taskboard(workspace)
    tasks = _director_resume_plan_tasks(workspace)
    missing: list[str] = []
    if not tasks:
        missing.append("runtime/tasks/plan.json")
    if not _director_resume_has_ce_blueprint(workspace):
        missing.append(".polaris/blueprints/latest.review.json")
    if missing:
        raise ValueError("Director-only resume missing evidence: " + ", ".join(missing))
    if _director_resume_snapshot_ready(workspace):
        return
    delivery_files = _director_resume_delivery_files(workspace, tasks)
    if delivery_files:
        raise ValueError(
            "Director-only resume snapshot is missing and workspace already has delivery files: "
            + ", ".join(delivery_files[:12])
        )
    executor._create_pre_director_snapshot(run_id="bench_director_resume_seed")


def _attach_platform_residual_attribution(
    payload: dict[str, Any],
    *,
    source_path: str = "",
) -> dict[str, Any]:
    """Embed non-terminal platform residual attribution into audits.

    Does not change bench pass/fail. Supervisors read
    ``platform_residual_attribution.primary.primary_module_id`` after a failed
    run.  Bench never owns scheduling or terminal model-ceiling authority.
    """

    pack = build_factory_audits_attribution_pack(payload, source_path=source_path)
    payload["platform_residual_attribution"] = pack
    return payload


def _next_immutable_json_path(path: Path) -> Path:
    """Return the first available immutable JSON path for *path*.

    If *path* does not exist, return *path*; otherwise try ``<stem>.2.json``,
    ``<stem>.3.json``, … until an unused slot is found.
    """
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}.{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> Path:
    """Write *payload* as UTF-8 JSON to *path*, never overwriting an existing file.

    If *path* already exists, write to ``<stem>.2.json``, ``<stem>.3.json``, …
    using the first available slot.  Returns the path actually written.
    """
    target = _next_immutable_json_path(path)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return target
