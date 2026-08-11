"""Bench backend session, observation events, and isolated launcher instances.

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


from scripts.factory_bench._bench_lib import artifacts as _artifacts

_pull_namespace(_artifacts)
del _artifacts

_BENCH_BACKEND: dict[str, str] = {"backend_url": "", "session_id": "", "token": ""}

_BENCH_OBSERVATION_CIRCUIT: dict[str, str] = {"disabled_reason": ""}


def configure_bench_backend(backend_url: str, session_id: str, token: str = "") -> None:
    """Set the active backend URL + session id + token (called once by main())."""
    _BENCH_BACKEND["backend_url"] = backend_url
    _BENCH_BACKEND["session_id"] = session_id
    _BENCH_BACKEND["token"] = token
    _BENCH_OBSERVATION_CIRCUIT["disabled_reason"] = ""


def _bench_observation_disabled() -> bool:
    return bool(str(_BENCH_OBSERVATION_CIRCUIT.get("disabled_reason") or "").strip())


def _disable_bench_observation(reason: str) -> None:
    if _bench_observation_disabled():
        return
    _BENCH_OBSERVATION_CIRCUIT["disabled_reason"] = str(reason or "shared observation failed").strip()
    print(
        f"[factory-bench] shared bench observation disabled: {_BENCH_OBSERVATION_CIRCUIT['disabled_reason']}",
        file=sys.stderr,
        flush=True,
    )


def _emit_bench_event(
    *,
    workspace: Path,
    project_id: str,
    level: int,
    name: str,
    summary: str = "",
    meta: dict[str, Any] | None = None,
    cache_root: str | None = None,
) -> bool:
    """Append a bench-level event to the workspace's runtime.events.jsonl
    AND forward it to the Factory HTTP backend (if wired by main()).

    Local path: writes to ``<cache_root>/runs/<run_id>/events/runtime.events.jsonl``
    (resolved via ``latest_run.json``) so the Polaris WS bridge at
    ``/v2/ws/runtime`` can stream it to the ContextOS real-time dashboard.

    Shared observation path: when main() explicitly wired a shared bench
    session, POSTs the event to ``/v2/factory/bench/sessions/{id}/events``.
    This bridge is internal-test-only and best-effort. It must never be
    treated as the isolated project's runtime source of truth.

    Returns True if at least one of the two paths succeeded; False only when
    neither produced a record (e.g. local path has no run_id and backend
    is not wired). All failures are non-fatal: the bench continues.
    """
    try:
        from polaris.kernelone.events import emit_event
    except ImportError:
        # If we cannot import the local emitter, we can still push to the
        # Factory HTTP backend below — do NOT bail out before that.
        emit_event = None

    payload_meta: dict[str, Any] = dict(meta or {})
    payload_meta.setdefault("project_id", str(project_id))
    payload_meta.setdefault("level", int(level))
    payload_meta.setdefault("source", "factory-bench")

    # --- Local JSONL (WS-bridge side channel): best-effort, requires
    # a Polaris cache_root + latest_run.json. The real bench runtime
    # uses a plain parent work_dir (no .polaris), so this path often
    # legitimately has nothing to write into. The Factory HTTP push
    # below is the canonical real-time path and must run regardless.
    local_ok = False
    if not cache_root:
        cache_root = _resolve_bench_cache_root(workspace)
    if cache_root:
        pointer = Path(cache_root) / "latest_run.json"
        if pointer.is_file():
            try:
                pointer_payload = json.loads(pointer.read_text(encoding="utf-8"))
                run_id = str(pointer_payload.get("run_id") or "").strip()
            except (OSError, ValueError):
                run_id = ""
            if run_id:
                events_path = Path(cache_root) / "runs" / run_id / "events" / "runtime.events.jsonl"
                if emit_event is not None:
                    try:
                        emit_event(
                            str(events_path),
                            kind="event",
                            actor="factory-bench",
                            name=f"factory_bench.{name}",
                            summary=summary,
                            meta=payload_meta,
                        )
                        local_ok = True
                    except (OSError, ValueError, TypeError) as exc:
                        print(
                            f"[factory-bench] WS emit failed: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )

    # --- Shared Factory HTTP observation push. Runs independently of cache_root
    # when explicitly enabled, but remains best-effort and circuit-broken so a
    # busy main backend cannot stall isolated project execution.
    backend_ok = False
    backend_url = _BENCH_BACKEND.get("backend_url", "")
    backend_sid = _BENCH_BACKEND.get("session_id", "")
    if backend_url and backend_sid and not _bench_observation_disabled():
        backend_ok = _push_bench_event_to_backend(
            backend_url=backend_url,
            session_id=backend_sid,
            event_type=f"factory_bench.{name}",
            name=f"factory_bench.{name}",
            actor="factory-bench",
            summary=summary,
            meta=payload_meta,
            token=_BENCH_BACKEND.get("token", ""),
        )

    return local_ok or backend_ok


def _factory_role_from_phase(phase: str) -> str:
    token = str(phase or "").strip().lower()
    if token in {"pending", "intake", "planning", "pm_planning", "docs_check"}:
        return "pm"
    if "chief" in token or "blueprint" in token or token in {"ce", "ce_review"}:
        return "chief_engineer"
    if "director" in token or token in {"implementation", "mutation", "execution", "handover"}:
        return "director"
    if "qa" in token or "verification" in token or "quality" in token:
        return "qa"
    return "unknown"


def _emit_factory_phase_event(
    *,
    bench_workspace: Path,
    project_workspace: Path,
    project_id: str,
    level: int,
    title: str,
    status: str,
    phase_payload: dict[str, Any],
    cache_root: str | None = None,
) -> bool:
    phase = str(phase_payload.get("phase") or "").strip()
    run_status = str(phase_payload.get("status") or status or "").strip()
    if not phase and not run_status:
        return False
    role = _factory_role_from_phase(phase)
    run_id = str(phase_payload.get("run_id") or "").strip()
    summary_parts = [project_id]
    if role != "unknown":
        summary_parts.append(role)
    if phase:
        summary_parts.append(f"phase={phase}")
    if run_status:
        summary_parts.append(f"status={run_status}")
    project_workspace_full = str(project_workspace.resolve())
    meta: dict[str, Any] = {
        "project_id": project_id,
        "level": int(level),
        "title": title,
        "workspace": project_workspace_full,
        "workspace_path": project_workspace_full,
        "project_workspace": project_workspace_full,
        "phase": phase,
        "status": run_status,
        "role": role,
    }
    if run_id:
        meta["run_id"] = run_id
    return _emit_bench_event(
        workspace=bench_workspace,
        project_id=project_id,
        level=level,
        name="project.phase",
        summary=" ".join(part for part in summary_parts if part),
        meta=meta,
        cache_root=cache_root,
    )


def _emit_factory_task_runtime_event(
    *,
    bench_workspace: Path,
    project_workspace: Path,
    project_id: str,
    level: int,
    title: str,
    phase_payload: dict[str, Any],
    event_payload: dict[str, Any],
    cache_root: str | None = None,
) -> bool:
    project_workspace_full = str(project_workspace.resolve())
    task_id = str(event_payload.get("task_id") or "").strip()
    task_status = str(event_payload.get("status") or "").strip()
    event_type = str(event_payload.get("event_type") or "").strip()
    director_run_id = str(event_payload.get("run_id") or "").strip()
    factory_run_id = str(phase_payload.get("run_id") or event_payload.get("factory_run_id") or "").strip()
    summary_parts = [project_id, "director"]
    if task_id:
        summary_parts.append(f"task={task_id}")
    if event_type:
        summary_parts.append(event_type)
    if task_status:
        summary_parts.append(f"status={task_status}")
    meta: dict[str, Any] = {
        "project_id": project_id,
        "level": int(level),
        "title": title,
        "workspace": project_workspace_full,
        "workspace_path": project_workspace_full,
        "project_workspace": project_workspace_full,
        "phase": "director_dispatch",
        "status": task_status or str(phase_payload.get("status") or "running"),
        "role": "director",
        "task_id": task_id,
        "task_status": task_status,
        "task_runtime_event_type": event_type,
        "director_run_id": director_run_id,
        "run_id": factory_run_id,
        "session_id": str(event_payload.get("session_id") or "").strip(),
        "details": event_payload.get("details") if isinstance(event_payload.get("details"), dict) else {},
    }
    return _emit_bench_event(
        workspace=bench_workspace,
        project_id=project_id,
        level=level,
        name="project.task_runtime",
        summary=" ".join(part for part in summary_parts if part),
        meta={k: v for k, v in meta.items() if v not in (None, "")},
        cache_root=cache_root,
    )


_DEFAULT_BACKEND_URL = "http://127.0.0.1:49977"

_DEFAULT_LOCAL_BACKEND_TOKEN = "polaris-local-dev"

_BENCH_HTTP_TIMEOUT_S = 10.0  # bumped from 2.0: cold-start 49977 can exceed 2s

_BENCH_OBSERVATION_HTTP_TIMEOUT_S = 1.5


def _resolve_polaris_home(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the Polaris home directory (``~/.polaris``).

    Resolution order mirrors the Electron ``resolvepolarisHome`` helper in
    ``config-paths.cjs``:

    1. ``KERNELONE_HOME`` env var — if set and already named ``.polaris``,
       use it directly; otherwise append ``.polaris``.
    2. ``~/.polaris`` (platform home).
    """
    active_env = env or os.environ
    home_override = str(active_env.get("KERNELONE_HOME") or "").strip()
    if home_override:
        expanded = Path(home_override).expanduser().resolve()
        if expanded.name.lower() == ".polaris":
            return expanded
        return expanded / ".polaris"
    return Path.home() / ".polaris"


