"""Argparse CLI entry (main) for factory-bench runner.

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


from scripts.factory_bench._bench_lib import chain as _chain

_pull_namespace(_chain)
del _chain


def main() -> int:
    ap = argparse.ArgumentParser(description="Polaris factory-bench full-chain runner")
    ap.add_argument("--project-ids", default="", help="comma-separated ids (e.g. L1-01,L1-02); empty = use --levels")
    ap.add_argument(
        "--levels",
        default="1,2,3,4,5,6,7,8,9,10,11,12",
        help="comma-separated levels to run when no ids given",
    )
    ap.add_argument(
        "--projects-file",
        default=str(_FIXTURE),
        help="factory-bench project catalog JSON; defaults to standalone creative projects_v2.json",
    )
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/factory-bench"))
    ap.add_argument("--timeout", type=int, default=5400, help="per-project chain timeout seconds")
    ap.add_argument(
        "--max-failed",
        type=int,
        default=0,
        help="early stop after N audit failures; 0 disables early stop",
    )
    ap.add_argument(
        "--director-workflow-execution-mode",
        choices=("serial", "parallel"),
        default="parallel",
        help="Director execution mode for the HTTP Factory PM→Chief Engineer→Director chain",
    )
    ap.add_argument(
        "--director-dispatch-driver",
        choices=("task-market",),
        default="task-market",
        help="Director dispatch path; only task-market mainline-full is supported",
    )
    ap.add_argument(
        "--start-from",
        choices=("pm", "director_resume"),
        default="pm",
        help="Factory bench stage to start from; director_resume reuses trusted PM/CE evidence and pre-Director snapshot",
    )
    ap.add_argument(
        "--use-legacy-chain",
        action="store_true",
        help="Retired; Factory Bench refuses legacy two-role subprocess runs",
    )
    ap.add_argument(
        "--real-run-timeout",
        type=int,
        default=60,
        help="seconds for each generated project's dependency/build/entrypoint real-run gate",
    )
    ap.add_argument(
        "--launcher-instance-mode",
        choices=tuple(sorted(_LAUNCHER_INSTANCE_MODES)),
        default=_default_launcher_instance_mode(),
        help=(
            "Launcher registration mode: isolated starts a project-scoped Polaris backend/frontend and runs the "
            "chain against it; observed registers shared-backend bench activity for explicit compatibility only"
        ),
    )
    ap.add_argument(
        "--bench-session-reporting",
        choices=tuple(sorted(_BENCH_SESSION_REPORTING_MODES)),
        default=_default_bench_session_reporting_mode(),
        help=(
            "Internal bench session reporting mode: auto reports only for observed shared-backend runs; "
            "shared also reports isolated runs to the main backend; off disables shared session POSTs"
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="validate projects and generate audit structure without running the chain",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="limit number of projects to process; 0 disables limit",
    )
    args = ap.parse_args()
    if args.use_legacy_chain:
        print(
            "[factory-bench] --use-legacy-chain is retired; use the HTTP Factory "
            "PM→Chief Engineer→Director task-market chain",
            flush=True,
        )
        return 2

    projects = load_projects() if args.projects_file == str(_FIXTURE) else load_projects(args.projects_file)
    if args.project_ids.strip():
        wanted_ids = [s.strip() for s in args.project_ids.split(",") if s.strip()]
        selected, missing_ids, alias_to_canonical = _resolve_explicit_project_selection(projects, wanted_ids)
        if missing_ids:
            print(
                "[factory-bench] unknown project id(s): "
                + ", ".join(missing_ids)
                + "; refusing to run partial explicit selection",
                flush=True,
            )
            return 1
        if alias_to_canonical:
            alias_summary = ", ".join(
                f"{alias}->{canonical}" for alias, canonical in sorted(alias_to_canonical.items())
            )
            print(f"[factory-bench] resolved level-local project id(s): {alias_summary}", flush=True)
    else:
        wanted_levels = {int(s) for s in args.levels.split(",") if s.strip()}
        selected = [p for p in projects if int(p["level"]) in wanted_levels]
    if not selected:
        print("[factory-bench] nothing selected", flush=True)
        return 1

    # Apply --limit if specified
    if args.limit > 0:
        selected = selected[: args.limit]
        print(f"[factory-bench] limiting to {len(selected)} project(s) (--limit={args.limit})", flush=True)

    # Handle --dry-run: validate and generate audit structure without running chain
    if args.dry_run:
        print(f"[factory-bench] dry-run mode: validating {len(selected)} project(s)", flush=True)
        try:
            base = _resolve_bench_work_dir(args.work_dir)
        except ValueError as exc:
            print(f"[factory-bench] invalid --work-dir: {exc}", flush=True)
            return 2
        base.mkdir(parents=True, exist_ok=True)
        audit_dir = base / "audits" / "dry-run"
        audit_dir.mkdir(parents=True, exist_ok=True)

        catalog_hash = hashlib.sha256(
            json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        for project in selected:
            pid = str(project.get("id") or "")
            level = int(project.get("level") or 0)
            lang = str(project.get("primary_language") or "")

            audit_file = audit_dir / f"{pid}.audit.json"
            project_audit = {
                "catalog_schema_version": "factory-bench/2",
                "catalog_hash": catalog_hash,
                "run_id": "dry-run",
                "project_id": pid,
                "level": level,
                "primary_language": lang,
                "title": str(project.get("title") or ""),
                "domain": str(project.get("domain") or ""),
                "project_type": str(project.get("project_type") or ""),
                "record": {
                    "project_id": pid,
                    "level": level,
                    "primary_language": lang,
                    "dry_run": True,
                    "validation_passed": True,
                },
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _write_immutable_json(audit_file, project_audit)
            print(f"[factory-bench]   {pid} L{level} {lang}: audit package generated", flush=True)

        print(f"[factory-bench] dry-run complete: {len(selected)} audit package(s) -> {audit_dir}", flush=True)
        return 0

    try:
        base = _resolve_bench_work_dir(args.work_dir)
    except ValueError as exc:
        print(f"[factory-bench] invalid --work-dir: {exc}", flush=True)
        return 2
    base.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    run_errors: list[str] = []
    failed = 0
    expected_llm_bindings = resolve_expected_llm_bindings()
    # This run id is part of every isolated launch identity. It must be chosen
    # before any registry interaction, not only when audit files are written.
    run_id = _sanitize_run_id(os.environ.get("FACTORY_BENCH_RUN_ID"))
    bench_session_id = os.environ.get("FACTORY_BENCH_SESSION_ID") or ""
    launcher_instance_mode = str(args.launcher_instance_mode or "isolated").strip().lower()
    bench_session_reporting = str(args.bench_session_reporting or "auto").strip().lower()
    backend_url = _resolve_backend_url()
    backend_token = _resolve_backend_token()
    bench_session_backend_url = _bench_session_backend_url(
        launcher_instance_mode=launcher_instance_mode,
        bench_session_reporting=bench_session_reporting,
        backend_url=backend_url,
    )
    bench_session_id = _ensure_bench_session(
        backend_url=bench_session_backend_url,
        work_dir=str(base),
        project_ids=[str(p["id"]) for p in selected],
        total=len(selected),
        metadata={
            "run_id": run_id,
            "levels": sorted({int(p.get("level") or 0) for p in selected}),
            "launcher_instance_mode": launcher_instance_mode,
            "bench_session_reporting": bench_session_reporting,
        },
        requested_session_id=bench_session_id,
        token=backend_token,
    )
    configure_bench_backend(bench_session_backend_url, bench_session_id, backend_token)
    backend_audit_context = build_bench_backend_audit_context(
        bench_session_backend_url,
        backend_token=backend_token,
        workspace=str(base),
    )
    _emit_bench_event(
        workspace=base,
        project_id="-",
        level=0,
        name="run.started",
        summary=f"factory-bench session {bench_session_id or 'local'}: {len(selected)} project(s)",
        meta={
            "session_id": bench_session_id,
            "total": len(selected),
            "launcher_instance_mode": launcher_instance_mode,
            "bench_session_reporting": bench_session_reporting,
            "shared_session_backend_url": bool(bench_session_backend_url),
        },
    )
    use_legacy_chain = False

    # Compute catalog hash for immutable audit trail
    catalog_hash = hashlib.sha256(json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[
        :16
    ]
    catalog_schema_version = "factory-bench/2"

    audit_dir = base / "audits" / run_id
    audit_dir.mkdir(parents=True, exist_ok=True)

    for project in selected:
        pid = project["id"]
        canonical_pid = str(project.get("canonical_catalog_project_id") or pid)
        requested_pid = str(project.get("requested_project_id") or pid)
        resume_director = str(args.start_from or "pm").strip().lower() == "director_resume"
        # Resolve the physical workspace identity before isolated backend
        # startup. Fresh attempts always get a new path; resume is the sole
        # explicit reuse path. Never delete an enrolled runtime root here.
        workspace = _project_workspace_for_run(
            base,
            project_id=str(pid),
            run_id=run_id,
            resume_director=resume_director,
        )
        log_path = base / f"{pid}.chain.log"
        # Bind catalog identity before launch. Fresh metadata is exclusive;
        # resume validates the existing immutable record and never overwrites.
        catalog_identity = {
            "catalog_schema_version": catalog_schema_version,
            "catalog_hash": catalog_hash,
            "run_id": run_id,
            "project_id": pid,
            "requested_project_id": requested_pid,
            "canonical_catalog_project_id": canonical_pid,
        }
        if resume_director:
            # Resume binds a new Bench attempt to the immutable workspace from
            # the original Factory run. The attempt id must not be confused
            # with the workspace's source run id.
            resume_identity = {key: value for key, value in catalog_identity.items() if key != "run_id"}
            catalog_meta = _require_workspace_catalog_meta(base, workspace, resume_identity)
        else:
            catalog_meta = _write_workspace_catalog_meta_exclusive(
                base,
                workspace,
                {
                    **catalog_identity,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
        print(f"[factory-bench] === {pid} {project['title']} ===", flush=True)
        project_level = int(project.get("level") or 0)
        project_title = str(project.get("title") or "")
        project_workspace = str(workspace.absolute())
        project_backend_url = backend_url
        project_backend_token = backend_token
        project_backend_audit_context = backend_audit_context
        launcher_instance_meta: dict[str, Any] = {"mode": launcher_instance_mode}
        if launcher_instance_mode == "isolated":
            launch_receipt = _new_isolated_bench_launch_receipt(
                bench_session_id=bench_session_id,
                run_id=run_id,
                project_id=str(pid),
                requested_project_id=requested_pid,
                canonical_project_id=canonical_pid,
                bench_workspace=base,
                project_workspace=project_workspace,
                workspace_catalog_meta=catalog_meta,
            )
            # Preserve the requested identity in the report if the supervisor
            # rejects or cannot start it; this never mutates an older record.
            launcher_instance_meta.update(
                {
                    "requested_instance_id": launch_receipt["requested_instance_id"],
                    "instance_id": launch_receipt["instance_id"],
                    "launch_receipt": launch_receipt,
                }
            )
            isolated_instance = _start_isolated_bench_project_instance(
                bench_session_id=bench_session_id,
                project_id=str(pid),
                project_title=project_title,
                level=project_level,
                bench_workspace=base,
                project_workspace=project_workspace,
                backend_token=backend_token,
                launch_receipt=launch_receipt,
            )
            if isolated_instance and bool(isolated_instance.get("ok", True)):
                project_backend_url = str(isolated_instance.get("backend_url") or backend_url).rstrip("/")
                project_backend_token = str(isolated_instance.get("token") or backend_token)
                project_backend_audit_context = build_bench_backend_audit_context(
                    project_backend_url,
                    backend_token=project_backend_token,
                    workspace=project_workspace,
                )
                launch_validation = _validate_isolated_bench_launch(
                    instance=isolated_instance,
                    receipt=launch_receipt,
                    backend_context=project_backend_audit_context,
                )
                launcher_instance_meta.update(
                    {
                        "ok": bool(launch_validation["ok"]),
                        "requested_instance_id": launch_receipt["requested_instance_id"],
                        "instance_id": isolated_instance.get("instance_id"),
                        "backend_url": isolated_instance.get("backend_url"),
                        "frontend_url": isolated_instance.get("frontend_url"),
                        "launch_receipt": launch_receipt,
                        "launch_validation": launch_validation,
                    }
                )
                workspace_switch_ok = bool(launch_validation["ok"])
            else:
                workspace_switch_ok = False
                launcher_instance_meta.update(
                    {
                        "ok": False,
                        "error": str((isolated_instance or {}).get("error") or "isolated_instance_start_failed"),
                        "error_type": str((isolated_instance or {}).get("error_type") or ""),
                        "error_detail": str((isolated_instance or {}).get("error_detail") or ""),
                        "failure_class": str((isolated_instance or {}).get("failure_class") or ""),
                        "error_code": str((isolated_instance or {}).get("error_code") or ""),
                        "platform_error": (isolated_instance or {}).get("platform_error"),
                    }
                )
        else:
            workspace_switch_ok = _push_bench_workspace_to_backend(
                backend_url=backend_url,
                workspace=project_workspace,
                token=backend_token,
            )
            _register_bench_project_instance(
                bench_session_id=bench_session_id,
                project_id=str(pid),
                project_title=project_title,
                level=project_level,
                bench_workspace=base,
                project_workspace=project_workspace,
                backend_url=backend_url,
                backend_token=backend_token,
            )
            launcher_instance_meta.update({"ok": True, "backend_binding": "shared_backend_workspace_switch"})
        runtime_foreign_keys = _runtime_project_contamination(project_workspace)
        if runtime_foreign_keys:
            workspace_switch_ok = False
            launcher_instance_meta["runtime_contamination"] = {
                "foreign_workspace_keys": runtime_foreign_keys[:12],
                "foreign_workspace_key_count": len(runtime_foreign_keys),
            }
        _emit_bench_event(
            workspace=base,
            project_id=pid,
            level=project_level,
            name="project.started",
            summary=f"{pid} {project_title} starting",
            meta={
                "session_id": bench_session_id,
                "title": project_title,
                "workspace": project_workspace,
                "workspace_path": project_workspace,
                "project_workspace": project_workspace,
                "launcher_instance": launcher_instance_meta,
                "workspace_switch": {
                    "attempted": bool(project_backend_url) and launcher_instance_mode != "isolated",
                    "ok": bool(workspace_switch_ok),
                    "endpoint": "/settings" if launcher_instance_mode != "isolated" else "instance_supervisor",
                    "runtime_contamination": launcher_instance_meta.get("runtime_contamination"),
                },
            },
        )
        last_stage_event_key = ""

        def _on_factory_stage_change(
            stage_status: str,
            status_payload: dict[str, Any],
            *,
            _project_id: str = pid,
            _project_level: int = project_level,
            _project_title: str = project_title,
            _project_workspace: Path = workspace,
        ) -> None:
            nonlocal last_stage_event_key
            phase = str(status_payload.get("phase") or "").strip()
            run_status = str(status_payload.get("status") or stage_status or "").strip()
            run_ref = str(status_payload.get("run_id") or "").strip()
            event_payload_raw = status_payload.get("event_payload")
            event_payload: dict[str, Any] = event_payload_raw if isinstance(event_payload_raw, dict) else {}
            factory_event_type = str(event_payload.get("type") or status_payload.get("event_type") or "").strip()
            if factory_event_type == "task_runtime_execution":
                event_key = ":".join(
                    [
                        run_ref,
                        factory_event_type,
                        str(event_payload.get("session_id") or ""),
                        str(event_payload.get("task_id") or ""),
                        str(event_payload.get("event_type") or ""),
                        str(event_payload.get("timestamp") or ""),
                    ]
                )
                if event_key == last_stage_event_key:
                    return
                last_stage_event_key = event_key
                _emit_factory_task_runtime_event(
                    bench_workspace=base,
                    project_workspace=_project_workspace,
                    project_id=_project_id,
                    level=_project_level,
                    title=_project_title,
                    phase_payload=status_payload,
                    event_payload=event_payload,
                )
                return
            event_key = f"{run_ref}:{run_status}:{phase}"
            if not event_key.strip(":") or event_key == last_stage_event_key:
                return
            last_stage_event_key = event_key
            _emit_factory_phase_event(
                bench_workspace=base,
                project_workspace=_project_workspace,
                project_id=_project_id,
                level=_project_level,
                title=_project_title,
                status=stage_status,
                phase_payload=status_payload,
            )

        if project_backend_url and not workspace_switch_ok:
            error = (
                "runtime_project_contamination"
                if runtime_foreign_keys
                else (
                    "measurement_contaminated"
                    if launcher_instance_mode == "isolated"
                    and isinstance(launcher_instance_meta.get("launch_validation"), Mapping)
                    and not bool(launcher_instance_meta["launch_validation"].get("ok"))
                    else "isolated_instance_start_failed"
                    if launcher_instance_mode == "isolated"
                    else "workspace_switch_failed"
                )
            )
            run_errors.append(error)
            chain = {
                "exit_code": -1,
                "duration_s": 0.0,
                "error": error,
                "failure_category": "runtime_environment",
                "root_cause_signature": f"runtime_environment:{error}",
                "launcher_instance": launcher_instance_meta,
                "workspace_switch": {
                    "attempted": launcher_instance_mode != "isolated",
                    "ok": False,
                    "endpoint": "/settings" if launcher_instance_mode != "isolated" else "instance_supervisor",
                    "workspace": project_workspace,
                    "runtime_contamination": launcher_instance_meta.get("runtime_contamination"),
                },
            }
            _emit_bench_event(
                workspace=base,
                project_id=pid,
                level=project_level,
                name="project.failed",
                summary=f"{pid} workspace switch failed before observation",
                meta={
                    "session_id": bench_session_id,
                    "error": error,
                    "failure_category": "runtime_environment",
                    "root_cause_signature": f"runtime_environment:{error}",
                    "workspace": project_workspace,
                    "workspace_path": project_workspace,
                    "project_workspace": project_workspace,
                    "launcher_instance": launcher_instance_meta,
                    "workspace_switch": chain["workspace_switch"],
                },
            )
        else:
            try:
                if use_legacy_chain:
                    chain = run_chain(
                        project,
                        workspace,
                        timeout_s=args.timeout,
                        log_path=log_path,
                        director_workflow_execution_mode=args.director_workflow_execution_mode,
                        director_dispatch_driver=args.director_dispatch_driver,
                    )
                else:
                    chain = run_factory_chain(
                        project,
                        workspace,
                        backend_url=project_backend_url,
                        backend_token=project_backend_token,
                        timeout_s=args.timeout,
                        log_path=log_path,
                        director_workflow_execution_mode=args.director_workflow_execution_mode,
                        director_dispatch_driver=args.director_dispatch_driver,
                        bench_session_id=bench_session_id,
                        start_from=args.start_from,
                        on_stage_change=_on_factory_stage_change,
                    )
            except subprocess.TimeoutExpired:
                chain = {"exit_code": -1, "duration_s": float(args.timeout), "timeout": True}
            except KeyboardInterrupt as exc:
                reason = "interrupted"
                interrupted_counts = _bench_record_counts(records, total=len(selected))
                _emit_bench_event(
                    workspace=base,
                    project_id="-",
                    level=0,
                    name="run.cancelled",
                    summary=f"factory-bench cancelled: {reason}",
                    meta={
                        "session_id": bench_session_id,
                        **interrupted_counts,
                        "error": reason,
                    },
                )
                if bench_session_backend_url and bench_session_id:
                    _push_bench_complete_to_backend(
                        backend_url=bench_session_backend_url,
                        session_id=bench_session_id,
                        success=False,
                        summary={
                            **interrupted_counts,
                            "error": reason,
                            "exception": type(exc).__name__,
                        },
                        token=backend_token,
                    )
                return 130
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                error = str(exc) or type(exc).__name__
                run_errors.append(error)
                chain = {
                    "exit_code": -1,
                    "duration_s": 0.0,
                    "error": error,
                    "exception": type(exc).__name__,
                    "_runner_exception": True,
                }
        _emit_bench_event(
            workspace=base,
            project_id=pid,
            level=int(project.get("level") or 0),
            name="project.completed",
            summary=f"{pid} exit={chain.get('exit_code')} dur={chain.get('duration_s')}s",
            meta={
                "session_id": bench_session_id,
                "exit_code": chain.get("exit_code"),
                "duration_s": chain.get("duration_s"),
                "timeout": bool(chain.get("timeout")),
            },
        )
        runtime_dirs = resolve_runtime_dirs_for_workspace(workspace)
        runtime_dir = runtime_dirs[0] if runtime_dirs else None
        # Determine whether the chain reached a genuine terminal state.
        # - start_failed/workspace_switch_failed: the pipeline never started.
        # - _runner_exception: the bench runner crashed before completion.
        # - event_wait_timeout: runtime.v2 did not deliver a terminal event;
        #   we send cancel, but the backend may still be mutating the
        #   workspace. Treat this as non-terminal and do not run final gates
        #   against a racing snapshot.
        # - Otherwise: wait_run_until_terminal returned a terminal status dict
        #   or a legacy subprocess reached an interrupted terminal state.
        chain_error = str(chain.get("error") or "")
        chain_is_terminal = _chain_reached_terminal(chain)
        chain_attempt_started = bool(str(chain.get("run_id") or "").strip()) or chain_error not in {
            "director_resume_run_missing",
            "isolated_instance_start_failed",
            "measurement_contaminated",
            "runtime_project_contamination",
            "start_failed",
            "workspace_switch_failed",
        }
        chain_results_raw = chain.get("chain_results")
        chain_results_for_status: dict[str, Any] = chain_results_raw if isinstance(chain_results_raw, dict) else {}
        chain_status_raw = str(chain_results_for_status.get("exit_class", ""))
        chain_phase_raw = chain_error or ("timeout" if chain.get("timeout") else "")
        record = build_factory_audit_record(
            project=project,
            workspace=str(workspace),
            artifact_globs=discover_artifacts(workspace, runtime_dirs) if chain_attempt_started else {},
            chain_terminal=chain_is_terminal,
            chain_status=chain_status_raw,
            chain_phase=chain_phase_raw,
        )
        record["runtime_dir"] = str(runtime_dir) if runtime_dir else None
        record["runtime_dirs"] = [str(path) for path in runtime_dirs]
        record["chain"] = chain
        record["launcher_instance"] = dict(launcher_instance_meta)
        if not chain_is_terminal:
            record["chain_diagnostics"] = _non_terminal_chain_diagnostics(
                chain=chain,
                backend_url=project_backend_url,
                project_workspace=project_workspace,
                launcher_instance=launcher_instance_meta,
            )
        if use_legacy_chain:
            record["chain_results"] = read_chain_results_from_runtime_dirs(runtime_dirs)
        else:
            record["chain_results"] = (
                chain.get("chain_results")
                if "chain_results" in chain
                else read_chain_results_from_runtime_dirs(runtime_dirs)
                if chain_attempt_started
                else {}
            )
        if not isinstance(record.get("chain_results"), dict):
            record["chain_results"] = {}
        contract_goal = str(record["chain_results"].get("contract_goal") or "")
        own_overlap = brief_goal_overlap(str(project.get("brief") or ""), contract_goal)
        record["goal_brief_overlap"] = round(own_overlap, 3)
        # Language-robust contamination detection: an absolute threshold
        # false-positives when the planner answers a Chinese brief with an
        # English goal (zero char-bigram overlap, live 2026-06-12). The real
        # contamination signal is RELATIVE — the goal resembling ANOTHER
        # project's brief more than its own.
        best_other = 0.0
        best_other_id = ""
        for other in projects:
            if other["id"] == project["id"]:
                continue
            score = brief_goal_overlap(str(other.get("brief") or ""), contract_goal)
            if score > best_other:
                best_other, best_other_id = score, str(other["id"])
        # Absolute floor besides the relative margin: an English goal vs a
        # Chinese own-brief scores 0.0, and any latin-bearing OTHER brief
        # (e.g. "Docker/Cgroups") wins the relative test on noise alone —
        # live false positive: L2-12's correct brick-breaker goal flagged as
        # ~L8-45 (container engine) at best_other≈0.1.
        record["wrong_product_suspect"] = bool(contract_goal and best_other > max(0.18, own_overlap + 0.1))
        record["wrong_product_match"] = best_other_id if record["wrong_product_suspect"] else ""
        record["chain_state"] = "pending_canonical_projection"
        raw_audit_bundle = chain.get("audit_bundle")
        audit_bundle: dict[str, Any] = raw_audit_bundle if isinstance(raw_audit_bundle, dict) else {}
        task_runtime_projection = audit_bundle.get("task_runtime_projection")
        if isinstance(task_runtime_projection, Mapping):
            record["task_runtime_projection"] = dict(task_runtime_projection)
        elif not isinstance(record.get("task_runtime_projection"), Mapping):
            record["task_runtime_projection"] = {}
        record.update(project_backend_audit_context)
        record["run_id"] = run_id
        record["project_id"] = pid
        record["requested_project_id"] = requested_pid
        record["canonical_project_id"] = canonical_pid
        record["canonical_catalog_project_id"] = canonical_pid
        record["factory_run_id"] = str(chain.get("run_id") or run_id)
        record["qa_invoked"] = (
            read_factory_qa_invocation_status(
                Path(project_workspace),
                record["factory_run_id"],
            )
            if chain_attempt_started
            else {"invoked": False, "reason": "current_attempt_not_started"}
        )
        record["chain_attempt_started"] = chain_attempt_started
        record["requested_instance_id"] = str(launcher_instance_meta.get("requested_instance_id") or "")
        record["instance_id"] = str(launcher_instance_meta.get("instance_id") or "")
        record["instance_launch_receipt"] = dict(launcher_instance_meta.get("launch_receipt") or {})
        record["instance_launch_validation"] = dict(launcher_instance_meta.get("launch_validation") or {})
        record["workspace"] = project_workspace
        record["project_workspace"] = project_workspace
        record["backend_url"] = project_backend_url
        record["backend_port"] = _url_port(project_backend_url)
        record["frontend_url"] = str(launcher_instance_meta.get("frontend_url") or "")
        record["frontend_port"] = _url_port(str(launcher_instance_meta.get("frontend_url") or ""))
        if chain_is_terminal:
            record["real_run_gate"] = build_real_run_gate(
                workspace,
                record,
                timeout_s=int(args.real_run_timeout),
            )
            record["run_ledger"] = persist_real_run_gate_ledger(
                workspace,
                record,
                record["real_run_gate"],
                run_id=run_id,
                project_id=pid,
            )
        else:
            record["real_run_gate"] = _build_non_terminal_real_run_gate(
                chain_phase=chain_phase_raw,
                chain_status=chain_status_raw,
            )
            if isinstance(record.get("chain_diagnostics"), dict):
                record["real_run_gate"]["diagnostics"] = record["chain_diagnostics"]
            record["run_ledger"] = persist_real_run_gate_ledger(
                workspace,
                record,
                record["real_run_gate"],
                run_id=run_id,
                project_id=pid,
                stage=chain_phase_raw or chain_status_raw or "chain_non_terminal",
                gate_name="chain_non_terminal",
            )
        record["run_ledger_projection"] = load_run_ledger_projection(
            workspace,
            run_id=run_id,
            factory_run_id=record["factory_run_id"],
            project_id=pid,
        )
        record["task_boundary_verdict"] = _read_task_boundary_verdict_from_run_ledger_projection(
            record["run_ledger_projection"]
        )
        required_llm_roles = required_llm_roles_for_factory_record(chain=chain, record=record)
        record["required_llm_roles"] = list(required_llm_roles)
        llm_events = collect_llm_events(workspace, runtime_dirs, audit_bundle)
        record["final_request_refs"] = project_final_request_refs(llm_events)
        record["llm_route_audit"] = build_llm_route_audit(
            llm_events,
            expected_bindings=expected_llm_bindings,
            required_roles=required_llm_roles,
            require_all_director_routes=False,
        )
        record["workspace_validation_repair_coverage"] = load_workspace_validation_repair_coverage(
            workspace,
            runtime_dirs,
        )
        record["director_repair_coverage_gap_summary"] = build_director_repair_coverage_gap_summary(
            record,
            audit_bundle,
        )
        record["canonical_projection"] = build_canonical_bench_projection(record)
        record["legacy_artifacts"] = record["canonical_projection"]["legacy_artifacts"]
        record["chain_state"] = grade_chain_state(record["canonical_projection"], chain.get("exit_code"))
        apply_factory_bench_gates(record, chain)
        apply_factory_bench_failure_taxonomy(record)
        convergence = audit_bundle.get("director_convergence")
        if isinstance(convergence, dict):
            record["director_convergence"] = convergence
        records.append(record)
        status = "PASS" if record["all_checks_passed"] else "FAIL"
        canonical_qa = record["canonical_projection"]["qa"]
        print(
            f"[factory-bench] {pid} {status}: chain={record['chain_state']} "
            f"files={record['code_file_count']} source={record.get('source_file_count', '?')} "
            f"plan={record['has_plan_doc']} blueprint={record['has_blueprint_doc']} "
            f"verdict_artifact={record['has_qa_verdict']} qa_authoritative={canonical_qa['authoritative']} "
            f"qa_passed={canonical_qa['ok']} director_artifact={record['chain_results'].get('director', {})} "
            f"goal_overlap={record['goal_brief_overlap']}"
            f"{' [WRONG-PRODUCT? ~' + record['wrong_product_match'] + ']' if record['wrong_product_suspect'] else ''} "
            f"chain_exit={chain.get('exit_code')} ({chain.get('duration_s')}s)",
            flush=True,
        )
        for check in record["checks"]:
            print(
                f"[factory-bench]   - {check['check']}: {'ok' if check['ok'] else 'FAIL'} ({check['detail']})",
                flush=True,
            )
        for gate in record["factory_gates"]:
            print(
                f"[factory-bench]   - gate:{gate['gate']}: {'ok' if gate['ok'] else 'FAIL'} ({gate['detail']})",
                flush=True,
            )
            _emit_bench_event(
                workspace=base,
                project_id=pid,
                level=int(project.get("level") or 0),
                name="gate.evaluated",
                summary=f"{pid} gate:{gate['gate']}={'ok' if gate['ok'] else 'FAIL'}",
                meta={
                    "session_id": bench_session_id,
                    "gate": gate["gate"],
                    "ok": bool(gate["ok"]),
                    "detail": gate.get("detail") or "",
                },
            )
        coverage_gap_summary = record.get("director_repair_coverage_gap_summary")
        if isinstance(coverage_gap_summary, dict) and int(coverage_gap_summary.get("coverage_gap_count") or 0) > 0:
            print(
                "[factory-bench]   - repair coverage gaps: "
                f"count={coverage_gap_summary['coverage_gap_count']} "
                f"languages={coverage_gap_summary['coverage_gap_languages']} "
                f"codes={coverage_gap_summary['coverage_gap_diagnostic_codes']} "
                f"routes={coverage_gap_summary['coverage_gap_recommended_routes']}",
                flush=True,
            )
        _emit_bench_event(
            workspace=base,
            project_id=pid,
            level=int(project.get("level") or 0),
            name="project.audit",
            summary=(
                f"{pid} audit={status} real_run={bool(record['real_run_gate'].get('ok'))} "
                f"llm_route={bool(record['llm_route_audit'].get('ok'))} "
                f"root={record['failure_taxonomy'].get('root_cause_signature')}"
            ),
            meta={
                "session_id": bench_session_id,
                "project_id": pid,
                "status": status.lower(),
                "real_run_gate": record["real_run_gate"],
                "llm_route_audit": record["llm_route_audit"],
                "failure_taxonomy": record["failure_taxonomy"],
            },
        )
        # Push live counters to the optional shared bench session. Isolated
        # project instances do not depend on this observation bridge.
        if bench_session_backend_url and bench_session_id:
            _push_bench_progress_to_backend(
                backend_url=bench_session_backend_url,
                session_id=bench_session_id,
                completed=sum(1 for r in records if r.get("all_checks_passed")),
                failed=sum(1 for r in records if not r.get("all_checks_passed")),
                token=backend_token,
            )

        out_path = base / "factory_audits.json"
        partial_agg = aggregate_factory_audits(records)
        partial_goal_audit = aggregate_goal_audit(records)
        partial_payload = {
            "aggregate": partial_agg,
            "goal_audit": partial_goal_audit,
            "records": records,
        }
        _attach_platform_residual_attribution(partial_payload, source_path=str(out_path))
        out_path.write_text(
            json.dumps(partial_payload, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        # Write immutable per-run audit package
        audit_file = _next_immutable_json_path(audit_dir / f"{pid}.audit.json")
        project_audit = {
            "catalog_schema_version": catalog_schema_version,
            "catalog_hash": catalog_hash,
            "run_id": run_id,
            "project_id": pid,
            "record": record,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "audit_path": str(audit_file.relative_to(base)),
        }
        _write_immutable_json(audit_file, project_audit)
        if not record["all_checks_passed"]:
            failed += 1
            if args.max_failed > 0 and failed >= args.max_failed:
                print(f"[factory-bench] early stop: {failed} failures (audit before continuing)", flush=True)
                break

    agg = aggregate_factory_audits(records)
    goal_audit = aggregate_goal_audit(records)
    out_path = base / "factory_audits.json"
    final_payload: dict[str, Any] = {
        "aggregate": agg,
        "goal_audit": goal_audit,
        "records": records,
    }
    _attach_platform_residual_attribution(final_payload, source_path=str(out_path))
    out_path.write_text(
        json.dumps(final_payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    run_success = agg["all_checks_passed"] == agg["total"]
    primary_attr = (final_payload.get("platform_residual_attribution") or {}).get("primary") or {}
    if primary_attr and not run_success:
        # Surface non-terminal residual attribution for workflow supervisors.
        _logger.info(
            "platform residual attribution primary_module_id=%s delivery_status=%s",
            primary_attr.get("primary_module_id"),
            primary_attr.get("delivery_status"),
        )
        print(
            f"[factory-bench] residual_module={primary_attr.get('primary_module_id')} "
            f"delivery_status={primary_attr.get('delivery_status')}",
            flush=True,
        )
    _emit_bench_event(
        workspace=base,
        project_id="-",
        level=0,
        name="run.completed",
        summary=f"factory-bench {agg['all_checks_passed']}/{agg['total']} passed",
        meta={
            "session_id": bench_session_id,
            "total": agg["total"],
            "passed": agg["all_checks_passed"],
            "failed": agg["total"] - agg["all_checks_passed"],
            "by_level": agg["by_level"],
            "goal_audit": goal_audit,
        },
    )
    if bench_session_backend_url and bench_session_id:
        complete_summary = {
            "total": agg["total"],
            "passed": agg["all_checks_passed"],
            "failed": agg["total"] - agg["all_checks_passed"],
            "by_level": agg["by_level"],
            "goal_audit": goal_audit,
        }
        if run_errors:
            complete_summary["error"] = "; ".join(run_errors)
        _push_bench_complete_to_backend(
            backend_url=bench_session_backend_url,
            session_id=bench_session_id,
            success=run_success,
            summary=complete_summary,
            token=backend_token,
        )
    print(
        f"\n[factory-bench] ===== {agg['all_checks_passed']}/{agg['total']} passed | by_level={agg['by_level']} =====",
        flush=True,
    )

    print(f"[factory-bench] audits -> {base / 'factory_audits.json'}", flush=True)
    return 0 if run_success else 1
