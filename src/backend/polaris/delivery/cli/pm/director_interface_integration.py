"""
Director Interface Integration for loop-pm.

Integrates the DirectorInterface abstraction into the PM loop,
allowing the PM to use either Script Director or No Director mode.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from polaris.delivery.cli.pm.config import PROJECT_ROOT
from polaris.delivery.cli.pm.director_mgmt import (
    append_director_log,
)
from polaris.kernelone.runtime.shared_types import normalize_path_list, timeout_seconds_or_none

logger = logging.getLogger(__name__)


def _bootstrap_backend_import_path() -> None:
    """Ensure backend package path when running file directly."""
    if __package__:
        return
    backend_root = Path(__file__).resolve().parents[4]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)


_bootstrap_backend_import_path()

try:
    from polaris.delivery.cli.pm.director_interface_core import (
        DirectorInterface,
        DirectorTask,
        create_director,
    )

    DIRECTOR_INTERFACE_AVAILABLE = True
except ImportError:
    DIRECTOR_INTERFACE_AVAILABLE = False


def resolve_project_root() -> Path:
    """Resolve the project root directory."""
    # Use PROJECT_ROOT directly from config
    if hasattr(PROJECT_ROOT, "parent"):
        return Path(PROJECT_ROOT).parent
    return Path(PROJECT_ROOT)


def create_director_for_pm(
    workspace: str,
    director_type: str | None = None,
    config: dict | None = None,
) -> DirectorInterface | None:
    """
    Create a Director instance for use by PM.

    Args:
        workspace: Workspace path
        director_type: 'script', 'none', or None for auto
        config: Additional configuration

    Returns:
        DirectorInterface instance or None if not available
    """
    if not DIRECTOR_INTERFACE_AVAILABLE:
        return None

    config = config or {}
    config["project_root"] = resolve_project_root()

    try:
        return create_director(workspace, director_type, config)
    except (RuntimeError, ValueError):
        logger.exception("director interface creation failed")
        return None


def _resolve_effective_director_timeout(args: argparse.Namespace) -> int | None:
    """
    Resolve effective Director timeout for DirectorInterface path.

    Semantics:
    - ``--director-timeout > 0``: use that timeout.
    - ``--director-timeout <= 0``: disable timeout (None).
    - If CLI option is unavailable, fallback to env
      ``KERNELONE_DIRECTOR_RUN_TIMEOUT`` (non-positive => disabled).
    """
    cli_timeout = getattr(args, "director_timeout", None)
    if cli_timeout is not None:
        return timeout_seconds_or_none(cli_timeout, default=0)
    return timeout_seconds_or_none(
        os.environ.get("KERNELONE_DIRECTOR_RUN_TIMEOUT", "0"),
        default=0,
    )


def run_director_via_interface(
    args: argparse.Namespace,
    workspace_full: str,
    iteration: int,
    task: dict[str, Any],
    subprocess_log_path: str = "",
    director_log_path: str = "",
) -> int:
    """
    Run Director using the DirectorInterface abstraction.

    This replaces the subprocess-based run_director_once when
    KERNELONE_DIRECTOR_TYPE is set to 'script' or 'none'.

    Args:
        args: CLI arguments
        workspace_full: Full workspace path
        iteration: Current iteration number
        task: Task dictionary with goal, target_files, etc.
        subprocess_log_path: Path for logging
        director_log_path: Path for director runlog output

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    from datetime import datetime

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if subprocess_log_path:
        append_director_log(
            subprocess_log_path,
            f"\n## {stamp} (iteration {iteration}) - DirectorInterface start\n",
        )

    # Determine director type from environment or args
    director_type = os.getenv("KERNELONE_DIRECTOR_TYPE", "auto")
    if director_type == "auto":
        director_type = getattr(args, "director_type", None) or "auto"
    token = str(director_type or "").strip().lower()
    if token and token not in {"auto", "script", "none"}:
        if subprocess_log_path:
            append_director_log(
                subprocess_log_path,
                f"[error] Unsupported director_type '{token}'. Allowed: auto, script, none.\n",
            )
        return 1

    # Build config
    config = {
        "script": getattr(
            args,
            "director_path",
            "src/backend/polaris/delivery/cli/director_v2.py",
        ),
        "timeout": _resolve_effective_director_timeout(args),
        "model": getattr(args, "director_model", "gpt-4"),
        "pm_task_path": getattr(args, "pm_task_path", ""),
        "director_result_path": getattr(args, "director_result_path", ""),
        "director_log_path": director_log_path or getattr(args, "director_log_path", ""),
        "prompt_profile": getattr(args, "prompt_profile", ""),
        "planner_response_path": getattr(args, "planner_response_path", ""),
        "ollama_response_path": getattr(args, "ollama_response_path", ""),
        "qa_response_path": getattr(args, "qa_response_path", ""),
        "reviewer_response_path": getattr(args, "reviewer_response_path", ""),
    }

    # Create director
    director = create_director_for_pm(workspace_full, director_type, config)
    if director is None:
        error_msg = "DirectorInterface not available or failed to initialize"
        if subprocess_log_path:
            append_director_log(subprocess_log_path, f"[error] {error_msg}\n")
        return 1

    # Log director info
    info = director.get_info()
    if subprocess_log_path:
        append_director_log(
            subprocess_log_path,
            f"[info] Using director: {info.get('name', 'unknown')} ({info.get('type', 'unknown')})\n",
        )

    # Create DirectorTask
    director_task = DirectorTask(
        task_id=task.get("id", f"task-{iteration}"),
        goal=task.get("goal", task.get("title", "")),
        target_files=normalize_path_list(task.get("target_files", [])),
        acceptance_criteria=task.get("acceptance", task.get("acceptance_criteria", [])),
        constraints=task.get("constraints", []),
        context={
            "workspace": workspace_full,
            "iteration": iteration,
            "task": task,
        },
        scope_paths=normalize_path_list(task.get("scope_paths", [])),
        scope_mode=task.get("scope_mode", "module"),
    )

    # Execute
    try:
        result = director.execute(director_task)

        # Log result
        if result.success:
            msg = f"Director completed: {len(result.changed_files)} files changed"
            if subprocess_log_path:
                append_director_log(subprocess_log_path, f"[success] {msg}\n")
        else:
            error = result.error or "Unknown error"
            if subprocess_log_path:
                append_director_log(subprocess_log_path, f"[error] {error}\n")

        return 0 if result.success else 1

    except (RuntimeError, ValueError) as e:
        if subprocess_log_path:
            append_director_log(subprocess_log_path, f"[error] Director execution failed: {e}\n")
        return 1