def _desktop_backend_info_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the path to ``desktop-backend.json`` written by Electron."""
    return _resolve_polaris_home(env) / "runtime" / "desktop-backend.json"


def _read_desktop_backend_info(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read and parse ``desktop-backend.json``.

    Returns an empty dict on any failure (missing file, malformed JSON,
    permission errors).  This is a *read-only* helper — it never creates
    or modifies the file.
    """
    path = _desktop_backend_info_path(env)
    try:
        if not path.exists():
            _logger.debug("desktop-backend.json not found at %s", path)
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _logger.debug("desktop-backend.json found at %s (token source found)", path)
            return data
        return {}
    except (ValueError, OSError):
        _logger.debug("desktop-backend.json unreadable at %s (token source missing)", path)
        return {}


def _resolve_backend_url(explicit: str | None = None) -> str:
    """Pick a backend URL from arg > env > desktop-backend info > default.

    Priority:
    1. *explicit* argument
    2. ``KERNELONE_BACKEND_URL`` env
    3. ``FACTORY_BENCH_BACKEND_URL`` env
    4. ``desktop-backend.json`` → ``backend.baseUrl``
    5. ``_DEFAULT_BACKEND_URL`` (127.0.0.1:49977)
    """
    candidate = (
        (explicit or "").strip()
        or os.environ.get("KERNELONE_BACKEND_URL", "").strip()
        or os.environ.get("FACTORY_BENCH_BACKEND_URL", "").strip()
        or _desktop_backend_url_from_info()
    )
    return candidate.rstrip("/") or _DEFAULT_BACKEND_URL


