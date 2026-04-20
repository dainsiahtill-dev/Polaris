"""Active Run Context Management.

Manages the current active run context for log writing.
Provides thread-local and global context tracking.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from polaris.kernelone.storage import resolve_storage_roots


@dataclass
class ActiveRunContext:
    """Represents the current active run context.

    This tracks the current run_id and associated metadata
    for log writers and other components.
    """

    run_id: str
    workspace: str
    runtime_root: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pm_iteration: int | None = None
    director_iteration: int | None = None
    task_id: str | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        # Resolve paths
        self.workspace = os.path.abspath(self.workspace)
        if self.runtime_root:
            self.runtime_root = os.path.abspath(self.runtime_root)
        else:
            roots = resolve_storage_roots(self.workspace)
            self.runtime_root = os.path.abspath(roots.runtime_root)

    @property
    def run_dir(self) -> str:
        """Get the run directory path."""
        return os.path.join(self.runtime_root, "runs", self.run_id)

    @property
    def logs_dir(self) -> str:
        """Get the logs directory path."""
        return os.path.join(self.run_dir, "logs")


# Thread-local storage for run context
_thread_local = threading.local()


def get_active_run_context() -> ActiveRunContext | None:
    """Get the current active run context (thread-local).

    Returns:
        ActiveRunContext or None if no active run
    """
    return getattr(_thread_local, "run_context", None)


def set_active_run_context(context: ActiveRunContext | None) -> None:
    """Set the current active run context (thread-local).

    Args:
        context: ActiveRunContext or None to clear
    """
    _thread_local.run_context = context


def clear_active_run_context() -> None:
    """Clear the current active run context."""
    _thread_local.run_context = None


# Global context for backward compatibility
_global_context: ActiveRunContext | None = None
_global_lock = threading.Lock()


def get_global_run_context() -> ActiveRunContext | None:
    """Get the global run context.

    Returns:
        ActiveRunContext or None if no active run
    """
    with _global_lock:
        return _global_context


def set_global_run_context(context: ActiveRunContext | None) -> None:
    """Set the global run context.

    Args:
        context: ActiveRunContext or None to clear
    """
    global _global_context
    with _global_lock:
        _global_context = context


class RunContextManager:
    """Context manager for running within a specific run context.

    Usage:
        with RunContextManager(workspace=".", run_id="run-123"):
            # Inside run context
            writer = get_writer(workspace=".")  # Will use run-123
    """

    def __init__(
        self,
        workspace: str,
        run_id: str,
        runtime_root: str | None = None,
        pm_iteration: int | None = None,
        director_iteration: int | None = None,
        task_id: str | None = None,
        use_thread_local: bool = True,
    ) -> None:
        """Initialize the context manager.

        Args:
            workspace: Workspace directory
            run_id: Run identifier
            runtime_root: Optional runtime root
            pm_iteration: PM iteration number
            director_iteration: Director iteration number
            task_id: Current task ID
            use_thread_local: Whether to use thread-local storage
        """
        self.workspace = workspace
        self.run_id = run_id
        self.runtime_root = runtime_root
        self.pm_iteration = pm_iteration
        self.director_iteration = director_iteration
        self.task_id = task_id
        self.use_thread_local = use_thread_local
        self._old_context: ActiveRunContext | None = None

    def __enter__(self) -> ActiveRunContext:
        """Enter the run context."""
        # Save old context
        if self.use_thread_local:
            self._old_context = get_active_run_context()
        else:
            self._old_context = get_global_run_context()

        # Create new context
        context = ActiveRunContext(
            run_id=self.run_id,
            workspace=self.workspace,
            runtime_root=self.runtime_root or "",
            pm_iteration=self.pm_iteration,
            director_iteration=self.director_iteration,
            task_id=self.task_id,
        )

        # Set new context
        if self.use_thread_local:
            set_active_run_context(context)
        else:
            set_global_run_context(context)

        return context

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the run context."""
        # Restore old context
        if self.use_thread_local:
            set_active_run_context(self._old_context)
        else:
            set_global_run_context(self._old_context)


def resolve_current_run_id() -> str:
    """Resolve the current run_id from context.

    Returns:
        Run ID string (empty if no active run)
    """
    # Try thread-local first
    context = get_active_run_context()
    if context:
        return context.run_id

    # Fall back to global
    context = get_global_run_context()
    if context:
        return context.run_id

    # Try to get from latest_run.json
    try:
        from polaris.kernelone.storage.io_paths import resolve_storage_roots

        roots = resolve_storage_roots(os.getcwd())
        latest_file = os.path.join(roots.runtime_root, "latest_run.json")
        if os.path.exists(latest_file):
            import json

            with open(latest_file, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("run_id", "")
    except (RuntimeError, ValueError):
        logger.debug("DEBUG: run_context.py:{211} {exc} (swallowed)")

    return ""


def resolve_current_workspace() -> str:
    """Resolve the current workspace from context.

    Returns:
        Workspace path (empty if no active run)
    """
    context = get_active_run_context()
    if context:
        return context.workspace

    context = get_global_run_context()
    if context:
        return context.workspace

    return os.getcwd()
