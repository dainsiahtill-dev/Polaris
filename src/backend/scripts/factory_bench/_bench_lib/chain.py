"""HTTP factory chain and legacy-style run_chain orchestration.

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


from scripts.factory_bench._bench_lib import gates as _gates

_pull_namespace(_gates)
del _gates


def _factory_role_completed(roles: Mapping[str, Any], role: str) -> bool:
    role_raw = roles.get(role)
    role_row = dict(role_raw) if isinstance(role_raw, Mapping) else {}
    return str(role_row.get("status") or "").strip().lower() == "completed"


def _select_director_resume_run(
    runs_response: Mapping[str, Any] | None,
    *,
    project_id: str = "",
) -> dict[str, Any] | None:
    """Select one failed full-chain run whose PM/CE checkpoints are frozen."""

    if not isinstance(runs_response, Mapping):
        return None
    raw_runs = runs_response.get("runs")
    if not isinstance(raw_runs, list):
        return None
    expected_project_id = str(project_id or "").strip()
    for raw_run in raw_runs:
        if not isinstance(raw_run, Mapping):
            continue
        run = dict(raw_run)
        if str(run.get("status") or "").strip().lower() != "failed":
            continue
        metadata_raw = run.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        failure_raw = run.get("failure")
        failure = dict(failure_raw) if isinstance(failure_raw, Mapping) else {}
        failed_stage = str(
            failure.get("stage")
            or metadata.get("last_failed_stage")
            or metadata.get("current_stage")
            or run.get("current_stage")
            or ""
        ).strip()
        if failed_stage != "director_dispatch":
            continue
        candidate_project_id = str(metadata.get("factory_bench_project_id") or "").strip()
        if expected_project_id and candidate_project_id and candidate_project_id != expected_project_id:
            continue
        roles_raw = run.get("roles")
        roles = dict(roles_raw) if isinstance(roles_raw, Mapping) else {}
        last_successful_stage = str(
            run.get("last_successful_stage") or metadata.get("last_successful_stage") or ""
        ).strip()
        if not _factory_role_completed(roles, "pm"):
            continue
        if not (
            _factory_role_completed(roles, "chief_engineer")
            or last_successful_stage == "chief_engineer_review"
        ):
            continue
        if not str(run.get("run_id") or "").strip():
            continue
        return run
    return None


def run_factory_chain(
    project: dict[str, Any],
    workspace: Path,
    *,
    backend_url: str,
    backend_token: str,
    timeout_s: int,
    log_path: Path,
    director_workflow_execution_mode: str = "parallel",
    director_dispatch_driver: str = "task-market",
    bench_session_id: str = "",
    start_from: str = "pm",
    on_stage_change: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Start a full run, or resume the same failed run at its Director checkpoint."""
    normalized_start_from = str(start_from or "pm").strip().lower()
    if normalized_start_from not in {"pm", "director_resume"}:
        raise ValueError(f"unsupported factory bench start_from: {start_from!r}")
    requested_start_from = normalized_start_from
    api_start_from = "director_resume" if normalized_start_from == "director_resume" else normalized_start_from
    workflow_mode = str(director_workflow_execution_mode or "parallel").strip().lower()
    if workflow_mode not in {"serial", "parallel"}:
        raise ValueError(f"unsupported director workflow execution mode: {director_workflow_execution_mode!r}")
    dispatch_driver = str(director_dispatch_driver or "task-market").strip().lower()
    if dispatch_driver != "task-market":
        raise ValueError("factory-bench only supports the PM→Chief Engineer→Director task-market chain")
    parsed_backend_url = urllib.parse.urlparse(str(backend_url or ""))
    backend_port = parsed_backend_url.port

    feature_keywords = _extract_feature_keywords(project)
    requirements_doc = build_requirements_doc(project)
    level_contract = build_factory_bench_level_contract(project.get("level"), project=project)
    if api_start_from != "director_resume":
        requirements_path = workspace / "requirements.md"
        requirements_path.write_text(requirements_doc, encoding="utf-8")
        ws_requirements = workspace / ".polaris" / "docs" / "product" / "requirements.md"
        ws_requirements.parent.mkdir(parents=True, exist_ok=True)
        ws_requirements.write_text(requirements_doc, encoding="utf-8")

        # Embed catalog metadata in the workspace so PM -> Chief Engineer -> Director can access it
        catalog_contract_path = workspace / ".polaris" / "catalog_contract.json"
        catalog_contract_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_contract = {
            "project_id": str(project.get("id") or "").strip(),
            "domain": str(project.get("domain") or "").strip(),
            "project_type": str(project.get("project_type") or "").strip(),
            "primary_language": str(project.get("primary_language") or "").strip(),
            "creative_hook": str(project.get("creative_hook") or "").strip(),
            "feature_keywords": feature_keywords,
            "checks": list(project.get("checks") or []),
            "test_focus": str(project.get("test_focus") or "").strip(),
            "level": int(project.get("level") or 0),
            "level_contract": level_contract,
            "source_tree_mandate": (
                "PM -> Chief Engineer -> Director must create src/ with core source files, not just scaffolding"
            ),
        }
        catalog_contract_path.write_text(
            json.dumps(catalog_contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if not (workspace / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=str(workspace), check=False)
        subprocess.run(["git", "add", "-A"], cwd=str(workspace), check=False)
        subprocess.run(
            ["git", "-c", "user.email=bench@polaris", "-c", "user.name=bench", "commit", "-qm", "bench: seed"],
            cwd=str(workspace),
            check=False,
        )

    started = time.time()
    deadline_safety_seconds = min(max(float(timeout_s) * 0.05, 15.0), 30.0)
    factory_deadline_epoch_seconds = started + max(float(timeout_s) - deadline_safety_seconds, 1.0)

    payload = {
        "workspace": str(workspace),
        "start_from": api_start_from,
        "directive": requirements_doc,
        "run_director": True,
        "director_iterations": 0,
        "director_workflow_execution_mode": workflow_mode,
        "director_dispatch_driver": "task-market",
        "loop": False,
        "input_source": "directive",
        "persist_workspace": False,
        "metadata": {
            "factory_bench_session_id": str(bench_session_id or "").strip(),
            "factory_bench_project_id": str(project.get("id") or "").strip(),
            "factory_bench_requested_project_id": str(
                project.get("requested_project_id") or project.get("requested_id") or project.get("id") or ""
            ).strip(),
            "factory_bench_canonical_project_id": str(
                project.get("canonical_project_id") or project.get("canonical_id") or project.get("id") or ""
            ).strip(),
            "factory_bench_level": int(project.get("level") or 0),
            "factory_bench_title": str(project.get("title") or "").strip(),
            "factory_bench_project_workspace": str(workspace.resolve()),
            "backend_url": str(backend_url or "").strip(),
            "backend_port": backend_port,
            "frontend_port": project.get("frontend_port"),
            "instance_id": str(project.get("instance_id") or project.get("launcher_instance_id") or "").strip(),
            "factory_bench_start_from": requested_start_from,
            "factory_bench_api_start_from": api_start_from,
            "factory_run_timeout_seconds": float(timeout_s),
            "factory_run_started_epoch_seconds": started,
            "factory_run_deadline_epoch_seconds": factory_deadline_epoch_seconds,
            "factory_run_deadline_safety_seconds": deadline_safety_seconds,
            "factory_run_deadline_source": "factory_bench_runner",
        },
    }

    with open(log_path, "w", encoding="utf-8") as log_fh:

        def _on_status(status: dict[str, Any]) -> None:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            phase = status.get("phase", "")
            msg = f"[{ts}] status={status.get('status')} phase={phase}\n"
            log_fh.write(msg)
            log_fh.flush()
            if on_stage_change is not None:
                on_stage_change(str(status.get("status") or ""), status)

        resume_source_run_id = ""
        if api_start_from == "director_resume":
            runs_response = list_factory_runs(
                backend_url,
                token=backend_token,
                workspace=str(workspace),
            )
            resume_source = _select_director_resume_run(
                runs_response,
                project_id=str(project.get("id") or "").strip(),
            )
            if resume_source is None:
                return {
                    "exit_code": -1,
                    "duration_s": round(time.time() - started, 1),
                    "error": "director_resume_run_missing",
                    "workspace": str(workspace),
                }
            resume_source_run_id = str(resume_source["run_id"]).strip()
            _prepare_director_resume_workspace(workspace)
            start_response = retry_factory_run_from_director(
                backend_url,
                resume_source_run_id,
                token=backend_token,
                workspace=str(workspace),
                reason="factory-bench local Director repair; preserve committed PM/CE checkpoints",
            )
        else:
            start_response = start_factory_run(backend_url, payload, token=backend_token)
        if not isinstance(start_response, dict):
            return {"exit_code": -1, "duration_s": 0, "error": "start_failed"}
        if isinstance(start_response.get("_http_error"), dict):
            return {
                "exit_code": -1,
                "duration_s": round(time.time() - started, 1),
                "error": "start_failed",
                "start_error": start_response["_http_error"],
            }

        run_id = str(start_response.get("run_id") or "").strip()
        if not run_id:
            return {
                "exit_code": -1,
                "duration_s": round(time.time() - started, 1),
                "error": "start_failed",
                "start_response": start_response,
            }
        if resume_source_run_id and run_id != resume_source_run_id:
            return {
                "exit_code": -1,
                "duration_s": round(time.time() - started, 1),
                "error": "director_resume_run_identity_changed",
                "expected_run_id": resume_source_run_id,
                "actual_run_id": run_id,
            }

        terminal_status = wait_run_until_terminal(
            backend_url,
            run_id,
            token=backend_token,
            workspace=str(workspace),
            timeout_s=float(timeout_s),
            on_status=_on_status,
            initial_status=start_response,
            return_diagnostics=True,
        )
        event_wait_error: dict[str, Any] = {}
        last_observed_status: dict[str, Any] = {}
        if isinstance(terminal_status, dict):
            raw_event_wait_error = terminal_status.get("_event_wait_error")
            if isinstance(raw_event_wait_error, dict):
                event_wait_error = raw_event_wait_error
            raw_last_observed = terminal_status.get("last_observed_status")
            if isinstance(raw_last_observed, dict):
                last_observed_status = raw_last_observed
        if terminal_status is None or event_wait_error:
            # R153: wait_run_until_terminal only returns _event_wait_error after the
            # wall-clock budget is exhausted (reconnect-until-deadline). Cancel then is
            # correct; the reason string must distinguish true timeout from connection
            # exhaustion so residual taxonomy is not mislabeled as a generic 5400s timeout
            # when the underlying observation path failed earlier and reconnected until
            # the deadline.
            wait_kind = str(event_wait_error.get("kind") or "").strip() or "timeout"
            wait_message = str(event_wait_error.get("message") or "").strip()
            if wait_kind == "runtime_v2_connection_failed":
                cancel_reason = f"factory-bench event wait runtime.v2 connection failed after {timeout_s}s" + (
                    f": {wait_message}" if wait_message else ""
                )
            elif wait_kind and wait_kind != "timeout":
                cancel_reason = f"factory-bench event wait {wait_kind} after {timeout_s}s" + (
                    f": {wait_message}" if wait_message else ""
                )
            else:
                cancel_reason = f"factory-bench event wait timeout after {timeout_s}s"
            cancel_response = cancel_factory_run(
                backend_url,
                run_id,
                reason=cancel_reason,
                token=backend_token,
                workspace=str(workspace),
                return_errors=True,
            )
            cancel_error = (
                cancel_response.get("_http_error")
                if isinstance(cancel_response, dict) and isinstance(cancel_response.get("_http_error"), dict)
                else {}
            )
            return {
                "exit_code": -1,
                "duration_s": round(time.time() - started, 1),
                "run_id": run_id,
                "error": "event_wait_timeout",
                "event_wait_error": event_wait_error,
                "last_observed_status": last_observed_status,
                "cancel_response": cancel_response,
                "cancel_error": cancel_error,
                "backend_url": backend_url,
                "workspace": str(workspace),
            }

    audit_bundle = get_audit_bundle(backend_url, run_id, token=backend_token, workspace=str(workspace))
    if not audit_bundle:
        _logger.warning(
            "factory-bench: audit-bundle GET returned empty/None for run %s; "
            "falling back to workspace .polaris artifacts",
            run_id,
        )
        audit_bundle = _fallback_audit_bundle_from_workspace(workspace)
    chain_results = map_factory_run_to_chain_results(terminal_status, audit_bundle)
    chain_results["factory_bench_start_from"] = requested_start_from
    chain_results["factory_api_start_from"] = api_start_from
    chain_results["factory_resume_mode"] = (
        "same_run_retry_phase" if api_start_from == "director_resume" else "new_full_run"
    )

    # Read contract_goal from workspace tasks/plan.json if available
    plan_path = workspace / ".polaris" / "docs" / "product" / "plan.json"
    if plan_path.is_file():
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            chain_results["contract_goal"] = str(plan_data.get("overall_goal") or "")[:160]
        except (OSError, ValueError):
            pass

    return {
        "exit_code": 0 if str(terminal_status.get("status") or "").lower() == "completed" else 1,
        "duration_s": round(time.time() - started, 1),
        "run_id": run_id,
        "start_from": requested_start_from,
        "factory_api_start_from": api_start_from,
        "factory_resume_mode": chain_results["factory_resume_mode"],
        "factory_terminal_status": terminal_status,
        "chain_results": chain_results,
        "audit_bundle": audit_bundle,
    }


def run_chain(
    project: dict[str, Any],
    workspace: Path,
    *,
    timeout_s: int,
    log_path: Path,
    director_workflow_execution_mode: str = "serial",
    director_dispatch_driver: str = "workflow",
) -> dict[str, Any]:
    """Invoke the full role chain headlessly on the workspace (subprocess).

    The exact invocation is centralized here; see factory-bench recon notes in
    the capability-amplification blueprint for the entrypoint decision.
    """
    requirements_path = workspace.parent / f"{project['id']}.requirements.md"
    requirements_doc = build_requirements_doc(project)
    requirements_path.write_text(requirements_doc, encoding="utf-8")
    # Belt and braces: also seed the workspace-resident requirements file the
    # chain's docs auto-init would otherwise fill with a placeholder template.
    ws_requirements = workspace / ".polaris" / "docs" / "product" / "requirements.md"
    ws_requirements.parent.mkdir(parents=True, exist_ok=True)
    ws_requirements.write_text(requirements_doc, encoding="utf-8")
    # Embed catalog metadata in the workspace so PM -> Chief Engineer -> Director can access it
    catalog_contract_path = workspace / ".polaris" / "catalog_contract.json"
    catalog_contract_path.parent.mkdir(parents=True, exist_ok=True)
    feature_keywords = _extract_feature_keywords(project)
    catalog_contract = {
        "project_id": str(project.get("id") or "").strip(),
        "domain": str(project.get("domain") or "").strip(),
        "project_type": str(project.get("project_type") or "").strip(),
        "primary_language": str(project.get("primary_language") or "").strip(),
        "creative_hook": str(project.get("creative_hook") or "").strip(),
        "feature_keywords": feature_keywords,
        "checks": list(project.get("checks") or []),
        "test_focus": str(project.get("test_focus") or "").strip(),
        "source_tree_mandate": (
            "PM -> Chief Engineer -> Director must create src/ with core source files, not just scaffolding"
        ),
    }
    catalog_contract_path.write_text(
        json.dumps(catalog_contract, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # Director bundle machinery wants a git base sha; give the workspace a repo.
    if not (workspace / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=str(workspace), check=False)
        subprocess.run(["git", "add", "-A"], cwd=str(workspace), check=False)
        subprocess.run(
            ["git", "-c", "user.email=bench@polaris", "-c", "user.name=bench", "commit", "-qm", "bench: seed"],
            cwd=str(workspace),
            check=False,
        )
    dispatch_driver = str(director_dispatch_driver or "workflow").strip().lower()
    if dispatch_driver not in {"workflow", "task-market"}:
        raise ValueError(f"unsupported director dispatch driver: {director_dispatch_driver!r}")
    workflow_mode = str(director_workflow_execution_mode or "serial").strip().lower()
    if workflow_mode not in {"serial", "parallel"}:
        raise ValueError(f"unsupported director workflow execution mode: {director_workflow_execution_mode!r}")

    cmd = [
        sys.executable,
        "-m",
        "polaris.delivery.cli.pm.cli",
        "--workspace",
        str(workspace),
        "--iterations",
        "1",
        "--requirements-path",
        str(requirements_path.resolve()),
        # Local 27B decodes ~20 tok/s; the 360s default PM timeout is sized for
        # cloud latency and kills planning mid-JSON.
        "--timeout",
        "1800",
    ]
    if dispatch_driver == "workflow":
        cmd.extend(
            [
                "--run-director",
                "--director-workflow-execution-mode",
                workflow_mode,
            ]
        )
    env = dict(os.environ)
    env.setdefault("KERNELONE_WORKSPACE", str(workspace))
    if dispatch_driver == "task-market":
        env.setdefault("KERNELONE_TASK_MARKET_MODE", "mainline-full")
        env.setdefault("KERNELONE_TASK_MARKET_ROLE_POOLS", "director")
        env.setdefault("KERNELONE_TASK_MARKET_ENABLE_SAFE_PARALLEL_DIRECTOR", "1")
        # Live factory-bench L1-01 / L2-07 / L6-32 (2026-06-17): with
        # KERNELONE_CE_STEP_FISSION off (the migration default), CE
        # does not fanout parent tasks into leaf steps, so the market
        # only ever has the parent task. Workers serialize on it, the
        # second/third siblings stay in `pending_design` forever, and
        # integration_qa never gets called. The task-market
        # dispatch driver is a deliberate opt-in to a more parallel
        # path, so it must also opt in to step fission.
        env.setdefault("KERNELONE_CE_STEP_FISSION", "1")
    # Module imports come from PYTHONPATH, NOT cwd: parts of the chain key
    # role-session/storage roots off the CURRENT DIRECTORY's workspace
    # resolution (docs sentinel). Running with cwd=src/backend made every
    # project share one "backend-…" session space — live forensics 2026-06-12:
    # L1-06 (tic-tac-toe) planned and shipped L1-01's calculator because the
    # planning role session replayed cross-project state.
    env["PYTHONPATH"] = str(_BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # Role bindings follow the user's GLOBAL llm config: orchestration roles
    # (PM/Chief Engineer/QA) on cloud large-context models, the Director coding role on the
    # local model under test. (The all-local override used during early bring-up
    # lives on in ~/Temp/factory-bench/llm_config_all_qwen.json — set
    # KERNELONE_LLM_CONFIG yourself to reproduce those runs.)
    started = time.time()
    with open(log_path, "w", encoding="utf-8") as log_fh:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
        if dispatch_driver != "task-market" or proc.returncode != 0:
            return {"exit_code": proc.returncode, "duration_s": round(time.time() - started, 1)}
        log_fh.write("\n[factory-bench] === task-market dispatch ===\n")
        log_fh.flush()
        market_cmd = [
            sys.executable,
            str(_BACKEND_ROOT / "scripts" / "factory_bench" / "run_market_chain.py"),
            "--workspace",
            str(workspace),
            "--fresh-market",
            "--archive-label",
            f"factory-bench-{project['id']}",
        ]
        market_proc = subprocess.run(
            market_cmd,
            cwd=str(workspace),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
    return {
        "exit_code": market_proc.returncode,
        "duration_s": round(time.time() - started, 1),
        "planning_exit_code": proc.returncode,
        "task_market_exit_code": market_proc.returncode,
    }