def _desktop_backend_url_from_info() -> str:
    """Extract baseUrl from desktop-backend.json, or "" if absent."""
    info = _read_desktop_backend_info()
    backend = info.get("backend")
    if isinstance(backend, dict):
        return str(backend.get("baseUrl") or "").strip()
    return ""


def _is_local_backend_url(url: str) -> bool:
    """Return True when *url* targets a loopback backend."""
    try:
        parsed = urlparse(str(url or ""))
    except (TypeError, ValueError):
        return False
    hostname = str(parsed.hostname or "").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _resolve_backend_token(explicit: str | None = None) -> str:
    """Pick a backend bearer token from arg > env > desktop-backend info.

    The Polaris factory router requires a Bearer token in the Authorization
    header (query tokens are intentionally rejected — see
    ``polaris.delivery.http.dependencies.require_auth``). The bench subprocess
    runs in a terminal and has no way to ask the Electron app for a token
    directly, so it reads the token Electron already persisted to
    ``desktop-backend.json`` as a fallback.

    Priority:
    1. *explicit* argument
    2. ``FACTORY_BENCH_BACKEND_TOKEN`` env
    3. ``KERNELONE_TOKEN`` env
    4. ``KERNELONE_BACKEND_TOKEN`` env
    5. ``desktop-backend.json`` → ``backend.token``

    Returns "" when no token is configured (the bench then makes
    unauthenticated requests, which is fine for dev mode with auth disabled).
    """
    token = (
        (explicit or "").strip()
        or os.environ.get("FACTORY_BENCH_BACKEND_TOKEN", "").strip()
        or os.environ.get("KERNELONE_TOKEN", "").strip()
        or os.environ.get("KERNELONE_BACKEND_TOKEN", "").strip()
        or _desktop_backend_token_from_info()
    )
    if not token and _is_local_backend_url(_resolve_backend_url()):
        token = _DEFAULT_LOCAL_BACKEND_TOKEN
    if token:
        _logger.debug("backend token source found")
    else:
        _logger.debug("backend token source missing — using unauthenticated requests")
    return token


def _desktop_backend_token_from_info() -> str:
    """Extract token from desktop-backend.json, or "" if absent."""
    info = _read_desktop_backend_info()
    backend = info.get("backend")
    if isinstance(backend, dict):
        return str(backend.get("token") or "").strip()
    return ""


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout_s: float = _BENCH_HTTP_TIMEOUT_S,
    token: str = "",
) -> dict[str, Any] | None:
    return _shared_http_post_json(url, body, timeout_s=timeout_s, token=token)


def _push_bench_session_to_backend(
    *,
    backend_url: str,
    work_dir: str,
    project_ids: list[str],
    total: int,
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    token: str = "",
) -> str | None:
    """Register a bench session with the Factory backend.

    Returns the assigned ``session_id`` on success, ``None`` on any failure
    (no backend / network error / non-2xx / malformed body). The bench run
    must continue in all cases; the only side effect of failure is that
    the Factory panel cannot show this run.
    """
    payload: dict[str, Any] = {
        "work_dir": str(work_dir),
        "project_ids": list(project_ids),
        "total": int(total),
        "metadata": dict(metadata or {}),
    }
    if session_id:
        payload["session_id"] = session_id
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions",
        payload,
        timeout_s=_BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        token=token,
    )
    if not isinstance(response, dict):
        return None
    sid = str(response.get("session_id") or "").strip()
    return sid or None


def _ensure_bench_session(
    *,
    backend_url: str,
    work_dir: str,
    project_ids: list[str],
    total: int,
    metadata: dict[str, Any] | None = None,
    requested_session_id: str = "",
    token: str = "",
) -> str:
    """Register a bench session and return the usable session id.

    An explicit ``FACTORY_BENCH_SESSION_ID`` is still a real frontend contract:
    the Factory panel can subscribe to ``event.bench:<id>`` only after the
    backend has a durable session row for that id.
    """

    requested = str(requested_session_id or "").strip()
    if not backend_url:
        return requested
    registered = _push_bench_session_to_backend(
        backend_url=backend_url,
        work_dir=work_dir,
        project_ids=project_ids,
        total=total,
        metadata=metadata,
        session_id=requested or None,
        token=token,
    )
    return registered or requested


