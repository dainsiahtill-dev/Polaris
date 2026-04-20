"""Director run management for loop-pm (interface-only path)."""

import argparse
import logging
import os
import time
from datetime import datetime
from typing import Any

from polaris.infrastructure.compat.io_utils import (
    ensure_parent_dir,
    write_json_atomic,
)

logger = logging.getLogger(__name__)


def append_director_log(log_path: str, text: str) -> None:
    """Append text to director log."""
    if not log_path:
        return
    ensure_parent_dir(log_path)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(text)


def write_director_status(path: str, payload: dict[str, Any]) -> None:
    """Write director status to file."""
    if not path:
        return
    try:
        ensure_parent_dir(path)
        write_json_atomic(path, payload)
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to write director status: {e}")


def run_director_once(
    args: argparse.Namespace,
    workspace_full: str,
    iteration: int,
    subprocess_log_path: str = "",
    director_log_path: str = "",
    status_path: str = "",
    status_payload: dict[str, Any] | None = None,
    pid_holder: dict[str, int] | None = None,
    task: dict[str, Any] | None = None,
) -> int:
    """Run director once through DirectorInterface."""
    base_status_payload: dict[str, Any] = dict(status_payload) if isinstance(status_payload, dict) else {}
    tracked_pid: int | None = None

    def _update_runtime_status(
        *,
        running: bool,
        exit_code: int | None = None,
        error: str = "",
    ) -> None:
        if not status_path:
            return
        payload = dict(base_status_payload)
        if tracked_pid is not None:
            payload["pid"] = tracked_pid
        payload["running"] = bool(running)
        payload["updated_at"] = time.time()
        if exit_code is not None:
            payload["exit_code"] = int(exit_code)
            payload["ended_at"] = time.time()
        if error:
            payload["error"] = error
        elif exit_code is not None and int(exit_code) == 0:
            payload["error"] = ""
        write_director_status(status_path, payload)

    # Determine effective director type once for this run.
    director_type = (
        str(getattr(args, "director_type", None) or os.getenv("POLARIS_DIRECTOR_TYPE", "auto")).strip().lower()
    )
    if director_type not in {"auto", "script", "none"}:
        error_text = f"Unsupported director_type '{director_type}'. Allowed: auto, script, none."
        if subprocess_log_path:
            append_director_log(subprocess_log_path, f"[error] {error_text}\n")
        _update_runtime_status(running=False, exit_code=1, error=error_text)
        return 1

    # Handle standalone mode (no Director)
    if director_type == "none":
        if subprocess_log_path:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            append_director_log(
                subprocess_log_path,
                f"\n## {stamp} (iteration {iteration}) - No Director mode\n"
                f"[info] PM running in standalone mode - skipping Director execution\n",
            )
        _update_runtime_status(running=False, exit_code=0)
        return 0

    # New architecture: always route through DirectorInterface.
    try:
        from polaris.delivery.cli.pm.director_interface_integration import (
            DIRECTOR_INTERFACE_AVAILABLE,
            run_director_via_interface,
        )
    except (RuntimeError, ValueError) as exc:
        if subprocess_log_path:
            append_director_log(
                subprocess_log_path,
                f"[error] DirectorInterface import failed: {exc}\n",
            )
        _update_runtime_status(running=False, exit_code=1, error=str(exc))
        return 1

    if not DIRECTOR_INTERFACE_AVAILABLE:
        error_text = "DirectorInterface unavailable in current runtime"
        if subprocess_log_path:
            append_director_log(subprocess_log_path, f"[error] {error_text}\n")
        _update_runtime_status(running=False, exit_code=1, error=error_text)
        return 1

    # Try to load task from pm_task_path when caller didn't pass it.
    if task is None:
        pm_task_path = getattr(args, "pm_task_path", None)
        if pm_task_path and os.path.exists(pm_task_path):
            import json

            with open(pm_task_path, encoding="utf-8") as f:
                task = json.load(f)

    if task is None:
        error_text = "Director task payload missing (pm_task_path not found or invalid)"
        if subprocess_log_path:
            append_director_log(subprocess_log_path, f"[error] {error_text}\n")
        _update_runtime_status(running=False, exit_code=1, error=error_text)
        return 1

    _update_runtime_status(running=True)
    exit_code = run_director_via_interface(
        args=args,
        workspace_full=workspace_full,
        iteration=iteration,
        task=task,
        subprocess_log_path=subprocess_log_path,
        director_log_path=director_log_path,
    )
    _update_runtime_status(
        running=False,
        exit_code=exit_code,
        error="" if int(exit_code) == 0 else f"DIRECTOR_EXIT_{int(exit_code)}",
    )
    return exit_code


def detect_plan_missing(plan_path: str, log_path: str, since_ts: float) -> str:
    """Detect if the plan contract was created and needs editing."""
    plan_created = False
    if plan_path and os.path.exists(plan_path):
        try:
            plan_created = os.path.getmtime(plan_path) >= max(0.0, since_ts - 2)
        except (RuntimeError, ValueError):
            plan_created = False
    if log_path:
        from polaris.delivery.cli.pm.utils import read_tail_lines

        tail = read_tail_lines(log_path, max_lines=120)
        for line in tail:
            if ("PLAN.md" in line or "contracts/plan.md" in line) and "Edit it and rerun" in line:
                return f"Plan contract was created. Edit {plan_path} and rerun."
    if plan_created:
        return f"Plan contract was created. Edit {plan_path} and rerun."
    return ""


def preflight_director_plan(plan_path: str) -> str | None:
    """Preflight check for director plan."""
    if not plan_path:
        return "Plan contract path missing."
    if os.path.exists(plan_path):
        return None
    from polaris.cells.docs.court_workflow.public import ensure_plan_file
    from polaris.delivery.cli.pm.utils import auto_plan_enabled

    if auto_plan_enabled():
        ensure_plan_file(plan_path, auto_continue=True)
        return None
    ensure_plan_file(plan_path, auto_continue=False)
    return f"Plan contract was created. Edit {plan_path} and rerun."


def archive_if_exists(src: str, dest: str) -> None:
    """Archive file if it exists."""
    if not src or not os.path.exists(src):
        return
    ensure_parent_dir(dest)
    try:
        with open(src, encoding="utf-8") as handle:
            content = handle.read()
        from polaris.infrastructure.compat.io_utils import write_text_atomic

        write_text_atomic(dest, content)
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to copy artifact: {e}")


def build_run_dir(workspace: str, cache_root: str, iteration: int) -> str:
    """Build run directory path."""
    from polaris.infrastructure.compat.io_utils import resolve_artifact_path

    rel = os.path.join("runtime", "runs", f"pm-{iteration:05d}")
    return resolve_artifact_path(workspace, cache_root, rel)


__all__ = [
    "append_director_log",
    "archive_if_exists",
    "build_run_dir",
    "detect_plan_missing",
    "preflight_director_plan",
    "run_director_once",
    "write_director_status",
]