def should_use_director_interface(args: argparse.Namespace) -> bool:
    """
    Check if we should use DirectorInterface instead of subprocess.

    Returns True if:
    1. DirectorInterface is available
    2. KERNELONE_DIRECTOR_TYPE is set to 'script' or 'none'
    3. Not explicitly disabled
    """
    if not DIRECTOR_INTERFACE_AVAILABLE:
        return False

    director_type = os.getenv("KERNELONE_DIRECTOR_TYPE", "")
    if director_type in ("script", "none"):
        return True

    # Check if director_type arg is set
    arg_director_type = getattr(args, "director_type", None)
    return arg_director_type in ("script", "none")


def is_standalone_mode(args: argparse.Namespace) -> bool:
    """
    Check if PM should run in standalone mode (no Director).

    Returns True if:
    1. Director type is explicitly set to 'none'
    2. --run-director is False and no director type is specified
    """
    # Check if explicitly set to none
    director_type = os.getenv("KERNELONE_DIRECTOR_TYPE", "")
    if director_type == "none":
        return True

    if getattr(args, "director_type", None) == "none":
        return True

    # Check if --run-director is disabled
    return bool(not getattr(args, "run_director", True))


def get_director_type(args: argparse.Namespace) -> str:
    """
    Get the effective director type based on args and environment.

    Priority:
    1. Environment variable KERNELONE_DIRECTOR_TYPE
    2. CLI argument --director-type
    3. Default: 'auto'
    """
    director_type = os.getenv("KERNELONE_DIRECTOR_TYPE", "")
    if director_type:
        token = str(director_type).strip().lower()
        if token in {"auto", "script", "none"}:
            return token
        return "auto"

    director_type_raw = getattr(args, "director_type", None)
    if director_type_raw:
        token = str(director_type_raw).strip().lower()
        if token in {"auto", "script", "none"}:
            return token
        return "auto"

    return "auto"


__all__ = [
    "DIRECTOR_INTERFACE_AVAILABLE",
    "create_director_for_pm",
    "get_director_type",
    "is_standalone_mode",
    "run_director_via_interface",
    "should_use_director_interface",
]