def _bench_record_counts(records: list[dict[str, Any]], *, total: int) -> dict[str, int]:
    passed = sum(1 for record in records if record.get("all_checks_passed"))
    failed = sum(1 for record in records if not record.get("all_checks_passed"))
    attempted = len(records)
    return {
        "total": int(total),
        "attempted": attempted,
        "passed": passed,
        "failed": failed,
        "pending": max(0, int(total) - attempted),
    }


def _push_bench_event_to_backend(
    *,
    backend_url: str,
    session_id: str,
    event_type: str,
    name: str | None = None,
    actor: str | None = None,
    summary: str | None = None,
    ok: bool | None = None,
    meta: dict[str, Any] | None = None,
    token: str = "",
) -> bool:
    """Append a bench event to the active session on the Factory backend."""
    if _bench_observation_disabled():
        return False
    payload: dict[str, Any] = {
        "type": str(event_type),
        "name": name,
        "actor": actor,
        "summary": summary,
        "ok": ok,
        "meta": dict(meta or {}),
    }
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions/{session_id}/events",
        payload,
        timeout_s=_BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        token=token,
    )
    if response is None:
        _disable_bench_observation(f"event POST failed: {event_type}")
        return False
    return response is not None and bool(response.get("appended", False))


def _push_bench_complete_to_backend(
    *,
    backend_url: str,
    session_id: str,
    success: bool = True,
    summary: dict[str, Any] | None = None,
    token: str = "",
) -> bool:
    """Mark a bench session complete (or failed) on the Factory backend."""
    if _bench_observation_disabled():
        return False
    payload: dict[str, Any] = {
        "success": bool(success),
        "summary": dict(summary or {}),
    }
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions/{session_id}/complete",
        payload,
        timeout_s=_BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        token=token,
    )
    if response is None:
        _disable_bench_observation("complete POST failed")
        return False
    return response is not None and bool(response.get("updated", False))


def _push_bench_progress_to_backend(
    *,
    backend_url: str,
    session_id: str,
    completed: int,
    failed: int,
    token: str = "",
) -> bool:
    """Push live per-project counters so the front-end sees real-time progress.

    Without this, ``session.completed`` / ``session.failed`` stay at the
    zero they had at registration time and the bench UI shows ``0/Y 通过``
    for the whole run. The bench subprocess must call this after every
    project so each project.finished (success or fail) increments the
    right counter and the Nats-JetStream/WebSocket snapshot reflects it on the next tick.
    """
    payload: dict[str, Any] = {
        "completed": int(completed),
        "failed": int(failed),
    }
    if _bench_observation_disabled():
        return False
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions/{session_id}/progress",
        payload,
        timeout_s=_BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        token=token,
    )
    if response is None:
        _disable_bench_observation("progress POST failed")
        return False
    return response is not None and bool(response.get("updated", False))


def _push_bench_workspace_to_backend(
    *,
    backend_url: str,
    workspace: str,
    token: str = "",
    attempts: int = 3,
    retry_delay_seconds: float = 0.25,
) -> bool:
    """Switch the desktop backend to the project workspace before observation starts."""
    if not backend_url or not workspace:
        return False
    workspace_path = Path(workspace).expanduser()
    try:
        workspace_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.warning("factory bench workspace switch skipped; cannot prepare workspace %s: %s", workspace, exc)
        return False
    target_workspace = workspace_path.resolve()
    workspace_payload = str(target_workspace)
    max_attempts = max(1, int(attempts))
    for attempt in range(max_attempts):
        response = _http_post_json(
            f"{backend_url}/settings",
            {"workspace": workspace_payload},
            token=token,
        )
        if isinstance(response, dict):
            returned_workspace = str(response.get("workspace") or response.get("workspace_path") or "").strip()
            if returned_workspace:
                try:
                    returned_path = Path(returned_workspace).expanduser().resolve()
                except (OSError, RuntimeError, ValueError) as exc:
                    _logger.warning(
                        "factory bench workspace switch rejected malformed response workspace=%r: %s",
                        returned_workspace,
                        exc,
                    )
                else:
                    if returned_path == target_workspace:
                        return True
                    _logger.warning(
                        "factory bench workspace switch mismatch: requested=%s returned=%s",
                        target_workspace,
                        returned_path,
                    )
        if attempt < max_attempts - 1 and retry_delay_seconds > 0:
            time.sleep(retry_delay_seconds)
    return False


def _register_bench_project_instance(
    *,
    bench_session_id: str,
    project_id: str,
    project_title: str,
    level: int,
    bench_workspace: Path,
    project_workspace: str,
    backend_url: str,
    backend_token: str,
) -> None:
    """Register bench project activity in the platform instance registry.

    This is discovery metadata for the Launcher only. factory_bench remains an
    internal stress harness and must not become a production fact source.
    """
    try:
        from polaris.cells.instances.internal.service import (
            InstanceRecord,
            InstanceRegistry,
            default_polaris_root,
            sanitize_instance_id,
        )
    except (ImportError, RuntimeError):
        return

    parsed_backend = urlparse(backend_url or "")
    parsed_frontend = urlparse(os.environ.get("FACTORY_BENCH_FRONTEND_URL", ""))
    backend_port = int(parsed_backend.port or 0)
    frontend_port = int(parsed_frontend.port or 0)
    if backend_port <= 0:
        return

    instance_id = sanitize_instance_id(
        f"{bench_session_id}-{project_id}" if bench_session_id else f"factory-bench-{project_id}"
    )
    record = InstanceRecord(
        instance_id=instance_id,
        name=f"{project_id} {project_title}".strip(),
        kind="bench_project",
        polaris_root=str(default_polaris_root()),
        workspace=project_workspace,
        runtime_root=str((Path(project_workspace) / "runtime").resolve()),
        backend_port=backend_port,
        frontend_port=frontend_port,
        backend_url=backend_url,
        frontend_url=os.environ.get("FACTORY_BENCH_FRONTEND_URL", ""),
        token=backend_token,
        backend_reload=False,
        frontend_vite=bool(frontend_port),
        start_frontend=bool(frontend_port),
        status="observed",
        backend_pid=None,
        frontend_pid=None,
        bench={
            "session_id": bench_session_id,
            "project_id": project_id,
            "level": level,
            "bench_workspace": str(bench_workspace),
            "registration_mode": "factory_bench_runner",
        },
        metadata={
            "registered_by": "factory_bench",
            "internal_test_only": True,
            "backend_binding": "shared_backend_workspace_switch",
        },
    )
    try:
        InstanceRegistry().save(record)
    except (OSError, RuntimeError, ValueError):
        return


def _default_launcher_instance_mode() -> str:
    raw = str(os.environ.get("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE") or "isolated").strip().lower()
    return raw if raw in _LAUNCHER_INSTANCE_MODES else "isolated"


def _default_bench_session_reporting_mode() -> str:
    raw = str(os.environ.get("FACTORY_BENCH_SESSION_REPORTING") or "auto").strip().lower()
    return raw if raw in _BENCH_SESSION_REPORTING_MODES else "auto"


def _bench_session_backend_url(
    *,
    launcher_instance_mode: str,
    bench_session_reporting: str,
    backend_url: str,
) -> str:
    reporting = str(bench_session_reporting or "auto").strip().lower()
    launcher_mode = str(launcher_instance_mode or "isolated").strip().lower()
    if reporting == "off":
        return ""
    if reporting == "shared":
        return str(backend_url or "").rstrip("/")
    if launcher_mode == "observed":
        return str(backend_url or "").rstrip("/")
    return ""


def _bench_project_instance_id(
    *,
    bench_session_id: str,
    project_id: str,
    bench_workspace: Path | str | None = None,
    run_id: str = "",
    launch_nonce: str = "",
) -> str:
    if bench_session_id:
        raw = f"{bench_session_id}-{project_id}"
    else:
        workspace_name = Path(str(bench_workspace or "")).name
        if workspace_name.startswith("factory-bench-"):
            raw = f"{workspace_name}-{project_id}"
        else:
            raw = f"factory-bench-{workspace_name}-{project_id}" if workspace_name else f"factory-bench-{project_id}"
    if launch_nonce:
        run_token = re.sub(r"[^A-Za-z0-9]+", "-", str(run_id or "local").lower()).strip("-")[-24:] or "local"
        nonce_token = re.sub(r"[^A-Za-z0-9]+", "-", str(launch_nonce).lower()).strip("-")[-20:]
        suffix = f"-run-{run_token}-{nonce_token}"
        raw = f"{raw[: max(1, 80 - len(suffix))]}{suffix}"
    try:
        from polaris.cells.instances.internal.service import sanitize_instance_id
    except (ImportError, RuntimeError):
        return re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-").lower()[:80] or "factory-bench-project"
    return sanitize_instance_id(raw)


def _new_isolated_bench_launch_receipt(
    *,
    bench_session_id: str,
    run_id: str,
    project_id: str,
    requested_project_id: str,
    canonical_project_id: str,
    bench_workspace: Path,
    project_workspace: str,
    workspace_catalog_meta: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the immutable identity claim for one isolated bench launch."""
    nonce = secrets.token_hex(8)
    requested_instance_id = _bench_project_instance_id(
        bench_session_id=bench_session_id,
        project_id=project_id,
        bench_workspace=bench_workspace,
    )
    instance_id = _bench_project_instance_id(
        bench_session_id=bench_session_id,
        project_id=project_id,
        bench_workspace=bench_workspace,
        run_id=run_id,
        launch_nonce=nonce,
    )
    workspace_path = Path(project_workspace).expanduser().absolute()
    persisted_catalog = _require_workspace_catalog_meta(bench_workspace, workspace_path, workspace_catalog_meta)
    workspace = str(workspace_path)
    workspace_device = int(persisted_catalog["workspace_device"])
    workspace_inode = int(persisted_catalog["workspace_inode"])
    catalog_receipt_hash = _workspace_catalog_hash(persisted_catalog)
    workspace_source_run_id = str(persisted_catalog.get("run_id") or "").strip()
    if not workspace_source_run_id:
        raise RuntimeError("Bench workspace catalog source run id is missing")
    return {
        "schema_version": "factory_bench.isolated_launch_receipt.v1",
        "launch_nonce": nonce,
        "launch_scope": f"{run_id}:{project_id}:{nonce}",
        "run_id": run_id,
        "workspace_source_run_id": workspace_source_run_id,
        "bench_session_id": bench_session_id,
        "project_id": project_id,
        "requested_project_id": requested_project_id,
        "canonical_project_id": canonical_project_id,
        "requested_instance_id": requested_instance_id,
        "instance_id": instance_id,
        "bench_workspace": str(bench_workspace.expanduser().absolute()),
        "workspace": workspace,
        "workspace_device": workspace_device,
        "workspace_inode": workspace_inode,
        "workspace_catalog_hash": catalog_receipt_hash,
        "runtime_root": str((workspace_path / "runtime").absolute()),
        "expected_backend_root": str(_BACKEND_ROOT),
        "expected_source_fingerprint": compute_source_fingerprint(_BACKEND_ROOT),
        "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _validate_isolated_bench_launch(
    *,
    instance: Mapping[str, Any],
    receipt: Mapping[str, Any],
    backend_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless the launched backend proves this runner's identity claim."""
    errors: list[str] = []
    metadata_raw = instance.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
    persisted_raw = metadata.get("instance_launch_receipt")
    persisted = persisted_raw if isinstance(persisted_raw, Mapping) else {}
    expected_instance_id = str(receipt.get("instance_id") or "")
    expected_bench_workspace = str(receipt.get("bench_workspace") or "")
    expected_workspace = str(receipt.get("workspace") or "")
    expected_workspace_device = receipt.get("workspace_device")
    expected_workspace_inode = receipt.get("workspace_inode")
    expected_catalog_hash = str(receipt.get("workspace_catalog_hash") or "")
    expected_runtime_root = str(receipt.get("runtime_root") or "")
    expected_backend_root = str(receipt.get("expected_backend_root") or "")
    expected_fingerprint = str(receipt.get("expected_source_fingerprint") or "")
    if not expected_instance_id or str(instance.get("instance_id") or "") != expected_instance_id:
        errors.append("instance_id_mismatch")
    if str(instance.get("workspace") or "") != expected_workspace:
        errors.append("workspace_mismatch")
    required_text_fields = (
        "schema_version",
        "launch_scope",
        "launch_nonce",
        "run_id",
        "workspace_source_run_id",
        "project_id",
        "requested_project_id",
        "canonical_project_id",
        "requested_instance_id",
        "instance_id",
        "bench_workspace",
        "workspace",
        "workspace_catalog_hash",
        "runtime_root",
        "expected_backend_root",
        "expected_source_fingerprint",
    )
    for field in required_text_fields:
        if not str(receipt.get(field) or "").strip():
            errors.append(f"launch_receipt_{field}_missing")
    if not isinstance(expected_workspace_device, int) or isinstance(expected_workspace_device, bool):
        errors.append("launch_receipt_workspace_device_missing")
    if not isinstance(expected_workspace_inode, int) or isinstance(expected_workspace_inode, bool):
        errors.append("launch_receipt_workspace_inode_missing")
    if re.fullmatch(r"[0-9a-f]{64}", expected_catalog_hash) is None:
        errors.append("launch_receipt_workspace_catalog_hash_invalid")
    try:
        catalog_payload, current_workspace_identity, current_catalog_hash = _read_workspace_catalog_meta_bound(
            Path(expected_bench_workspace),
            Path(expected_workspace),
        )
    except (OSError, RuntimeError, ValueError):
        catalog_payload = {}
        current_workspace_identity = {}
        current_catalog_hash = ""
        errors.append("workspace_catalog_unavailable")
    if current_workspace_identity.get("device") != expected_workspace_device:
        errors.append("workspace_device_mismatch")
    if current_workspace_identity.get("inode") != expected_workspace_inode:
        errors.append("workspace_inode_mismatch")
    if current_catalog_hash != expected_catalog_hash:
        errors.append("workspace_catalog_hash_mismatch")
    if catalog_payload.get("run_id") != receipt.get("workspace_source_run_id"):
        errors.append("workspace_catalog_run_id_mismatch")
    if catalog_payload.get("project_id") != receipt.get("project_id"):
        errors.append("workspace_catalog_project_id_mismatch")
    if catalog_payload.get("workspace_nonce") != Path(expected_workspace).name:
        errors.append("workspace_catalog_nonce_mismatch")
    if catalog_payload.get("workspace_device") != expected_workspace_device:
        errors.append("workspace_catalog_device_mismatch")
    if catalog_payload.get("workspace_inode") != expected_workspace_inode:
        errors.append("workspace_catalog_inode_mismatch")
    if str(instance.get("runtime_root") or "") != expected_runtime_root:
        errors.append("runtime_root_mismatch")
    for field in (
        "schema_version",
        "launch_scope",
        "launch_nonce",
        "run_id",
        "workspace_source_run_id",
        "project_id",
        "requested_project_id",
        "canonical_project_id",
        "requested_instance_id",
        "instance_id",
        "bench_workspace",
        "workspace",
        "workspace_device",
        "workspace_inode",
        "workspace_catalog_hash",
        "runtime_root",
        "expected_backend_root",
        "expected_source_fingerprint",
    ):
        if persisted.get(field) != receipt.get(field):
            errors.append(f"launch_receipt_{field}_mismatch")

    freshness_raw = backend_context.get("backend_freshness")
    freshness = freshness_raw if isinstance(freshness_raw, Mapping) else {}
    backend_info_raw = freshness.get("backend_info")
    backend_info = backend_info_raw if isinstance(backend_info_raw, Mapping) else {}
    if not bool(freshness.get("ok")):
        errors.append("backend_fingerprint_not_fresh")
    if not expected_fingerprint or str(freshness.get("expected_fingerprint") or "") != expected_fingerprint:
        errors.append("expected_source_fingerprint_mismatch")
    if str(freshness.get("actual_fingerprint") or "") != expected_fingerprint:
        errors.append("actual_source_fingerprint_mismatch")
    if str(backend_info.get("workspace") or "") != expected_workspace:
        errors.append("backend_workspace_mismatch")
    instance_backend_pid = instance.get("backend_pid")
    if not isinstance(instance_backend_pid, int) or isinstance(instance_backend_pid, bool) or instance_backend_pid <= 0:
        errors.append("instance_backend_pid_missing")
    elif backend_info.get("pid") != instance_backend_pid:
        errors.append("backend_pid_mismatch")
    if str(backend_info.get("instance_id") or "") != expected_instance_id:
        errors.append("backend_instance_id_mismatch")
    try:
        receipt_backend_path = Path(expected_backend_root)
        observed_backend_path = Path(str(backend_info.get("backend_root") or ""))
        backend_root_matches = (
            receipt_backend_path.is_absolute()
            and observed_backend_path.is_absolute()
            and receipt_backend_path.resolve() == _BACKEND_ROOT
            and observed_backend_path.resolve() == _BACKEND_ROOT
        )
    except (OSError, RuntimeError, ValueError):
        backend_root_matches = False
    if not backend_root_matches:
        errors.append("backend_root_mismatch")
    return {
        "ok": not errors,
        "error": "measurement_contaminated" if errors else "",
        "reasons": errors,
        "launch_scope": str(receipt.get("launch_scope") or ""),
        "requested_instance_id": str(receipt.get("requested_instance_id") or ""),
        "instance_id": expected_instance_id,
        "run_id": str(receipt.get("run_id") or ""),
        "backend_pid": instance_backend_pid if isinstance(instance_backend_pid, int) else None,
        "backend_root": expected_backend_root,
        "expected_source_fingerprint": expected_fingerprint,
        "actual_source_fingerprint": str(freshness.get("actual_fingerprint") or ""),
    }


def _wait_backend_health(backend_url: str, token: str, *, timeout_s: float = 45.0) -> bool:
    deadline = time.time() + max(1.0, float(timeout_s))
    health_url = f"{str(backend_url or '').rstrip('/')}/health"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    while time.time() < deadline:
        try:
            request = urllib.request.Request(health_url, headers=headers)
            with urllib.request.urlopen(request, timeout=2.0) as response:
                if 200 <= int(response.status) < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
    return False


def _start_isolated_bench_project_instance(
    *,
    bench_session_id: str,
    project_id: str,
    project_title: str,
    level: int,
    bench_workspace: Path,
    project_workspace: str,
    backend_token: str,
    launch_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Start a project-scoped Polaris instance for internal factory_bench runs."""
    try:
        from polaris.cells.instances.internal.service import (
            InstanceRegistryError,
            InstanceSupervisor,
            default_polaris_root,
        )
    except (ImportError, RuntimeError) as exc:
        return {
            "ok": False,
            "error": "instance_supervisor_unavailable",
            "error_type": type(exc).__name__,
            "error_detail": str(exc),
        }

    if launch_receipt is None:
        return {
            "ok": False,
            "error": "isolated_launch_receipt_required",
            "error_type": "MissingLaunchReceiptError",
            "error_detail": "isolated Bench launch requires an inode-bound catalog receipt",
        }
    receipt = dict(launch_receipt)
    try:
        receipt_backend_root = resolve_backend_source_root(str(receipt.get("expected_backend_root") or ""))
        if receipt_backend_root != _BACKEND_ROOT:
            raise RuntimeError(
                f"isolated launch receipt source root mismatch: runner={_BACKEND_ROOT} receipt={receipt_backend_root}"
            )
        polaris_root = default_polaris_root()
        supervisor_backend_root = resolve_backend_source_root(polaris_root / "src" / "backend")
        if supervisor_backend_root != _BACKEND_ROOT:
            raise RuntimeError(
                f"instance supervisor source root mismatch: runner={_BACKEND_ROOT} supervisor={supervisor_backend_root}"
            )
        supervisor = InstanceSupervisor()
        stopped_predecessors: list[str] = []
        source_run_id = str(receipt.get("workspace_source_run_id") or "").strip()
        attempt_run_id = str(receipt.get("run_id") or "").strip()
        if source_run_id and source_run_id != attempt_run_id:
            # A resume intentionally reuses one inode-bound workspace. Stop
            # only an older internal Bench instance proven to own the same
            # workspace, Bench root, project, and source run. Never touch main
            # or another Agent's unrelated project instance.
            for prior in supervisor.list_instances():
                if not isinstance(prior, Mapping):
                    continue
                prior_id = str(prior.get("instance_id") or "").strip()
                if not prior_id or prior_id == str(receipt.get("instance_id") or ""):
                    continue
                if str(prior.get("kind") or "") != "bench_project":
                    continue
                if str(prior.get("workspace") or "") != str(receipt.get("workspace") or ""):
                    continue
                if str(prior.get("status") or "").strip().lower() in {"stopped", "failed"}:
                    continue
                prior_metadata = prior.get("metadata")
                if not isinstance(prior_metadata, Mapping) or not bool(prior_metadata.get("internal_test_only")):
                    continue
                prior_receipt = prior_metadata.get("instance_launch_receipt")
                if not isinstance(prior_receipt, Mapping):
                    continue
                prior_source_run_id = str(
                    prior_receipt.get("workspace_source_run_id") or prior_receipt.get("run_id") or ""
                ).strip()
                if prior_source_run_id != source_run_id:
                    continue
                if str(prior_receipt.get("bench_workspace") or "") != str(receipt.get("bench_workspace") or ""):
                    continue
                if str(prior_receipt.get("project_id") or "") != str(receipt.get("project_id") or ""):
                    continue
                supervisor.stop_instance(prior_id)
                stopped_predecessors.append(prior_id)
        if stopped_predecessors:
            receipt["resume_predecessor_instance_ids"] = stopped_predecessors
        instance = supervisor.start_instance(
            {
                "instance_id": str(receipt["instance_id"]),
                "name": f"{project_id} {project_title}".strip(),
                "kind": "bench_project",
                "polaris_root": str(polaris_root),
                "workspace": str(receipt["workspace"]),
                "runtime_root": str(receipt["runtime_root"]),
                "backend_port": None,
                "frontend_port": None,
                "backend_reload": False,
                "frontend_vite": True,
                "start_frontend": True,
                "require_fresh_instance": True,
                "bench": {
                    "session_id": bench_session_id,
                    "project_id": project_id,
                    "level": level,
                    "bench_workspace": str(bench_workspace),
                    "registration_mode": "factory_bench_runner",
                    "run_id": str(receipt["run_id"]),
                    "launch_scope": str(receipt["launch_scope"]),
                },
                "metadata": {
                    "registered_by": "factory_bench",
                    "internal_test_only": True,
                    "backend_binding": "isolated_backend_instance",
                    "launcher_instance_mode": "isolated",
                    "instance_launch_receipt": receipt,
                },
            }
        )
    except InstanceRegistryError as exc:
        _logger.error("factory bench isolated instance registry unavailable: %s", exc)
        return {
            "ok": False,
            "error": "instance_registry_unavailable",
            "failure_class": "platform_failure",
            "error_code": exc.code,
            "error_type": type(exc).__name__,
            "error_detail": str(exc),
            "platform_error": exc.to_dict(),
        }
    except (OSError, RuntimeError, ValueError) as exc:
        _logger.debug("factory bench isolated instance start failed", exc_info=True)
        return {
            "ok": False,
            "error": "isolated_instance_start_failed",
            "error_type": type(exc).__name__,
            "error_detail": str(exc),
        }
    instance_token = str(instance.get("token") or "")
    if not instance_token:
        return {
            "ok": False,
            "error": "isolated_instance_token_missing",
            "error_type": "MissingInstanceTokenError",
            "error_detail": "Instance Supervisor did not return a per-instance token",
        }
    if not _wait_backend_health(str(instance.get("backend_url") or ""), instance_token):
        metadata = instance.get("metadata")
        if isinstance(metadata, dict):
            metadata["backend_health"] = "starting"
    instance["launch_receipt"] = receipt
    instance["ok"] = True
    return instance


def _runtime_project_contamination(project_workspace: str) -> list[str]:
    """Return foreign workspace keys found under a bench project's local runtime base."""

    try:
        from polaris.kernelone.storage import workspace_key
    except (ImportError, RuntimeError):
        return []
    workspace_path = Path(project_workspace).expanduser().resolve()
    projects_root = workspace_path / "runtime" / ".polaris" / "projects"
    if not projects_root.is_dir():
        return []
    current_key = workspace_key(str(workspace_path))
    foreign_keys: list[str] = []
    try:
        children = sorted(projects_root.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    for child in children:
        if child.is_dir() and child.name != current_key:
            foreign_keys.append(child.name)
    return foreign_keys
